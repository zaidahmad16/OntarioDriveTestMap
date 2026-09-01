#!/usr/bin/env python3
"""
ocr_probe.py — read street signs off dashcam footage.

DESTINATION: acquisition/ocr_probe.py

Streams frames in chunks: extract a batch, OCR it, keep only frames that
matched a street name, delete the rest, move on. Peak disk stays a few MB
instead of the hundreds a full extraction costs.

Requires:
    pip install yt-dlp paddleocr paddlepaddle
    sudo apt install ffmpeg

Usage:
    python3 ocr_probe.py VIDEO_ID --db ../data/osm.db --sample 40
    python3 ocr_probe.py VIDEO_ID --db ../data/osm.db
"""

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys


def need(cmd):
    if shutil.which(cmd) is None:
        print(f"missing: {cmd}")
        return False
    return True


def gazetteer(db, lat=45.376146, lon=-75.647589, r=0.05):
    con = sqlite3.connect(db)
    names = set()
    for base, full in con.execute(
            "SELECT DISTINCT js.base, js.full FROM junction_streets js "
            "JOIN junctions j ON j.node_id = js.node_id "
            "WHERE ABS(j.lat-?) < ? AND ABS(j.lon-?) < ?",
            (lat, r * 0.72, lon, r)):
        for v in (base, full):
            if v and len(v) >= 4:
                names.add(v)
    con.close()
    return names


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def download(vid, outdir, height):
    out = os.path.join(outdir, f"{vid}.mp4")
    if os.path.exists(out):
        print(f"  already have {out}")
        return out
    cmd = ["yt-dlp", "-f",
           f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
           "--merge-output-format", "mp4", "-o", out,
           f"https://www.youtube.com/watch?v={vid}"]
    print("  " + " ".join(cmd))
    subprocess.run(cmd)
    return out if os.path.exists(out) else None


def cut(video, tmp, start, dur, fps):
    """One chunk of frames into a scratch dir, emptied first."""
    os.makedirs(tmp, exist_ok=True)
    for f in glob.glob(os.path.join(tmp, "*.jpg")):
        os.remove(f)
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", str(start), "-i", video,
         "-t", str(dur), "-vf", f"fps={fps}", "-q:v", "2",
         os.path.join(tmp, "f_%05d.jpg")], check=True)
    return sorted(glob.glob(os.path.join(tmp, "*.jpg")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--workdir", default="../data/raw/video")
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--chunk", type=int, default=60,
                    help="seconds of video per batch")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--dur", type=int, default=0, help="0 = whole video")
    ap.add_argument("--keep-video", action="store_true")
    ap.add_argument("--sample", type=int, default=0,
                    help="grab N spread-out frames and stop, for eyeballing")
    args = ap.parse_args()

    if not (need("yt-dlp") and need("ffmpeg")):
        return 1

    os.makedirs(args.workdir, exist_ok=True)
    tmp = os.path.join(args.workdir, "_tmp")
    keep = os.path.join(args.workdir, "hits", args.video_id)
    os.makedirs(keep, exist_ok=True)

    print(f"\n1. video {args.video_id}")
    v = download(args.video_id, args.workdir, args.height)
    if not v:
        print("  download failed")
        return 1
    total = duration(v)
    print(f"  {os.path.getsize(v)/1e6:.0f} MB, {total/60:.1f} min")

    end = args.start + args.dur if args.dur else total

    if args.sample:
        step = max(1, int((end - args.start) / args.sample))
        print(f"\n2. sampling {args.sample} frames every {step}s")
        for i in range(args.sample):
            t = args.start + i * step
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", v,
                 "-frames:v", "1", "-q:v", "2",
                 os.path.join(keep, f"t{int(t):05d}.jpg")], check=True)
        print(f"  xdg-open {keep}")
        return 0

    gaz = gazetteer(args.db)
    print(f"\n2. ocr  ({len(gaz):,} local street names)")
    from paddleocr import PaddleOCR
    # enable_mkldnn=False: the oneDNN backend crashes on PP-OCRv6 with
    # "ConvertPirAttribute2RuntimeAttribute not support". Every frame
    # threw it, and a bare except swallowed all 1,095 identically.
    engine = PaddleOCR(lang="en", enable_mkldnn=False)

    hits, n_frames, n_text, n_err = [], 0, 0, 0
    t = args.start
    while t < end:
        span = min(args.chunk, end - t)
        paths = cut(v, tmp, t, span, args.fps)
        for i, p in enumerate(paths):
            ts = t + i / args.fps
            n_frames += 1
            try:
                res = engine.predict(p)
            except Exception as e:
                # Count and surface errors. Silently continuing turned a
                # total backend failure into a clean-looking zero result.
                n_err += 1
                if n_err <= 3:
                    print(f"    OCR error: {str(e)[:120]}")
                continue
            words = []
            for page in (res or []):
                words.extend(page.get("rec_texts") or [])
            text = " ".join(words)
            if text.strip():
                n_text += 1
            low = " " + re.sub(r"[^\w\s]", " ", text.lower()) + " "
            found = sorted({g for g in gaz if f" {g} " in low})
            if found:
                shutil.copy(p, os.path.join(keep, f"t{int(ts):05d}.jpg"))
                hits.append({"t": round(ts, 1), "streets": found,
                             "raw": text[:150]})
                print(f"    t={ts:7.1f}s  {found}")
        for p in paths:
            os.remove(p)
        t += span
        print(f"  ...{t:.0f}/{end:.0f}s   frames {n_frames}  hits {len(hits)}")

    shutil.rmtree(tmp, ignore_errors=True)

    out = os.path.join(args.workdir, f"{args.video_id}_ocr.json")
    json.dump({"video": args.video_id, "fps": args.fps, "hits": hits},
              open(out, "w"), indent=1)

    print("\n== result ==")
    print(f"  frames processed:       {n_frames}")
    print(f"  frames with any text:   {n_text}")
    print(f"  frames that errored:    {n_err}")
    print(f"  frames naming a street: {len(hits)}")
    if hits:
        print(f"  distinct streets: {sorted({s for h in hits for s in h['streets']})}")
        print(f"\n  matched frames kept in {keep}")
        print("  Check a few. A wrong read that happens to match a street")
        print("  name is worse than a miss — it becomes a real edge later.")
    else:
        print("\n  Nothing found. Try --sample 40 and look at the images.")

    if not args.keep_video:
        os.remove(v)
        print(f"\n  deleted {v}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())