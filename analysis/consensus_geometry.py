#!/usr/bin/env python3
"""
consensus_geometry.py — turn per-segment support into a drawable route.

DESTINATION: analysis/consensus_geometry.py

Everything upstream produces junction POINTS with support counts.
Nothing draws the road between them. This is the step that makes a
route line, per route family, with confidence attached to each piece.

How it works:

  1. group traces into route families            (they are different routes)
  2. build a weighted graph per family           (nodes = junctions)
  3. order segments by following the graph       (a route is a walk, not a set)
  4. route each consecutive pair through OSRM    (real road geometry)
  5. attach confidence per segment               (authors, sources, verdict)

Two things it deliberately does not do:

  It does not force a single "best path". The design doc specified
  Dijkstra over -log(support) to extract one route per cluster. On this
  data that would discard the branches — Walkley G2 is one spine with
  two residential appendages, and a shortest path picks one appendage
  and drops the other. The whole subgraph above threshold is the answer.

  It does not silently drop low-confidence segments. They are emitted
  with their support so the renderer can fade or omit them. Deciding
  what is publishable is a display question, not a geometry one.

Usage:
    python3 consensus_geometry.py ../data/out/walkley/reddit_traces.json \\
        ../data/out/walkley/ocr_traces.json ../data/out/walkley/ocr_traces_g.json \\
        --db ../data/osm.db --centre walkley \\
        --out ../data/out/walkley/consensus_routes.geojson
    python3 consensus_geometry.py ... --dry-run     # no OSRM calls
    python3 consensus_geometry.py ... --check-drive-past   # see below

--check-drive-past runs drive_past.py's classifier over the same traces
and warns about any segment in THIS run's output it calls drive-past or
unconfirmed (video-only, no text at that junction). Doesn't drop or alter
anything — a human still decides via corrections.json, same as corr-002.
Off by default: it re-reads every trace file a second time.
"""

import argparse
import datetime as dt
import itertools
import json
import math
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from streetnames import load_known, key, variants as name_variants
from classvote import class_vote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive_past

OSRM = "https://router.project-osrm.org/route/v1/driving"
UA = "OntarioRoadTestMap/0.1 (research; ontariodrivetestmap.fyi)"


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


# Above the largest real sparse-road stretch seen in the data (~383 m),
# so only genuine OSRM beelines trip it. Kept in sync with the frontend's
# GAP_THRESHOLD_M in MapView.jsx (which draws these gaps as dashed).
GAP_WARN_M = 400


def _max_coord_gap(coords):
    """Largest straight jump between consecutive [lon, lat] vertices, in
    metres. Real OSRM road geometry is dense (<~50 m/step); a large value
    means an unrouted beeline leg slipped into the geometry."""
    biggest = 0.0
    for i in range(1, len(coords)):
        p, q = coords[i - 1], coords[i]
        biggest = max(biggest, haversine((p[1], p[0]), (q[1], q[0])))
    return biggest


