#!/usr/bin/env python3
"""
transcript_traces.py — turn fetched YouTube transcripts into traces.

DESTINATION: extraction/transcript_traces.py

Reuses the intermediate form so nothing downstream cares which source a
trace came from. Differences from the Reddit path:
  - transcripts are timestamped, so order comes from the timeline
  - narration says "turn left here" far more than it names a street
  - speech-to-text mangles names, so fuzzy snapping matters more
"""

import argparse
import csv as _csv
import difflib
import glob
import json
import os
import re
import sqlite3
import sys

DIRECTION = r"(left|right|straight)"

TURN = re.compile(
    r"\b(?:turn(?:ing|ed|s)?|make|take|takes?|go|going|head(?:ing|ed)?|"
    r"hang|hop|get)?\s*(?:a\s+)?" + DIRECTION +
    r"\s*(?:turn\s+)?(?:on|onto|at|into|down|to|towards?|toward)\s+"
    r"(?:the\s+)?([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2})",
    re.IGNORECASE)

# Ordinals and filler that collide with real Ottawa street names.
# "first", "second", "cross" are streets somewhere in the city and are
# almost never streets in a dashcam narration.
FALSE_FRIENDS = {
    "first", "second", "third", "fourth", "fifth", "cross", "toronto",
    "back", "front", "center", "centre", "green", "good", "little",
    "middle", "park", "ready", "forward", "button", "best", "bell",
    "mark", "school", "ontario", "canada", "academy", "line",
}

STOP = {
    "the","a","an","it","me","my","you","your","this","that","there","here",
    "then","when","where","which","who","and","but","so","if","of","to","in",
    "on","at","for","with","side","lane","turn","turns","road","street","way",
    "exit","light","lights","red","green","stop","sign","signal","test",
    "route","examiner","instructor","car","traffic","median","highway","hwy",
    "time","one","two","some","any","all","each","other","another","main",
    "same","first","second","third","last","next","left","right","straight",
    "north","south","east","west","centre","center","parking","lot","school",
    "zone","speed","limit","now","just","okay","ok","yeah","gonna","going",
    "us","them","him","her","our","we","they","he","she","i","is","are","be",
    "up","down","over","back","again","little","bit","gotta","want","need",
}

SUFFIX = {"rd","road","st","street","ave","avenue","dr","drive","blvd",
          "boulevard","pkwy","parkway","cres","crescent","way","lane","ln",
          "crt","court","terrace","circle","place"}


def gazetteer(db, lat=None, lon=None, radius_deg=0.05):
    """Street names, optionally restricted to near a centre.

    The full Ottawa gazetteer is 17,257 names and includes Good Street,
    Green Street, Back Lane and similar, so unrestricted matching turns
    "looking good" and "green light" into street hits. Restricting to
    junctions near the test centre removes most of that.
    """
    con = sqlite3.connect(db)
    names = set()
    if lat is not None:
        q = ("SELECT DISTINCT js.base, js.full FROM junction_streets js "
             "JOIN junctions j ON j.node_id = js.node_id "
             "WHERE ABS(j.lat-?) < ? AND ABS(j.lon-?) < ?")
        rows = con.execute(q, (lat, radius_deg * 0.72, lon, radius_deg))
    else:
        rows = con.execute("SELECT DISTINCT base, full FROM streets")
    for base, full in rows:
        for v in (base, full):
            if v and len(v) >= 4 and v not in FALSE_FRIENDS:
                names.add(v)
    con.close()
    return names


def clean(raw, gaz):
    """Captured span -> a gazetteer street, or None. Strict by design."""
    if not raw:
        return None
    kept = []
    for w in re.sub(r"[^\w\s]", " ", raw.lower()).split():
        if w in SUFFIX:
            kept.append(w)
            break
        if w in STOP:
            break
        if len(kept) >= 3:
            break
        kept.append(w)
    if not kept:
        return None
    full = " ".join(kept)
    base = " ".join(kept[:-1]) if kept[-1] in SUFFIX and len(kept) > 1 else full
    if base in FALSE_FRIENDS or full in FALSE_FRIENDS:
        return None
    for cand in (base, full):
        if cand in gaz:
            return cand
    near = difflib.get_close_matches(base, gaz, n=1, cutoff=0.88)
    return near[0] if near else None


def extract(segments, gaz):
    turns = []
    for s in segments:
        for m in TURN.finditer(s["text"]):
            st = clean(m.group(2), gaz)
            if not st:
                continue
            t = {"direction": m.group(1).lower(), "street": st,
                 "t": round(s["start"], 1)}
            if turns and turns[-1]["street"] == st and \
               turns[-1]["direction"] == t["direction"] and \
               t["t"] - turns[-1]["t"] < 8:
                continue
            turns.append(t)
    return turns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts", default="../data/raw/transcripts")
    ap.add_argument("--csv", default="../data/raw/walkley_sources.csv")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--out", default="../data/out/youtube_traces.json")
    ap.add_argument("--lat", type=float, default=45.376146)
    ap.add_argument("--lon", type=float, default=-75.647589)
    ap.add_argument("--min-turns", type=int, default=2)
    args = ap.parse_args()

    gaz = gazetteer(args.db, args.lat, args.lon)
    print(f"local gazetteer: {len(gaz):,} names")

    meta = {}
    if os.path.exists(args.csv):
        for r in _csv.DictReader(open(args.csv, encoding="utf-8")):
            if r.get("video_id"):
                meta[r["video_id"]] = r

    traces, rows = [], []
    for fp in sorted(glob.glob(os.path.join(args.transcripts, "*.json"))):
        vid = os.path.basename(fp)[:-5]
        if vid.startswith("_"):
            continue
        d = json.load(open(fp))
        if isinstance(d, dict):
            continue
        turns = extract(d, gaz)
        m = meta.get(vid, {})
        rows.append((vid, len(turns), m.get("test_class", "?"),
                     m.get("title", "")[:46], turns))
        if len(turns) >= args.min_turns:
            traces.append({
                "source_id": f"youtube:{vid}",
                "centre_id": m.get("centre", "walkley"),
                "test_class": m.get("test_class", "unknown"),
                "reliability": 0.8,
                "observed_at": (m.get("published") or "")[:10],
                "author_hash": m.get("channel", ""),
                "turns": [{"direction": t["direction"], "street": t["street"]}
                          for t in turns],
            })

    rows.sort(key=lambda x: -x[1])
    print(f"\ntranscripts read: {len(rows)}")
    print(f"traces (>= {args.min_turns} turns): {len(traces)}\n")
    for vid, n, cls, title, turns in rows[:12]:
        if n:
            seq = " → ".join(f"{t['direction'][0].upper()}:{t['street']}"
                             for t in turns[:10])
            print(f"  {n:3d}  {cls:9s} {vid}  {title}")
            print(f"       {seq}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(traces, open(args.out, "w"), indent=1)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
