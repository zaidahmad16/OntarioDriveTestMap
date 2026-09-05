#!/usr/bin/env python3
"""
drive_past.py — separate streets the route turns onto from streets it
merely passes through.

DESTINATION: analysis/drive_past.py

The problem, from a dashcam frame at t=1037 of eIyLEPqtM_w: an overhead
mast sign at a signalised intersection reads "Colliston" on the left and
"Albion N" on the right. The car goes straight through. OCR reads both
correctly. The route turns onto Albion and never touches Colliston.

Nothing already in the pipeline separates them:

  junction validation  passes — the junction is real and on the route
  duration filtering   passes — the sign is visible for a normal approach
  fuzzy-match checks   passes — the OCR read is exactly right
  support thresholds   passes — 7 authors, 15 video mentions
  closed-walk checks   passes — it is a valid edge at a node on the route

The one signal that does separate them is what kind of source saw it.

  a camera records every street name present at a junction
  a person records only the street they turned onto

Five independent Reddit authors wrote out the Walkley G route turn by
turn. All five name Albion. None names Colliston.

So: at a junction where at least one competing segment has text
corroboration, a segment with video mentions only is a drive-past
candidate. This is deliberately narrow. Video is the more complete
source overall — 0 of 20 video traces failed junction resolution against
11 of 20 text traces — and nothing here discounts video generally.

Usage:
    python3 drive_past.py ../data/out/reddit_traces.json \\
        ../data/out/ocr_traces.json ../data/out/ocr_traces_g.json \\
        --db ../data/osm.db --centre walkley
    python3 drive_past.py ... --apply --out ../data/out/segments.json
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "common"))
# One normaliser for the whole pipeline. Five files previously carried
# their own copy under five names, and on 2026-09-04 two of them
# disagreed: video traces keyed "airport" while text traces keyed
# "airport parkway", so one street looked like two competing streets at
# the same junction and this classifier flagged a real segment.
from streetnames import load_known, key, variants as name_variants


class Graph:
    def __init__(self, db):
        self.con = sqlite3.connect(db)
        self.con.row_factory = sqlite3.Row
        self._j = {}

    def variants(self, n):
        return name_variants(n)

    def junction(self, a, b):
        """Node where two streets meet, or None."""
        k = tuple(sorted((key(a), key(b))))
        if k in self._j:
            return self._j[k]
        va, vb = self.variants(a), self.variants(b)
        pa = ",".join("?" * len(va))
        pb = ",".join("?" * len(vb))
        r = self.con.execute(f"""
            SELECT j.node_id FROM junctions j
            WHERE j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pa}) OR full IN ({pa}))
              AND j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pb}) OR full IN ({pb}))
            ORDER BY CASE j.kind WHEN 'node' THEN 0 ELSE 1 END
            LIMIT 1""", (*va, *va, *vb, *vb)).fetchone()
        self._j[k] = r["node_id"] if r else None
        return self._j[k]


def gather(paths, g, centre):
    """Segment -> evidence, keyed by the junction node it sits on.

    Authors are deduplicated per segment per source kind: one person
    contributing four traces is one author, and the same person seen in
    video and in text counts once for each because those are different
    kinds of observation.
    """
    seg = defaultdict(lambda: {"video": set(), "text": set(),
                               "node": None, "mentions": 0})
    n_traces = 0
    for p in paths:
        if not os.path.exists(p):
            print(f"  ! missing {p}")
            continue
        for t in json.load(open(p)):
            if centre and centre not in str(t.get("centre_id", "")):
                continue
            n_traces += 1
            kind = "video" if t["source_id"].startswith("youtube") else "text"
            who = t.get("author_hash") or t["source_id"]
            st = [x["street"] for x in t.get("turns", [])]
            for a, b in zip(st, st[1:]):
                if a == b or a.startswith("@") or b.startswith("@"):
                    continue
                node = g.junction(a, b)
                if node is None:
                    continue
                k = tuple(sorted((key(a), key(b))))
                seg[k][kind].add(who)
                seg[k]["node"] = node
                seg[k]["mentions"] += 1
    return seg, n_traces


def classify(seg, min_video=3):
    """Flag video-only segments that share a junction with a text-backed one.

    Sharing a junction is what makes the comparison fair. A video-only
    segment somewhere no text source ever described might simply be a
    part of the route nobody wrote about. A video-only segment at a
    junction where another segment IS text-corroborated is different:
    a person was there, described the turn, and did not mention this
    street.
    """
    by_node = defaultdict(list)
    for k, v in seg.items():
        by_node[v["node"]].append(k)

    out = {}
    for k, v in seg.items():
        nv, nt = len(v["video"]), len(v["text"])
        peers = [p for p in by_node[v["node"]] if p != k]
        peer_text = max((len(seg[p]["text"]) for p in peers), default=0)

        if nt > 0:
            verdict, why = "route", "text corroborated"
        elif nv < min_video:
            verdict, why = "weak", f"video only, {nv} author(s)"
        elif peer_text > 0:
            verdict, why = "drive-past", (
                f"video only ({nv}), but a segment at the same junction "
                f"has {peer_text} text author(s)")
        else:
            verdict, why = "unconfirmed", (
                f"video only ({nv}), no text source describes this junction")

        out[k] = {**v, "video_n": nv, "text_n": nt,
                  "verdict": verdict, "why": why,
                  "peers": [f"{p[0]}×{p[1]}" for p in peers]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--centre", default="walkley")
    ap.add_argument("--min-video", type=int, default=3,
                    help="video authors needed before a segment can be "
                         "called a drive-past rather than merely weak")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="../data/out/segments.json")
    args = ap.parse_args()

    n_names, _ = load_known(args.db)
    g = Graph(args.db)
    seg, n = gather(args.traces, g, args.centre)
    res = classify(seg, args.min_video)
    print(f"{n} traces, {len(res)} segments\n")

    order = {"route": 0, "drive-past": 1, "unconfirmed": 2, "weak": 3}
    rows = sorted(res.items(),
                  key=lambda kv: (order[kv[1]["verdict"]],
                                  -kv[1]["video_n"] - kv[1]["text_n"]))

    cur = None
    for k, v in rows:
        if v["verdict"] != cur:
            cur = v["verdict"]
            print(f"\n── {cur} ──")
        print(f"  {k[0]} × {k[1]:<22s} vid {v['video_n']}  txt {v['text_n']}"
              f"   {v['why']}")

    counts = defaultdict(int)
    for v in res.values():
        counts[v["verdict"]] += 1
    print("\n" + "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))

    flagged = [k for k, v in res.items() if v["verdict"] == "drive-past"]
    if flagged:
        print(f"\n{len(flagged)} segment(s) flagged as drive-past. These "
              f"would otherwise render as high confidence:")
        for k in flagged:
            v = res[k]
            print(f"  {k[0]} × {k[1]}  ({v['video_n']} video authors, "
                  f"{v['mentions']} mentions, 0 text)")
            if v["peers"]:
                print(f"     shares a junction with: {', '.join(v['peers'])}")

    if args.apply:
        payload = {f"{k[0]}|{k[1]}": {
            "streets": list(k), "node": v["node"],
            "video_authors": v["video_n"], "text_authors": v["text_n"],
            "mentions": v["mentions"], "verdict": v["verdict"],
            "reason": v["why"]} for k, v in res.items()}
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        json.dump(payload, open(args.out, "w"), indent=1)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())