class Graph:
    def __init__(self, db):
        self.con = sqlite3.connect(db)
        self.con.row_factory = sqlite3.Row
        self._j = {}

    def junction_candidates(self, a, b):
        """Every real junction node where streets a and b meet, no
        disambiguation applied -- the one query both junction() (first
        real node, node before ramp) and manual_routes.py's
        best_junction() (nearest to current position, capped) build on,
        so a fix to the join itself (ramp handling, base/full matching)
        only has to happen once. Previously manual_routes.py hand-copied
        this whole query as its own function; the two could silently
        diverge if one got fixed and not the other (found in review)."""
        va, vb = name_variants(a), name_variants(b)
        pa = ",".join("?" * len(va))
        pb = ",".join("?" * len(vb))
        return self.con.execute(f"""
            SELECT j.node_id, j.lat, j.lon, j.kind FROM junctions j
            WHERE j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pa}) OR full IN ({pa}))
              AND j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pb}) OR full IN ({pb}))
            ORDER BY CASE j.kind WHEN 'node' THEN 0 ELSE 1 END
        """, (*va, *va, *vb, *vb)).fetchall()

    def junction(self, a, b):
        k = tuple(sorted((key(a), key(b))))
        if k in self._j:
            return self._j[k]
        rows = self.junction_candidates(a, b)
        self._j[k] = (dict(rows[0]) if rows else None)
        return self._j[k]

    def centre(self, cid):
        r = self.con.execute(
            "SELECT lat, lon, name FROM centres LIMIT 1").fetchone()
        return dict(r) if r else None

    def nearby_traffic_control(self, lat, lon, radius_m=20):
        """Real stop sign / traffic signal within radius_m of a point,
        or None. Table populated by geometry/extract_traffic_data.py --
        absent entirely until that script has been run once."""
        try:
            deg = radius_m / 111_000
            rows = self.con.execute(
                "SELECT lat, lon, kind FROM traffic_control "
                "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (lat - deg, lat + deg, lon - deg, lon + deg),
            ).fetchall()
        except sqlite3.OperationalError:
            return None  # table doesn't exist yet -- not run, not a crash
        best = None
        for r in rows:
            d = haversine((lat, lon), (r["lat"], r["lon"]))
            if d <= radius_m and (best is None or d < best[0]):
                best = (d, r["kind"])
        return best[1] if best else None

    def street_maxspeed(self, name, lat=None, lon=None):
        """Real posted speed limit for a named street, or None -- most
        streets in this extract simply aren't tagged (~4.7% of ways
        checked directly against the Ottawa extract), so None is the
        common, honest case, not a bug.

        A long arterial can genuinely have more than one real speed zone
        (Walkley Road: 50 near the DriveTest centre, 80 toward the
        airport) -- picking whichever tagged way SQLite returns first
        for the name (the old behaviour, no `lat`/`lon`) can attach the
        WRONG zone to a given point. Confirmed live: the owner saw 80
        shown for a maneuver actually in the 50 zone. When a location is
        given and way_maxspeed_loc has coordinates for the matching ways
        (see backfill_maxspeed_location.py), the nearest one by real
        distance wins instead of an arbitrary row order.

        osm.db is province-wide (see MAX_JUMP_M elsewhere in this file
        for the same class of bug in junction lookup): a common street
        base name like "Davidson" matches completely unrelated real
        streets near Ottawa AND near Smiths Falls, 50+ km apart. Without
        a cap, "nearest of the candidates" still returns the closest
        WRONG street when the real one just isn't tagged at all --
        confirmed directly: Smiths Falls' real Davidson Street West has
        no maxspeed tag, every candidate SQLite found was a different
        Davidson elsewhere in the province, yet the nearest-picks-best
        logic returned one anyway as if it were correct."""
        MAX_SPEED_MATCH_M = 3000
        if not name:
            return None

        # Own try/except: if way_maxspeed_loc doesn't exist (it's built
        # by a separate one-off script, backfill_maxspeed_location.py,
        # not part of the normal osm.db build pipeline) this must NOT
        # take down the name-only fallback below with it -- a bug found
        # in review: the two used to share one try/except, so a missing
        # table silently zeroed out speed_limit data for every street,
        # every route, every caller, not just the new location feature.
        if lat is not None and lon is not None:
            try:
                best, best_d = None, None
                for v in name_variants(name):
                    rows = self.con.execute(
                        "SELECT wm.maxspeed, l.lat, l.lon FROM streets s "
                        "JOIN way_maxspeed wm ON wm.way_id = s.way_id "
                        "JOIN way_maxspeed_loc l ON l.way_id = wm.way_id "
                        "WHERE s.base = ? OR s.full = ?", (v, v),
                    ).fetchall()
                    for r in rows:
                        d = haversine((lat, lon), (r["lat"], r["lon"]))
                        if best_d is None or d < best_d:
                            best, best_d = r["maxspeed"], d
                if best is not None:
                    # a located candidate exists for this name -- either
                    # it's close enough to be the real match, or it's a
                    # same-named street far away, in which case guessing
                    # via the unlocated name-only fallback below would be
                    # no better. Either way, don't fall through.
                    return best if best_d <= MAX_SPEED_MATCH_M else None
            except sqlite3.OperationalError:
                pass  # way_maxspeed_loc missing -- fall through below,
                      # same as "no located candidate found for this name"

        try:
            for v in name_variants(name):
                r = self.con.execute(
                    "SELECT wm.maxspeed FROM streets s "
                    "JOIN way_maxspeed wm ON wm.way_id = s.way_id "
                    "WHERE s.base = ? OR s.full = ? LIMIT 1", (v, v),
                ).fetchone()
                if r:
                    return r["maxspeed"]
        except sqlite3.OperationalError:
            return None
        return None


