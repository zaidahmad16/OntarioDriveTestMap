#!/usr/bin/env python3
"""
ocr_traces.py — turn OCR street sightings into intermediate-form traces.

DESTINATION: extraction/ocr_traces.py

Four filters, each for a failure mode seen in the real output:

  duration   a real blade sign is visible 1-9s. "parkin" held for 43s —
             a parking sign or something inside the car, not a street.
  burst      a frame naming many streets at once is a map overlay or
             title card. Two "Route Guide" videos opened with eight
             streets in alphabetical order, which no car ever drives.
  junction   consecutive streets must meet on the road graph. If OCR
             reads walkley then verger and no junction exists, verger is
             a shopfront.
  dedupe     collapse repeated readings of the same street.

A sign gives no direction, so turns are emitted as "straight" and the
geometry is recovered by routing through the junctions in order.
"""

import argparse
import csv as _csv
import json
import os
import re
import sqlite3
import sys

SUFFIXES = {"road", "rd", "street", "st", "avenue", "ave", "drive", "dr",
            "boulevard", "blvd", "parkway", "pkwy", "crescent", "cres",
            "court", "crt", "lane", "ln", "way", "place", "pl", "private",
            "terrace", "circle", "trail"}


def base_name(s):
    w = re.sub(r"[^\w\s]", " ", (s or "").lower()).split()
    while w and w[-1] in SUFFIXES:
        w.pop()
    return " ".join(w) if w else (s or "").lower()


class Graph:
    def __init__(self, db):
        self.con = sqlite3.connect(db)
        self.con.row_factory = sqlite3.Row

    def variants(self, name):
        b = base_name(name)
        f = " ".join(re.sub(r"[^\w\s]", " ", (name or "").lower()).split())
        return list({b, f} - {""})

    def meet(self, a, b):
        va, vb = self.variants(a), self.variants(b)
        pa = ",".join("?" * len(va))
        pb = ",".join("?" * len(vb))
        r = self.con.execute(f"""
            SELECT 1 FROM junctions j
            WHERE j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pa}) OR full IN ({pa}))
              AND j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pb}) OR full IN ({pb}))
            LIMIT 1""", (*va, *va, *vb, *vb)).fetchone()
        return r is not None

    def exists(self, name):
        v = self.variants(name)
        p = ",".join("?" * len(v))
        return self.con.execute(
            f"SELECT 1 FROM streets WHERE base IN ({p}) OR full IN ({p}) "
            f"LIMIT 1", (*v, *v)).fetchone() is not None


def sightings(hits, max_run, max_per_frame):
    per = {}
    for h in hits:
        if len(h["streets"]) > max_per_frame:
            continue
        for s in h["streets"]:
            per.setdefault(base_name(s), []).append(h["t"])

    out = []
    for s, ts in per.items():
        ts = sorted(set(ts))
        start = prev = ts[0]
        for t in ts[1:] + [None]:
            if t is None or t - prev > 5:
                dur = prev - start + 1
                if dur <= max_run:
                    out.append((start, dur, s))
                if t is not None:
                    start = t
            if t is not None:
                prev = t
    out.sort()
    return out


def validate(seq, g):
    if len(seq) < 2:
        return seq, []
    keep, dropped = [], []
    for i, (t, d, s) in enumerate(seq):
        nb = []
        lo, hi = max(0, i - 2), min(len(seq), i + 3)
        nb = [seq[j][2] for j in range(lo, hi) if j != i]
        if any(n != s and g.meet(s, n) for n in nb):
            keep.append((t, d, s))
        else:
            dropped.append((s, "no junction with neighbours"))
    return keep, dropped


def dedupe(seq):
    out = []
    for t, d, s in seq:
        if out and out[-1][2] == s:
            pt, pd, ps = out[-1]
            out[-1] = (pt, max(pd, t + d - pt), ps)
            continue
        out.append((t, d, s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--csv", default="../data/raw/walkley_sources.csv")
    ap.add_argument("--out", default="../data/out/ocr_traces.json")
    ap.add_argument("--max-run", type=float, default=15.0)
    ap.add_argument("--max-per-frame", type=int, default=3)
    ap.add_argument("--min-turns", type=int, default=2)
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    data = json.load(open(args.results))
    g = Graph(args.db)

    meta = {}
    if os.path.exists(args.csv):
        for r in _csv.DictReader(open(args.csv, encoding="utf-8")):
            if r.get("video_id"):
                meta[r["video_id"]] = r

    traces = []
    for vid, r in data.items():
        raw = sightings(r.get("hits", []), args.max_run, args.max_per_frame)
        if args.no_validate:
            seq, dropped = raw, []
        else:
            seq, dropped = validate(raw, g)
        seq = dedupe(seq)

        m = meta.get(vid, {})
        names = [s for _, _, s in seq]
        print(f"{vid}  {len(r.get('hits', []))} hits -> {len(seq)} sightings")
        if names:
            print("   " + " → ".join(names))
        if dropped:
            print(f"   dropped: {sorted({d[0] for d in dropped})}")

        if len(seq) >= args.min_turns:
            traces.append({
                "source_id": f"youtube:{vid}",
                "centre_id": "walkley",
                "test_class": m.get("test_class", "unknown"),
                "reliability": 0.9,
                "observed_at": (m.get("published") or "")[:10],
                "author_hash": m.get("channel", ""),
                "turns": [{"direction": "straight", "street": s}
                          for _, _, s in seq],
                "sightings": [{"t": t, "dur": d, "street": s}
                              for t, d, s in seq],
            })

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(traces, open(args.out, "w"), indent=1)
    print(f"\n{len(traces)} traces -> {args.out}")

    if traces:
        best = max(traces, key=lambda t: len(t["turns"]))
        print(f"longest: {best['source_id']} ({len(best['turns'])} streets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())