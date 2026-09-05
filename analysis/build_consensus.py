#!/usr/bin/env python3
"""
build_consensus.py — per-segment support and confidence, as GeoJSON.

DESTINATION: analysis/build_consensus.py

Takes every trace from every source and produces one record per road
segment: which independent people described it, how strongly, and how
recently. That is what the map renders.

Three decisions baked in, each from something that went wrong earlier:

  count AUTHORS, not traces. jt7gwng contributed four traces. That is
  one source. Counting traces produced a corroboration figure that was
  wrong by a factor of four and had to be retracted.

  validate against the road graph. A segment whose two streets do not
  meet at a junction is not a segment — it is an extraction error.
  This kills "walkley -> get -> airport parkway" without a wordlist.

  weight by source type and recency, then report the raw author count
  separately. A confidence score is a judgement; an author count is a
  fact. The map should be able to show the fact.

Usage:
    python3 build_consensus.py ../data/out/reddit_traces.json \\
        ../data/out/ocr_traces.json --db ../data/osm.db \\
        --centre walkley --out ../data/out/consensus.geojson
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict

class Graph:
    def __init__(self, db):
        self.con = sqlite3.connect(db)
        self.con.row_factory = sqlite3.Row
        self._j = {}

    def variants(self, n):
        return name_variants(n)

    def junction(self, a, b):
        """Coordinate where two streets meet, or None."""
        k = tuple(sorted((key(a), key(b))))
        if k in self._j:
            return self._j[k]
        va, vb = self.variants(a), self.variants(b)
        pa = ",".join("?" * len(va))
        pb = ",".join("?" * len(vb))
        r = self.con.execute(f"""
            SELECT j.node_id, j.lat, j.lon FROM junctions j
            WHERE j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pa}) OR full IN ({pa}))
              AND j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pb}) OR full IN ({pb}))
            LIMIT 1""", (*va, *va, *vb, *vb)).fetchone()
        out = (r["node_id"], r["lat"], r["lon"]) if r else None
        self._j[k] = out
        return out


def age_weight(observed, half_life_years=3.0):
    """Routes change. A 2013 account is weaker evidence than a 2025 one.

    Exponential decay rather than a cutoff — a cutoff throws away the
    only evidence some segments have.
    """
    if not observed:
        return 0.6
    try:
        d = dt.date.fromisoformat(observed[:10])
    except ValueError:
        return 0.6
    years = (dt.date.today() - d).days / 365.25
    return max(0.15, 0.5 ** (years / half_life_years))


def source_weight(t):
    """What a source contributes, not which platform it came from.

    Video that names streets on signs cannot skip a street the way a
    person recalling a route can — measured: 0 of 8 video traces failed
    junction resolution against 11 of 20 text traces. That completeness
    is why video scores higher, not the medium itself.
    """
    if t["source_id"].startswith("youtube"):
        return 0.9
    n = len(t.get("turns", []))
    if n >= 6:
        return 0.6        # ordered list, most of a route
    if n >= 3:
        return 0.45
    return 0.25           # a couple of streets in passing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--centre", default="walkley")
    ap.add_argument("--out", default="../data/out/consensus.geojson")
    ap.add_argument("--min-authors", type=int, default=1)
    ap.add_argument("--report", action="store_true",
                    help="print the table, write nothing")
    args = ap.parse_args()

    load_known(args.db)
    g = Graph(args.db)

    traces = []
    for p in args.traces:
        if not os.path.exists(p):
            print(f"  ! missing {p}")
            continue
        for t in json.load(open(p)):
            if args.centre and args.centre not in str(t.get("centre_id", "")):
                continue
            traces.append(t)
    print(f"{len(traces)} traces\n")

    seg = defaultdict(lambda: {"authors": {}, "weight": 0.0,
                               "sources": defaultdict(int), "last": ""})
    dropped = defaultdict(int)

    for t in traces:
        # An author with no hash is treated as its own source rather than
        # pooled with every other anonymous one, which would understate
        # independence. Falls back to the source id.
        who = t.get("author_hash") or t["source_id"]
        kind = "video" if t["source_id"].startswith("youtube") else "text"
        sw = source_weight(t)
        aw = age_weight(t.get("observed_at"))
        st = [x["street"] for x in t.get("turns", [])]

        for a, b in zip(st, st[1:]):
            if a == b or a.startswith("@") or b.startswith("@"):
                continue
            j = g.junction(a, b)
            if not j:
                # not a junction on the graph, so not a segment. This is
                # where "get", "enter", "make" and shopfront misreads die.
                dropped[tuple(sorted((key(a), key(b))))] += 1
                continue
            k = tuple(sorted((key(a), key(b))))
            s = seg[k]
            # an author counts once per segment however many traces they
            # contributed, but keeps their strongest weight
            s["authors"][who] = max(s["authors"].get(who, 0), sw * aw)
            s["sources"][kind] += 1
            obs = (t.get("observed_at") or "")[:10]
            if obs > s["last"]:
                s["last"] = obs

    rows = []
    for k, s in seg.items():
        n = len(s["authors"])
        if n < args.min_authors:
            continue
        j = g.junction(*k)
        rows.append({
            "streets": list(k),
            "authors": n,
            "weight": round(sum(s["authors"].values()), 2),
            "video": s["sources"]["video"],
            "text": s["sources"]["text"],
            "last_seen": s["last"],
            "node": j[0] if j else None,
            "lat": j[1] if j else None,
            "lon": j[2] if j else None,
        })
    rows.sort(key=lambda r: (-r["authors"], -r["weight"]))

    print(f"{'segment':38s} {'auth':>4s} {'wt':>6s} {'vid':>4s} {'txt':>4s}  last")
    for r in rows:
        nm = f"{r['streets'][0]} × {r['streets'][1]}"
        print(f"{nm:38s} {r['authors']:4d} {r['weight']:6.2f} "
              f"{r['video']:4d} {r['text']:4d}  {r['last_seen']}")

    if dropped:
        print(f"\ndropped, no junction on the graph ({len(dropped)}):")
        for k, c in sorted(dropped.items(), key=lambda kv: -kv[1])[:12]:
            print(f"   {k[0]} × {k[1]}  ({c}×)")

    if args.report:
        return 0

    # Geometry: a point per junction. Segment lines need the road
    # geometry between two junctions, which is snap_traces' job — this
    # emits the junctions with their support so the map has something
    # real to render now.
    feats = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
        "properties": {k: v for k, v in r.items() if k not in ("lat", "lon")},
    } for r in rows if r["lat"]]

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump({"type": "FeatureCollection",
               "properties": {"centre": args.centre,
                              "built": dt.date.today().isoformat(),
                              "traces": len(traces)},
               "features": feats}, open(args.out, "w"), indent=1)
    print(f"\nwrote {args.out}  ({len(feats)} junctions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())