def age_weight(observed, hl=5.0):
    # exponential decay, not a cutoff — an old trace still counts, just less
    if not observed:
        return 0.6
    try:
        d = dt.date.fromisoformat(observed[:10])
    except ValueError:
        return 0.6
    return max(0.15, 0.5 ** (((dt.date.today() - d).days / 365.25) / hl))


def source_weight(t):
    # video weighs more because it can't skip a street the way recall can,
    # not because it's video
    if t["source_id"].startswith("youtube"):
        return 0.9
    n = len(t.get("turns", []))
    return 0.6 if n >= 6 else (0.45 if n >= 3 else 0.25)


def trace_segments(t, g):
    """Ordered turns -> graph-valid street-pair segments."""
    st = [x["street"] for x in t.get("turns", [])]
    out = []
    for a, b in zip(st, st[1:]):
        if a == b or a.startswith("@") or b.startswith("@"):
            continue
        if g.junction(a, b):
            out.append(tuple(sorted((key(a), key(b)))))
    return out


def families(T, g, min_size=2):
    """Split traces into route families.

    Necessary, not optional: leave-one-out on 2026-09-04 measured F1 0.79
    pooled against 0.86 per family. A consensus built across two different
    routes describes neither.
    """
    import numpy as np
    sets = [set(trace_segments(t, g)) for t in T]
    df = defaultdict(int)
    for e in sets:
        for k in e:
            df[k] += 1
    n = len(T)
    w = {k: math.log(n / c) + 1e-6 for k, c in df.items()}

    D = np.zeros((n, n))
    for i, j in itertools.combinations(range(n), 2):
        inter = sets[i] & sets[j]
        if inter:
            num = sum(w[k] for k in inter)
            den = min(sum(w[k] for k in sets[i]), sum(w[k] for k in sets[j]))
            s = num / den if den else 0.0
        else:
            s = 0.0
        D[i][j] = D[j][i] = 1.0 - s

    labels = None
    try:
        from sklearn.cluster import HDBSCAN
        labels = HDBSCAN(metric="precomputed", min_cluster_size=min_size,
                         min_samples=1).fit_predict(D)
        if all(l == -1 for l in labels):
            labels = None
    except Exception:
        pass
    if labels is None:
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform
        Z = linkage(squareform(D, checks=False), method="average")
        labels = fcluster(Z, t=0.6, criterion="distance") - 1

    out = defaultdict(list)
    for t, l in zip(T, labels):
        out[int(l)].append(t)
    return out


def class_families(T, g, min_size=2, assign_overlap=0.34):
    """Cluster WITHIN test_class, so no family ever mixes G and G2.

    The plain families() above clusters purely on shared streets and is
    class-blind: a G route and a G2 route that share the same arterials
    near the centre get merged into one family, which then renders under
    both class filters and reads as "G and G2 are the same route." A
    driving test is one class; a family must be too.

    Only confirmed G / G2 traces seed families. "ambiguous" (matched both
    class keyword patterns) and "unknown" traces don't get a class vote --
    that is exactly what glued the classes together before -- so each is
    instead attached to the single best-matching class-family by segment
    overlap (>= assign_overlap of the trace's own segments), enriching its
    support without changing its class, or dropped if it matches nothing.

    Returns {(class, local_id): [traces]} -- family numbering restarts per
    class, and (centre, class, family) is the real route key downstream.
    """
    labeled = defaultdict(list)
    unlabeled = []
    for t in T:
        c = t.get("test_class")
        if c in ("G", "G2"):
            labeled[c].append(t)
        else:
            unlabeled.append(t)

    fams = {}
    for c, sub in labeled.items():
        for lid, traces in families(sub, g, min_size).items():
            if lid < 0:
                continue  # HDBSCAN noise, not a route
            fams[(c, lid)] = list(traces)

    fam_segs = {k: set().union(*[set(trace_segments(t, g)) for t in v]) if v else set()
                for k, v in fams.items()}
    for t in unlabeled:
        ts = set(trace_segments(t, g))
        if not ts:
            continue
        best, best_score = None, 0.0
        for k, segs in fam_segs.items():
            if not segs:
                continue
            score = len(ts & segs) / len(ts)
            if score > best_score:
                best, best_score = k, score
        if best is not None and best_score >= assign_overlap:
            fams[best].append(t)
    return fams


