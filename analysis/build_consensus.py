#!/usr/bin/env python3
"""
build_consensus.py — per-segment support and confidence, as GeoJSON.

DESTINATION: analysis/build_consensus.py

Three decisions baked in, each from something that went wrong earlier:

  count AUTHORS, not traces. jt7gwng contributed four traces. That is
  one source. Counting traces produced a corroboration figure that was
  wrong by a factor of four and had to be retracted.

  validate against the road graph. A segment whose two streets do not
  meet at a junction is not a segment — it is an extraction error.
  This kills "walkley -> get -> airport parkway" without a wordlist.

  report the raw author count separately from the weighted score. A
  confidence score is a judgement; an author count is a fact.
"""

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

SUFFIXES = {"road", "rd", "street", "st", "avenue", "ave", "drive", "dr",
            "boulevard", "blvd", "parkway", "pkwy", "crescent", "cres",
            "court", "crt", "lane", "ln", "way", "place", "pl", "private",
            "terrace", "circle", "trail"}

KNOWN = set()


def base(s):
    """Strip the trailing street type, unless the full name is itself a
    street in the extract.

    "airport parkway" must not become "airport" — Airport Parkway and
    the airport service roads are different roads kilometres apart, and
    collapsing them merged evidence for one into the other. This is the
    third file this bug has appeared in.
    """
    full = " ".join(re.sub(r"[^\w\s]", " ", (s or "").lower()).split())
    if full in KNOWN:
        return full
    w = full.split()
    while w and w[-1] in SUFFIXES:
        w.pop()
    return " ".join(w) if w else full


class Graph:
    def __init__(self, db):
        self.con = sqlite3.connect(db)
        self.con.row_factory = sqlite3.Row
        self._j = {}
        KNOWN.update(r[0] for r in
                     self.con.execute("SELECT DISTINCT full FROM streets")
                     if r[0])

    def variants(self, n):
        b = base(n)
        f = " ".join(re.sub(r"[^\w\s]", " ", (n or "").lower()).split())
        return list({b, f} - {""})

    def junction(self, a, b):
        k = tuple(sorted((base(a), base(b))))
        if k in self._j:
            return self._j[k]
        va, vb = self.variants(a), self.variants(b)
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
        out = (r["node_id"], r["lat"], r["lon"], r["kind"]) if r else None
        self._j[k] = out
        return out


def age_weight(observed, half_life_years=3.0):
    """Routes change. Decay rather than a cutoff — a cutoff throws away
    the only evidence some segments have."""
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

    Video scores highest because a camera cannot skip a street the way
    a person recalling a route can — 0 of 8 video traces failed junction
    resolution against 11 of 20 text traces.
    """
    if t["source_id"].startswith("youtube"):
        return 0.9
    n = len(t.get("turns", []))
    if n >= 6:
        return 0.6
    if n >= 3:
        return 0.45
    return 0.25


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--centre", default="walkley")
    ap.add_argument("--out", default="../data/out/consensus.geojson")
    ap.add_argument("--min-authors", type=int, default=1)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

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

    seg = defaultdict(lambda: {"authors": {}, "sources": defaultdict(int),
                               "last": "", "kind": "node"})
    dropped = defaultdict(int)

    for t in traces:
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
                dropped[tuple(sorted((base(a), base(b))))] += 1
                continue
            k = tuple(sorted((base(a), base(b))))
            s = seg[k]
            s["authors"][who] = max(s["authors"].get(who, 0), sw * aw)
            s["sources"][kind] += 1
            s["kind"] = j[3]
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
            "junction": s["kind"],
            "node": j[0] if j else None,
            "lat": j[1] if j else None,
            "lon": j[2] if j else None,
        })
    rows.sort(key=lambda r: (-r["authors"], -r["weight"]))

    print(f"{'segment':40s} {'auth':>4s} {'wt':>6s} {'vid':>4s} {'txt':>4s}  last")
    for r in rows:
        nm = f"{r['streets'][0]} x {r['streets'][1]}"
        via = " (ramp)" if r["junction"] == "ramp" else ""
        print(f"{nm:40s} {r['authors']:4d} {r['weight']:6.2f} "
              f"{r['video']:4d} {r['text']:4d}  {r['last_seen']}{via}")

    if dropped:
        print(f"\ndropped, no junction on the graph ({len(dropped)}):")
        for k, c in sorted(dropped.items(), key=lambda kv: -kv[1])[:12]:
            print(f"   {k[0]} x {k[1]}  ({c}x)")

    if args.report:
        return 0

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