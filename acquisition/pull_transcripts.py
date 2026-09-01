#!/usr/bin/env python3
"""
pull_transcripts.py — fetch transcripts for collected videos and measure
whether the narration actually names streets.

DESTINATION: acquisition/pull_transcripts.py
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time

TURN_NEAR = re.compile(
    r"\b(left|right|straight|turn|exit|merge|onto|into)\b", re.IGNORECASE)


def load_gazetteer(db):
    con = sqlite3.connect(db)
    names = set()
    for col in ("base", "full"):
        for (v,) in con.execute(f"SELECT DISTINCT {col} FROM streets"):
            if v and len(v) >= 4:
                names.add(v)
    con.close()
    return sorted(names, key=len, reverse=True)


def find_video_ids(csv_path):
    import csv as _csv
    ids, rows = [], []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            rows.append(row)
            vid = None
            for k, v in row.items():
                if not v:
                    continue
                kl = (k or "").lower()
                if kl in ("video_id", "videoid", "id"):
                    vid = v.strip()
                    break
                if "url" in kl or "link" in kl:
                    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", v)
                    if m:
                        vid = m.group(1)
                        break
            if vid:
                ids.append((vid, row))
    return ids, rows


def fetch(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["en", "en-CA", "en-US"])
        return [{"text": s.text, "start": s.start, "dur": s.duration}
                for s in fetched]
    except Exception as e:
        return {"error": type(e).__name__, "detail": str(e)[:160]}


def analyse(outdir, gaz):
    files = sorted(glob.glob(os.path.join(outdir, "*.json")))
    rows = []
    for fp in files:
        if os.path.basename(fp).startswith("_"):
            continue
        d = json.load(open(fp))
        if isinstance(d, dict) and "error" in d:
            rows.append({"video": os.path.basename(fp)[:-5],
                         "ok": False, "err": d["error"]})
            continue
        text = " ".join(s["text"] for s in d)
        low = " " + re.sub(r"[^\w\s]", " ", text.lower()) + " "
        hits, near = set(), set()
        for name in gaz:
            if f" {name} " in low:
                hits.add(name)
                for m in re.finditer(re.escape(f" {name} "), low):
                    window = low[max(0, m.start() - 60):m.end() + 60]
                    if TURN_NEAR.search(window):
                        near.add(name)
                        break
        rows.append({"video": os.path.basename(fp)[:-5], "ok": True,
                     "chars": len(text), "streets": len(hits),
                     "streets_near_turn": len(near),
                     "names": sorted(near) or sorted(hits)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--outdir", default="../data/raw/transcripts")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--analyse-only", action="store_true")
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    gaz = load_gazetteer(args.db)
    print(f"gazetteer: {len(gaz):,} names\n")

    if not args.analyse_only:
        if not args.csv:
            ap.error("provide the video csv, or use --analyse-only")
        ids, rows = find_video_ids(args.csv)
        print(f"csv rows: {len(rows)}   video ids found: {len(ids)}")
        if not ids:
            print("Columns:", list(rows[0].keys()) if rows else "(empty)")
            return 1
        if args.limit:
            ids = ids[:args.limit]
        ok = fail = skip = 0
        for i, (vid, row) in enumerate(ids, 1):
            path = os.path.join(args.outdir, f"{vid}.json")
            if os.path.exists(path):
                skip += 1
                continue
            r = fetch(vid)
            json.dump(r, open(path, "w"))
            if isinstance(r, dict):
                fail += 1
                print(f"  [{i}/{len(ids)}] {vid}  {r['error']}")
            else:
                ok += 1
                print(f"  [{i}/{len(ids)}] {vid}  {len(r)} segments")
            time.sleep(args.pause)
        print(f"\n  fetched {ok}   failed {fail}   already had {skip}")

    print("\n== analysis ==")
    rows = analyse(args.outdir, gaz)
    good = [r for r in rows if r["ok"]]
    print(f"  transcripts on disk:     {len(rows)}")
    print(f"  usable (not an error):   {len(good)}")
    if not good:
        return 0

    named = [r for r in good if r["streets"] > 0]
    turny = [r for r in good if r["streets_near_turn"] > 0]
    print(f"  naming >=1 street:       {len(named)}  "
          f"({len(named)/len(good)*100:.0f}%)")
    print(f"  street NEXT TO turn word:{len(turny)}  "
          f"({len(turny)/len(good)*100:.0f}%)   <-- the number that matters")
    print(f"  naming >=3 streets:      "
          f"{len([r for r in good if r['streets'] >= 3])}")

    print("\n  best transcripts:")
    for r in sorted(good, key=lambda x: -x["streets_near_turn"])[:12]:
        if r["streets_near_turn"]:
            print(f"    {r['video']}  {r['streets_near_turn']:2d} streets  "
                  f"{', '.join(r['names'][:8])}")

    errs = {}
    for r in rows:
        if not r["ok"]:
            errs[r["err"]] = errs.get(r["err"], 0) + 1
    if errs:
        print("\n  failures:")
        for k, v in sorted(errs.items(), key=lambda x: -x[1]):
            print(f"    {k}: {v}")

    json.dump(rows, open(os.path.join(outdir_safe(args.outdir)), "w"), indent=1)
    return 0


def outdir_safe(d):
    return os.path.join(d, "_analysis.json")


if __name__ == "__main__":
    sys.exit(main())