def support(traces, g):
    """Segment -> evidence. Authors deduplicated per source kind."""
    seg = defaultdict(lambda: {"video": set(), "text": set(), "w": {},
                               "last": "", "node": None})
    for t in traces:
        who = t.get("author_hash") or t["source_id"]
        kind = "video" if t["source_id"].startswith("youtube") else "text"
        sw = source_weight(t) * age_weight(t.get("observed_at"))
        st = [x["street"] for x in t.get("turns", [])]
        for a, b in zip(st, st[1:]):
            if a == b or a.startswith("@") or b.startswith("@"):
                continue
            j = g.junction(a, b)
            if not j:
                continue
            k = tuple(sorted((key(a), key(b))))
            s = seg[k]
            s[kind].add(who)
            s["w"][who] = max(s["w"].get(who, 0), sw)
            s["node"] = j
            obs = (t.get("observed_at") or "")[:10]
            if obs > s["last"]:
                s["last"] = obs
    return seg


def order_walk(seg, keep):
    """Put segments into travel order by walking the street graph.

    A route is a walk, not a set. Two segments sharing a street are
    adjacent; following those adjacencies recovers the sequence. Where
    the graph branches — one spine, several appendages — each branch is
    emitted as its own run rather than being forced into one line.
    """
    adj = defaultdict(list)
    for k in keep:
        a, b = k
        adj[a].append((b, k))
        adj[b].append((a, k))

    def walk_from(cur):
        out = []
        while True:
            nxt = None
            for other, k in adj[cur]:
                if k in unused:
                    nxt = (other, k)
                    break
            if not nxt:
                return out
            other, k = nxt
            unused.discard(k)
            out.append(k)
            cur = other

    unused = set(keep)
    runs = []
    while unused:
        # start at the most-connected unused street, so the spine leads.
        # sorted() before max(): plain set iteration order depends on
        # Python's per-process hash randomization, so ties (every leaf
        # street is degree 1) broke to a different street each run, and
        # a different start split the same edges into a different set
        # of runs. Sorting makes the street name itself the tiebreak.
        start = max(sorted({s for k in unused for s in k}),
                    key=lambda s: len([1 for _, k in adj[s] if k in unused]))
        run = walk_from(start)
        # A plain waypoint (exactly 2 streets ever meet here, i.e. a
        # pass-through, not a real branch) walked from its middle only
        # extends one way and stops -- the other half of the SAME
        # straight path then looked like a second, disconnected run
        # purely because of where the walk happened to start. Found on
        # a real 2-3 segment case: build_route_feature() needs >=2
        # points to draw a line, so the orphaned single-edge remainder
        # silently vanished instead of joining its other half. A real
        # branch (3+ streets meeting at one point) must still split into
        # separate runs -- only continue backward through an actual
        # pass-through point, never through a genuine junction.
        if len(adj[start]) == 2:
            back = walk_from(start)
            if back:
                run = list(reversed(back)) + run
        if run:
            runs.append(run)
    return runs


