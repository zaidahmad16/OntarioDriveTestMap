#!/usr/bin/env python3
"""
snap_traces.py — turn ordered street names into map geometry.

Input:  traces in intermediate form (ordered turns naming streets)
        osm.db, the offline intersection index
Output: one polyline per trace, plus the OSM node sequence along it

Pipeline position:
    parse_md -> process_reddit -> [THIS] -> cluster -> consensus

What it does not do: cluster, build consensus, score confidence, or draw
anything. It converts words to lines. Everything downstream needs lines.

Usage:
    python3 snap_traces.py traces.json --db ../data/osm.db --out snapped.json
    python3 snap_traces.py traces.json --db ../data/osm.db --dry-run
    python3 snap_traces.py traces.json --db ../data/osm.db --geojson out.geojson
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

OSRM_DEFAULT = "https://router.project-osrm.org/route/v1/driving"
UA = "OntarioRoadTestMap/0.1 (research; ontariodrivetestmap.fyi)"

# ---------------------------------------------------------------------------
# DECISION 1 — divided carriageways.
#
# "cedarwood x walkley" returns two nodes 10 m apart, because Walkley has
# separate carriageways. Picking wrongly can route into the wrong direction
# of travel or insert a U-turn, which would then appear in the polyline as
# a real manoeuvre and could propagate into consensus as a corroborated turn.
#
# Default here: pick the candidate nearest the previous resolved waypoint.
# Cheap, no extra requests, handles the ordinary case. Alternatives are
# --carriageway first (arbitrary, current behaviour elsewhere) and
# --carriageway all, which hands every candidate to OSRM and takes the
# cheapest route. Marked as a default, not a settled decision.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DECISION 2 — trace representation.
#
# The design doc says a trace is a sequence of OSM way IDs. OSRM annotates
# routes with NODE ids and has no way-id annotation.
#
# Default here: store both. Node sequence for clustering, because it is
# finer-grained and a partly-traversed road counts partly. Way ids for
# display and for the route_segments schema, mapped from nodes through the
# index. Costs one extra column and keeps both options open.
# ---------------------------------------------------------------------------


def haversine(a, b):
    """Rough metres between two (lat, lon). Good enough for picking a node."""
    from math import radians, sin, cos, asin, sqrt
    la1, lo1, la2, lo2 = map(radians, [a[0], a[1], b[0], b[1]])
    h = sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2
    return 2 * 6371000 * asin(sqrt(h))


class Index:
    """Read-only view over osm.db."""

    def __init__(self, path):
        if not os.path.exists(path):
            raise SystemExit(f"no index at {path}. Build it first with "
                             f"build_intersection_index.py")
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row

    def variants(self, name):
        import re
        SUF = {"road","rd","street","st","avenue","ave","drive","dr",
               "boulevard","blvd","parkway","pkwy","crescent","cres","court",
               "crt","ct","lane","ln","way","place","pl","terrace","terr",
               "circle","cir","trail","private","north","south","east","west"}
        s = re.sub(r"[^\w\s]", " ", (name or "").lower())
        w = [x for x in s.split() if x]
        full = " ".join(w)
        base = list(w)
        while base and base[-1] in SUF:
            base.pop()
        return list({full, " ".join(base)} - {""})

    def junctions(self, a, b):
        va, vb = self.variants(a), self.variants(b)
        pa = ",".join("?" * len(va))
        pb = ",".join("?" * len(vb))
        return self.con.execute(f"""
            SELECT j.node_id, j.lat, j.lon
            FROM junctions j
            WHERE j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pa}) OR full IN ({pa}))
              AND j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pb}) OR full IN ({pb}))
        """, (*va, *va, *vb, *vb)).fetchall()

    def midpoints(self, street, exclude):
        """Junctions on `street` that are not the given end nodes.

        Two waypoints at opposite ends of a street do not make OSRM drive
        along it — OSRM takes the cheapest path between them, which for a
        crescent is the straight road it hangs off. A waypoint partway
        along forces the traverse.
        """
        v = self.variants(street)
        p = ",".join("?" * len(v))
        rows = self.con.execute(f"""
            SELECT j.node_id, j.lat, j.lon FROM junctions j
            WHERE j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({p}) OR full IN ({p}))
        """, (*v, *v)).fetchall()
        return [r for r in rows if r["node_id"] not in exclude]

    # Every route starts and ends at the test centre, and the centre is
    # the one location that is not a street junction. Without it a route
    # stops at its last real intersection instead of closing the loop.
    CENTRE_WORDS = ("@centre", "test centre", "test center", "testcentre",
                    "drivetest", "drive test", "the centre", "the center",
                    "test site")

    def centre(self, name, centre_id=None):
        n = (name or "").lower().strip()
        if not any(w in n for w in self.CENTRE_WORDS):
            return None
        rows = self.con.execute(
            "SELECT centre_id, name, lat, lon FROM centres").fetchall()
        if not rows:
            return None
        if centre_id:
            for r in rows:
                if centre_id.lower() in (r["centre_id"] or "").lower():
                    return r
        return rows[0]

    def known(self, name):
        v = self.variants(name)
        p = ",".join("?" * len(v))
        r = self.con.execute(
            f"SELECT 1 FROM streets WHERE base IN ({p}) OR full IN ({p}) LIMIT 1",
            (*v, *v)).fetchone()
        return r is not None

    def ways_for_nodes(self, node_ids):
        """Node ids -> the way ids they belong to, order preserved."""
        if not node_ids:
            return []
        out, seen = [], set()
        CH = 900
        for i in range(0, len(node_ids), CH):
            chunk = node_ids[i:i+CH]
            p = ",".join("?" * len(chunk))
            for r in self.con.execute(
                f"SELECT DISTINCT way_id FROM junction_streets "
                f"WHERE node_id IN ({p})", chunk):
                if r["way_id"] not in seen:
                    seen.add(r["way_id"])
                    out.append(r["way_id"])
        return out


def resolve(trace, idx, policy="nearest"):
    """Ordered turns -> waypoints, with a reason for every failure."""
    streets = [t["street"] for t in trace["turns"]]
    pairs = list(zip(streets, streets[1:]))

    points, misses, prev, prev_node = [], [], None, None
    for a, b in pairs:
        if a == b:                      # same street twice in a row
            continue
        # A pair naming the centre resolves to the centre point, not to a
        # junction. Handles both "left into test centre" at the end and
        # the implicit start.
        ca, cb = idx.centre(a), idx.centre(b)
        if ca or cb:
            c = ca or cb
            points.append({"node_id": f"centre:{c['centre_id']}",
                           "lat": c["lat"], "lon": c["lon"],
                           "pair": [a, b], "candidates": 1})
            prev = (c["lat"], c["lon"])
            prev_node = None
            continue

        rows = idx.junctions(a, b)
        if not rows:
            # Distinguish the three reasons a lookup can fail. They are
            # different problems and want different fixes.
            if not idx.known(a):
                why = f"unknown street: {a}"
            elif not idx.known(b):
                why = f"unknown street: {b}"
            else:
                why = "streets exist but do not intersect"
            misses.append({"pair": [a, b], "reason": why})
            continue

        # Never reuse the immediately previous waypoint. A route that
        # enters a street and comes back out to the same street is
        # traversing it, and the two junctions are different nodes: both
        # ends of a crescent, or opposite carriageways of a divided road.
        # Nearest-to-previous would pick the previous node itself at
        # distance zero, and the street would never be driven.
        cand = [x for x in rows if x["node_id"] != prev_node] or rows

        if len(cand) == 1 or policy == "first" or prev is None:
            r = cand[0]
        else:
            r = min(cand, key=lambda x: haversine(prev, (x["lat"], x["lon"])))

        pt = {"node_id": r["node_id"], "lat": r["lat"], "lon": r["lon"],
              "pair": [a, b], "candidates": len(rows)}
        points.append(pt)
        prev = (r["lat"], r["lon"])
        prev_node = r["node_id"]

        # If the previous pair ended on this same street, the route is
        # traversing it. Insert a midpoint so OSRM follows the street
        # rather than taking the shortcut between its two ends.
        if len(points) >= 2:
            prv = points[-2]["pair"]
            # Only a genuine traverse: the route left street P for street
            # S, then came straight back to P. Both junctions are S x P,
            # so the two ends belong to the same street pair. Any other
            # consecutive pair shares a street too — that is just an
            # ordinary turn and needs no midpoint.
            if sorted(prv) == sorted([a, b]) and prv != [a, b]:
                street = b if prv[1] == b else a
                street = list({a, b} & set(prv))[0] if False else (
                    prv[1] if prv[1] in (a, b) and prv[1] != prv[0] else a)
                mids = idx.midpoints(street,
                                     {points[-2]["node_id"], r["node_id"]})
                if mids:
                    # Pick the candidate furthest from BOTH ends, not the
                    # nearest to the entry. A midpoint near one end can be
                    # reached cheaply from the other end, and OSRM will do
                    # exactly that, producing an out-and-back instead of a
                    # traverse. Maximising the smaller of the two
                    # distances puts the waypoint where no shortcut wins.
                    e1 = (points[-2]["lat"], points[-2]["lon"])
                    e2 = (r["lat"], r["lon"])
                    m = max(mids, key=lambda x: min(
                        haversine(e1, (x["lat"], x["lon"])),
                        haversine(e2, (x["lat"], x["lon"]))))
                    points.insert(-1, {"node_id": m["node_id"],
                                       "lat": m["lat"], "lon": m["lon"],
                                       "pair": [street, "(traverse)"],
                                       "candidates": len(mids)})

    return points, misses


def route(points, osrm, pause=1.0):
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    # No continue_straight=false. That permits a U-turn at every waypoint,
    # which lets OSRM satisfy a mid-street waypoint by driving out to it
    # and turning around instead of driving through. A road test is not
    # asked to U-turn, so forbidding them matches reality and forces the
    # traverse.
    q = urllib.parse.urlencode({"overview": "full", "geometries": "geojson",
                                "annotations": "nodes"})
    req = urllib.request.Request(f"{osrm}/{coords}?{q}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode())
    time.sleep(pause)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", help="intermediate-form traces json")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--out", default="snapped.json")
    ap.add_argument("--geojson")
    ap.add_argument("--osrm", default=OSRM_DEFAULT)
    ap.add_argument("--carriageway", choices=["nearest", "first"],
                    default="nearest", help="which node when a junction has several")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve intersections only, no routing calls")
    ap.add_argument("--min-points", type=int, default=2)
    ap.add_argument("--route-partials", action="store_true",
                    help="also route traces with unresolved junctions "
                         "(produces interpolated geometry — off by default)")
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args()

    idx = Index(args.db)
    traces = json.load(open(args.traces))
    print(f"{len(traces)} traces in\n")

    snapped, reasons = [], Counter()
    full = partial = failed = 0

    for t in traces:
        pts, misses = resolve(t, idx, args.carriageway)
        for m in misses:
            reasons[m["reason"].split(":")[0]] += 1

        sid = t.get("source_id", "?")
        n_pairs = len(pts) + len(misses)
        if not misses and pts:
            full += 1
            status = "full"
        elif pts:
            partial += 1
            status = "partial"
        else:
            failed += 1
            status = "failed"

        print(f"  {status:8s} {sid:34s} {len(pts)}/{n_pairs} junctions"
              + (f"   [{misses[0]['reason']}]" if misses else ""))

        rec = {**t, "status": status, "waypoints": pts, "misses": misses}

        # Only route traces where every junction resolved. OSRM will
        # happily connect a gap-toothed waypoint list, and the result
        # contains segments no source described — invented geometry that
        # looks entirely plausible on a map and would enter consensus
        # with full support. dkswtcc produced a 15 km path this way, by
        # welding two separate routes together across the gaps.
        if (not args.dry_run and len(pts) >= args.min_points
                and (status == "full" or args.route_partials)):
            try:
                r = route(pts, args.osrm, args.pause)
                if r.get("code") == "Ok":
                    leg = r["routes"][0]
                    nodes = []
                    for l in leg.get("legs", []):
                        nodes.extend(l.get("annotation", {}).get("nodes", []))
                    rec["geometry"] = leg["geometry"]
                    rec["distance_m"] = leg["distance"]
                    rec["duration_s"] = leg["duration"]
                    rec["node_seq"] = nodes          # clustering representation
                    rec["way_seq"] = idx.ways_for_nodes(nodes)  # display / schema
                else:
                    rec["route_error"] = r.get("code")
            except Exception as e:
                rec["route_error"] = str(e)

        snapped.append(rec)

    print(f"\n  full {full}   partial {partial}   failed {failed}")
    if reasons:
        print("\n  why junctions failed:")
        for k, v in reasons.most_common():
            print(f"    {k}: {v}")

    routed = [s for s in snapped if "distance_m" in s]
    if routed:
        d = sorted(s["distance_m"] / 1000 for s in routed)
        print(f"\n  routed {len(routed)}   "
              f"median {d[len(d)//2]:.2f} km   min {d[0]:.2f}   max {d[-1]:.2f}")
        amb = sum(1 for s in snapped for p in s["waypoints"]
                  if p["candidates"] > 1)
        print(f"  waypoints with >1 candidate node: {amb} "
              f"(carriageway policy: {args.carriageway})")

    json.dump(snapped, open(args.out, "w"), indent=1)
    print(f"\nwrote {args.out}")

    if args.geojson:
        feats = []
        for s in snapped:
            if "geometry" in s:
                feats.append({"type": "Feature", "geometry": s["geometry"],
                              "properties": {"source_id": s.get("source_id"),
                                             "test_class": s.get("test_class"),
                                             "km": round(s["distance_m"]/1000, 2)}})
        json.dump({"type": "FeatureCollection", "features": feats},
                  open(args.geojson, "w"), indent=1)
        print(f"wrote {args.geojson}  ({len(feats)} lines)")

    return 0


if __name__ == "__main__":
    sys.exit(main())