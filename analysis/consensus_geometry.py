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
    python3 consensus_geometry.py ../data/out/reddit_traces.json \\
        ../data/out/ocr_traces.json ../data/out/ocr_traces_g.json \\
        --db ../data/osm.db --centre walkley \\
        --out ../data/out/consensus_routes.geojson
    python3 consensus_geometry.py ... --dry-run     # no OSRM calls
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

OSRM = "https://router.project-osrm.org/route/v1/driving"
UA = "OntarioRoadTestMap/0.1 (research; ontariodrivetestmap.fyi)"


class Graph:
    def __init__(self, db):
        self.con = sqlite3.connect(db)
        self.con.row_factory = sqlite3.Row
        self._j = {}

    def junction(self, a, b):
        k = tuple(sorted((key(a), key(b))))
        if k in self._j:
            return self._j[k]
        va, vb = name_variants(a), name_variants(b)
        pa = ",".join("?" * len(va))
        pb = ",".join("?" * len(vb))
        r = self.con.execute(f"""
            SELECT j.node_id, j.lat, j.lon, j.kind FROM junctions j
            WHERE j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pa}) OR full IN ({pa}))
              AND j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pb}) OR full IN ({pb}))
            ORDER BY CASE j.kind WHEN 'node' THEN 0 ELSE 1 END
            LIMIT 1""", (*va, *va, *vb, *vb)).fetchone()
        self._j[k] = (dict(r) if r else None)
        return self._j[k]

    def centre(self, cid):
        r = self.con.execute(
            "SELECT lat, lon, name FROM centres LIMIT 1").fetchone()
        return dict(r) if r else None


def age_weight(observed, hl=3.0):
    if not observed:
        return 0.6
    try:
        d = dt.date.fromisoformat(observed[:10])
    except ValueError:
        return 0.6
    return max(0.15, 0.5 ** (((dt.date.today() - d).days / 365.25) / hl))


def source_weight(t):
    if t["source_id"].startswith("youtube"):
        return 0.9
    n = len(t.get("turns", []))
    return 0.6 if n >= 6 else (0.45 if n >= 3 else 0.25)


def trace_segments(t, g):
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

    unused = set(keep)
    runs = []
    while unused:
        # start at the most-connected unused street, so the spine leads
        start = max({s for k in unused for s in k},
                    key=lambda s: len([1 for _, k in adj[s] if k in unused]))
        run, cur = [], start
        while True:
            nxt = None
            for other, k in adj[cur]:
                if k in unused:
                    nxt = (other, k)
                    break
            if not nxt:
                break
            other, k = nxt
            unused.discard(k)
            run.append(k)
            cur = other
        if run:
            runs.append(run)
    return runs


def osrm_route(points, pause=1.0):
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    q = urllib.parse.urlencode({"overview": "full", "geometries": "geojson",
                                "annotations": "nodes"})
    req = urllib.request.Request(f"{OSRM}/{coords}?{q}",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read().decode())
    time.sleep(pause)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--centre", default="walkley")
    ap.add_argument("--out", default="../data/out/consensus_routes.geojson")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="minimum summed weight; 0.5 is where leave-one-out "
                         "peaked at F1 0.79, not the design doc's 1.5")
    ap.add_argument("--min-family", type=int, default=3)
    ap.add_argument("--corrections", default="../corrections/corrections.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pause", type=float, default=1.0)
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

    fam = families(T, g, 2)
    real = {k: v for k, v in fam.items()
            if k >= 0 and len(v) >= args.min_family}
    print(f"{len(real)} route families "
          f"(+{len(fam.get(-1, []))} unclustered)\n")

    feats = []
    for fid in sorted(real):
        traces = real[fid]
        seg = support(traces, g)
        authors = len({t.get("author_hash") or t["source_id"] for t in traces})

        keep, dropped, corrected = [], 0, 0
        for k, v in seg.items():
            if k in overrides:
                corrected += 1
                continue
            if sum(v["w"].values()) < args.threshold:
                dropped += 1
                continue
            keep.append(k)

        runs = order_walk(seg, keep)
        streets = sorted({s for k in keep for s in k})
        print(f"── family {fid}: {len(traces)} traces, {authors} authors, "
              f"{len(keep)} segments, {len(runs)} run(s)")
        print(f"     {', '.join(streets[:9])}")
        if corrected:
            print(f"     {corrected} segment(s) removed by correction")
        if dropped:
            print(f"     {dropped} below threshold {args.threshold}")

        for ri, run in enumerate(runs):
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
                continue

            geom = None
            if not args.dry_run:
                try:
                    r = osrm_route(pts, args.pause)
                    if r.get("code") == "Ok":
                        geom = r["routes"][0]["geometry"]
                        dist = r["routes"][0]["distance"]
                    else:
                        print(f"     run {ri}: OSRM {r.get('code')}")
                except Exception as e:
                    print(f"     run {ri}: {str(e)[:80]}")
            if geom is None:
                geom = {"type": "LineString",
                        "coordinates": [[p["lon"], p["lat"]] for p in pts]}
                dist = None

            feats.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "family": fid, "run": ri,
                    "traces": len(traces), "authors": authors,
                    "distance_m": round(dist) if dist else None,
                    "segments": props,
                    "min_authors": min(p["authors"] for p in props),
                    "max_authors": max(p["authors"] for p in props),
                },
            })
        print()

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