def build_route_feature(seg, run, ri, fid, traces, authors, test_class,
                        mixed_classes, args, below_threshold=False, g=None):
    """One run (ordered list of segment keys, all from the SAME family)
    -> one GeoJSON Feature, road-snapped via OSRM. Shared by the main
    per-family loop and recover_low_confidence_routes.py, which walks
    the same family's below-threshold segments separately -- one
    implementation of "segments -> drawable route", not two, same
    reasoning as common/classvote.py. Returns None for a run too short
    to draw (a single segment is one point, not a line)."""
    pts, props = [], []
    for k in run:
        v = seg[k]
        n = v["node"]
        pts.append({"lat": n["lat"], "lon": n["lon"]})
        props.append({
            "streets": list(k),
            "authors": len(v["video"] | v["text"]),
            "video": len(v["video"]), "text": len(v["text"]),
            "weight": round(sum(v["w"].values()), 2),
            "junction": n["kind"], "last_seen": v["last"],
        })
    if len(pts) < 2:
        return None

    geom, dist, steps = None, None, []
    if not args.dry_run:
        try:
            r = osrm_route(pts, args.pause)
            if r.get("code") == "Ok":
                geom = r["routes"][0]["geometry"]
                dist = r["routes"][0]["distance"]
                steps = extract_steps(r, g)
            else:
                print(f"     run {ri}: OSRM {r.get('code')}")
        except Exception as e:
            print(f"     run {ri}: {str(e)[:80]}")
    if geom is None:
        geom = {"type": "LineString",
                "coordinates": [[p["lon"], p["lat"]] for p in pts]}

    # OSRM answers "Ok" even when a leg is unroutable -- it returns a
    # straight beeline (2 points, distance == straight-line distance) for
    # that stretch. Left as-is that fake straight is indistinguishable
    # from real road inside a *confirmed* route (found on Walkley's airport
    # routes: a 778 m jump straight across greenspace). Detect the largest
    # such gap and record it so it isn't silently presented as driven road
    # -- the frontend renders any gap over GAP_THRESHOLD as a dashed
    # "unrouted" segment rather than solid confirmed geometry.
    gap_m = _max_coord_gap(geom.get("coordinates", []))
    if gap_m > GAP_WARN_M:
        print(f"     ! run {ri}: {gap_m:.0f}m unrouted gap in geometry "
              f"(OSRM beelined an unroutable junction pair) -- flagged, not "
              f"drawn as confirmed road")

    return {
        "type": "Feature",
        "geometry": geom,
        "_steps": steps,
        "properties": {
            "family": fid, "run": ri,
            "traces": len(traces), "authors": authors,
            "test_class": test_class, "mixed_classes": mixed_classes,
            "below_threshold": below_threshold,
            "distance_m": round(dist) if dist else None,
            "gap_m": round(gap_m) if gap_m > GAP_WARN_M else None,
            "segments": props,
            "min_authors": min(p["authors"] for p in props),
            "max_authors": max(p["authors"] for p in props),
        },
    }


def osrm_route(points, pause=1.0):
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    q = urllib.parse.urlencode({"overview": "full", "geometries": "geojson",
                                "annotations": "nodes", "steps": "true"})
    req = urllib.request.Request(f"{OSRM}/{coords}?{q}",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read().decode())
    time.sleep(pause)
    return d


_MODIFIER_TEXT = {
    "uturn": "make a U-turn", "sharp right": "turn sharp right",
    "right": "turn right", "slight right": "turn slightly right",
    "straight": "continue straight", "slight left": "turn slightly left",
    "left": "turn left", "sharp left": "turn sharp left",
}


def step_instruction(step):
    """One OSRM step -> a plain-English instruction, in the same style
    as a normal turn-by-turn app. Not a full reimplementation of OSRM's
    own text-instructions library -- just enough of its cases to read
    naturally for the maneuver types this pipeline actually produces
    (depart/turn/new name/arrive/roundabout), all built from the SAME
    real routing response already fetched for the line's geometry --
    nothing here is invented, it's OSRM's own account of a real path
    between real points, reformatted."""
    m = step.get("maneuver", {})
    name = step.get("name") or "the road"
    mtype = m.get("type", "")
    modifier = m.get("modifier", "")

    if mtype == "depart":
        return f"Head onto {name}" if name != "the road" else "Head out"
    if mtype == "arrive":
        return "Arrive at destination"
    if mtype in ("roundabout", "rotary"):
        exit_n = m.get("exit", 1)
        return f"Enter the roundabout and take exit {exit_n} onto {name}"
    if mtype == "new name":
        return f"Continue onto {name}"
    if mtype == "turn" or mtype == "end of road":
        verb = _MODIFIER_TEXT.get(modifier, "continue")
        return f"{verb.capitalize()} onto {name}"
    if mtype == "merge":
        return f"Merge onto {name}"
    if mtype == "fork":
        verb = _MODIFIER_TEXT.get(modifier, "continue")
        return f"At the fork, {verb} onto {name}"
    return f"Continue onto {name}"


