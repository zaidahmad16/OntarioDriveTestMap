#!/usr/bin/env python3
"""
ocr_traces.py — turn OCR street sightings into intermediate-form traces.

DESTINATION: extraction/ocr_traces.py

Input:  ocr_results.json from the Colab OCR run
        osm.db, for junction validation
Output: traces in the same shape as the Reddit ones, so snap_traces.py
        consumes them without caring where they came from

Four filters, each for a failure mode seen in the real output:

  duration   a real blade sign is visible 1-9s as the car approaches and
             passes. "parkin" held for 43s — that is a parking sign or
             something inside the car, not a street.

  burst      a frame containing many street names at once is a map
             overlay or title card. Two "Complete Route Guide" videos
             opened with baycrest, cedarwood, fairlea, gore,
             heatherington, heron, sandalwood, walkley — alphabetical,
             which no car ever drives.

  junction   consecutive streets in a route must meet on the road graph.
             If OCR reads walkley then verger and no junction exists,
             verger is a shopfront. This is the strongest filter and it
             uses the index already built for snapping.

  dedupe     collapse repeated readings of the same street.

Direction is not recoverable from a sign, so turns are emitted as
"straight" and the geometry is recovered by routing through the
junctions in order.

Usage:
    python3 ocr_traces.py ../data/raw/walkley_ocr_g2.json \\
        --db ../data/osm.db --csv ../data/raw/walkley_sources.csv \\
        --out ../data/out/walkley/ocr_traces.json
"""

import argparse
import csv as _csv
import json
import os
import re
import sqlite3
import sys

# Shared normaliser. This file previously carried its own copy, which is
# how "Montréal Road" failed to match at Canotek — no accent folding.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from streetnames import load_known, key as base_name, variants as name_variants


class Graph:
    def __init__(self, db):
        self.con = sqlite3.connect(db)
        self.con.row_factory = sqlite3.Row

    def variants(self, name):
        return name_variants(name)

    def meet(self, a, b):
        """Do these two streets share a junction?"""
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
    """Timestamped hits -> [(start, duration, street)], filtered.

    A frame naming many streets at once is screen content, not road
    signage, so it is dropped before anything else.
    """
    per = {}
    for h in hits:
        if len(h["streets"]) > max_per_frame:
            continue                     # burst: map overlay or title card
        for s in h["streets"]:
            per.setdefault(base_name(s), []).append(h["t"])

    out = []
    for s, ts in per.items():
        ts = sorted(set(ts))
        start = prev = ts[0]
        for t in ts[1:] + [None]:
            if t is None or t - prev > 5:
                dur = prev - start + 1
                if dur <= max_run:       # duration: long runs are not signs
                    out.append((start, dur, s))
                if t is not None:
                    start = t
            if t is not None:
                prev = t
    out.sort()
    return out


def validate(seq, g):
    """Keep only streets that connect to a neighbour on the road graph.

    A street read once, with no graph connection to what came before or
    after, is a misread of something that is not a street. Endpoints are
    checked against their single neighbour.
    """
    if len(seq) < 2:
        return seq, []
    keep, dropped = [], []
    for i, (t, d, s) in enumerate(seq):
        nb = []
        if i > 0:
            nb.append(seq[i - 1][2])
        if i < len(seq) - 1:
            nb.append(seq[i + 1][2])
        if any(n != s and g.meet(s, n) for n in nb):
            keep.append((t, d, s))
        else:
            dropped.append((s, "no junction with neighbours"))
    return keep, dropped


def dedupe(seq):
    out = []
    for t, d, s in seq:
        if out and out[-1][2] == s:
            # same street seen again immediately: extend, do not repeat
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
    ap.add_argument("--out", default="../data/out/walkley/ocr_traces.json")
    ap.add_argument("--max-run", type=float, default=15.0,
                    help="seconds; longer sightings are not passing signs")
    ap.add_argument("--max-per-frame", type=int, default=3,
                    help="frames naming more streets than this are screen "
                         "content, not signage")
    ap.add_argument("--min-turns", type=int, default=2)
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    data = json.load(open(args.results))
    load_known(args.db)
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
                # video with readable street signage. Higher than a text
                # account: the sequence comes from timestamps rather than
                # from someone's memory of the order.
                "reliability": 0.9,
                "observed_at": (m.get("published") or "")[:10],
                "author_hash": m.get("channel", ""),
                # a sign gives no direction, only presence and order
                "turns": [{"direction": "straight", "street": s}
                          for _, _, s in seq],
                # per-sighting confidence: a 6s clear read is stronger
                # evidence than a 1s fuzzy match
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