def insert_steps(cur, route_line_id, steps):
    """Shared by every script that inserts into route_lines and has
    step data to go with it -- one insert loop, not four copies."""
    for i, s in enumerate(steps):
        cur.execute(
            """
            INSERT INTO route_line_steps
                (route_line_id, step_order, instruction, distance_m, duration_s,
                 traffic_control, speed_limit)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (route_line_id, i, s["instruction"], s["distance_m"], s["duration_s"],
             s.get("traffic_control"), s.get("speed_limit")),
        )


def extract_steps(osrm_response, g=None):
    """OSRM's own leg/step breakdown for a route already fetched with
    steps=true -> a flat, ordered list of {instruction, distance_m,
    duration_s}. Returns [] if the response has no step data (e.g. a
    dry run's synthetic straight-line geometry, which never calls
    OSRM).

    Pass g (a Graph, see extract_traffic_data.py for what populates its
    tables) to enrich each instruction with a real stop sign / traffic
    signal at that maneuver's location and the real posted speed limit
    for that street, when the OSM extract actually has that tag --
    silently omitted otherwise, never guessed.

    Two OSRM behaviours get corrected here, not just formatted:

    1. OSRM creates one "leg" PER WAYPOINT PAIR in the request, and
       every leg gets its own depart/arrive pair -- even though our
       waypoints are real junctions along ONE continuous route, not
       separate trips. Passed through raw, a 4-junction route becomes
       3 fake "arrive at destination" / "head onto X" pairs instead of
       one real trip with 2 real turns. Fixed by only keeping the
       FIRST leg's depart and the LAST leg's arrive; every other leg's
       "arrive" is dropped (it's not a real destination) and its
       "depart" becomes a real turn-onto-the-next-road instruction.

    2. OSRM emits a new step at every minor road-geometry change, not
       just at meaningful turns -- a single street with a few gentle
       bends produces a dozen "new name" steps for what a human
       describes as one turn onto that street. Consecutive steps that
       stay on the same named road get merged, distances/durations
       summed -- genuine turns, forks, roundabouts, and the real
       depart/arrive always stay their own row.
    """
    legs = osrm_response.get("routes", [{}])[0].get("legs", [])
    n = len(legs)
    flat = []
    for li, leg in enumerate(legs):
        for step in leg.get("steps", []):
            m = step.get("maneuver", {})
            mtype = m.get("type", "")
            if mtype == "arrive" and li < n - 1:
                continue  # not a real destination -- the next leg's depart covers this junction
            if mtype == "depart" and li > 0:
                # a mid-route junction, not the real start -- describe it
                # as the turn it actually is, not a fresh "head onto"
                name = step.get("name") or "the road"
                loc = m.get("location") or [None, None]
                flat.append({
                    "instruction": f"Continue onto {name}",
                    "distance_m": round(step.get("distance", 0)),
                    "duration_s": round(step.get("duration", 0)),
                    "_name": name,
                    "_lon": loc[0], "_lat": loc[1],
                })
                continue
            loc = m.get("location") or [None, None]
            flat.append({
                "instruction": step_instruction(step),
                "distance_m": round(step.get("distance", 0)),
                "duration_s": round(step.get("duration", 0)),
                "_name": step.get("name") or "",
                "_lon": loc[0], "_lat": loc[1],
            })

    out, current = [], None
    for row in flat:
        is_continuation = (
            current is not None
            and row["_name"] == current["_name"]
            and row["instruction"].startswith(("Continue", "Turn slightly"))
        )
        if is_continuation:
            current["distance_m"] += row["distance_m"]
            current["duration_s"] += row["duration_s"]
            continue
        if current is not None:
            out.append(current)
        current = row
    if current is not None:
        out.append(current)

    for row in out:
        if g is not None and row["_lat"] is not None:
            row["traffic_control"] = g.nearby_traffic_control(row["_lat"], row["_lon"])
            row["speed_limit"] = g.street_maxspeed(row["_name"], row["_lat"], row["_lon"])
        else:
            row["traffic_control"] = None
            row["speed_limit"] = None
        del row["_name"], row["_lat"], row["_lon"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--centre", default="walkley")
    ap.add_argument("--out", default="../data/out/walkley/consensus_routes.geojson")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="minimum summed weight; 0.5 is where leave-one-out "
                         "peaked at F1 0.79, not the design doc's 1.5")
    ap.add_argument("--min-family", type=int, default=3)
    ap.add_argument("--corrections", default="../corrections/corrections.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--check-drive-past", action="store_true",
                    help="warn about published segments that drive_past.py "
                         "classifies as drive-past or unconfirmed; does not "
                         "remove anything, see module docstring")
    args = ap.parse_args()

    load_known(args.db)
    g = Graph(args.db)

    T = []
    for p in args.traces:
        if not os.path.exists(p):
            print(f"  ! missing {p}")
            continue
        for t in json.load(open(p)):
            if args.centre and args.centre not in str(t.get("centre_id", "")):
                continue
            if len(trace_segments(t, g)) >= 2:
                T.append(t)
    print(f"{len(T)} traces\n")

    # segment-level corrections override the data. A drive-past confirmed
    # from a dashcam frame is better evidence than fifteen video mentions.
    overrides = {}
    if os.path.exists(args.corrections):
        for c in json.load(open(args.corrections)).get("corrections", []):
            if c.get("kind") != "segment" or c.get("status") != "confirmed":
                continue
            for s in c.get("affects", []):
                parts = [x.strip() for x in s.split("x")]
                if len(parts) == 2:
                    overrides[tuple(sorted(parts))] = c["id"]
        if overrides:
            print(f"{len(overrides)} segment correction(s) applied: "
                  f"{', '.join('×'.join(k) for k in overrides)}\n")

    # Cluster within class so no family mixes G and G2 (see class_families).
    fams = class_families(T, g, min_size=2)
    print(f"{len(fams)} class-coherent route families\n")

    feats = []
    published = set()
    for (cls, fid) in sorted(fams, key=lambda k: (k[0], k[1])):
        traces = fams[(cls, fid)]
        seg = support(traces, g)
        authors = len({t.get("author_hash") or t["source_id"] for t in traces})

        # Class is fixed by the family's confirmed members now, never voted
        # across mixed classes -- so mixed_classes is structurally False.
        test_class, mixed_classes = cls, False

        # Consensus thresholding filters segments where sources DISAGREE.
        # That only makes sense with enough sources to disagree: a family
        # with fewer than --min-family traces has no consensus to take, so
        # its route is that evidence's own path (every graph-valid segment
        # it named). Bigger families still get the tuned threshold.
        thin = len(traces) < args.min_family
        keep, dropped, corrected = [], 0, 0
        for k, v in seg.items():
            if k in overrides:
                corrected += 1
                continue
            if not thin and sum(v["w"].values()) < args.threshold:
                dropped += 1
                continue
            keep.append(k)
        published.update(keep)

        runs = order_walk(seg, keep)
        streets = sorted({s for k in keep for s in k})
        tag = " (thin: full path, no consensus filter)" if thin else ""
        print(f"── {cls} family {fid}: {len(traces)} traces, {authors} authors, "
              f"{len(keep)} segments, {len(runs)} run(s){tag}")
        print(f"     {', '.join(streets[:9])}")
        if corrected:
            print(f"     {corrected} segment(s) removed by correction")
        if dropped:
            print(f"     {dropped} below threshold {args.threshold}")

        for ri, run in enumerate(runs):
            feat = build_route_feature(seg, run, ri, fid, traces, authors,
                                       test_class, mixed_classes, args,
                                       below_threshold=False, g=g)
            if feat:
                feats.append(feat)
        print()

    if args.check_drive_past:
        dpg = drive_past.Graph(args.db)
        dseg, _ = drive_past.gather(args.traces, dpg, args.centre)
        verdicts = drive_past.classify(dseg)
        flagged = {k: v for k, v in verdicts.items()
                   if k in published and v["verdict"] in
                   ("drive-past", "unconfirmed")}
        if flagged:
            print(f"drive-past check: {len(flagged)} published segment(s) "
                  f"flagged\n")
            for k, v in flagged.items():
                print(f"  ! {k[0]} × {k[1]}  [{v['verdict']}]  {v['why']}")
            print("\n  Not removed from output. Review against "
                  "corrections/corrections.json and add a 'segment' "
                  "correction if this is a real drive-past.\n")
        else:
            print("drive-past check: no published segment flagged\n")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump({"type": "FeatureCollection",
               "properties": {"centre": args.centre,
                              "built": dt.date.today().isoformat(),
                              "threshold": args.threshold,
                              "traces": len(T)},
               "features": feats}, open(args.out, "w"), indent=1)
    print(f"wrote {args.out}  ({len(feats)} route lines)")
    if args.dry_run:
        print("  dry run — straight lines between junctions, no OSRM")
    return 0


if __name__ == "__main__":
    sys.exit(main())