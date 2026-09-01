#!/usr/bin/env python3
"""
extract_frames.py — pull frames locally, ready for OCR elsewhere.

DESTINATION: acquisition/extract_frames.py

Downloading has to happen from a residential IP. YouTube already returned
IpBlocked for 25 transcript fetches, and datacenter IPs fare worse. So:
download and cut frames here, upload only the frames, run OCR on a GPU.

Frames are cropped and downscaled before upload. Street blade signs sit in
the upper part of the frame, never on the road surface.

Use --ids with a hand-checked list. The csv's `centre` column says walkley
for all 94 rows because the collector stamped the search term onto every
result — two of them are Canotek and one is Smiths Falls.
"""

import argparse
import csv as _csv
import glob
import os
import shutil
import subprocess
import sys


def download(vid, outdir, height):
    out = os.path.join(outdir, f"{vid}.mp4")
    if os.path.exists(out):
        return out
    subprocess.run(
        ["yt-dlp", "-f",
         f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
         "--merge-output-format", "mp4", "-o", out, "-q", "--no-warnings",
         f"https://www.youtube.com/watch?v={vid}"])
    return out if os.path.exists(out) else None


def frames(video, outdir, fps, crop_top, scale_w):
    os.makedirs(outdir, exist_ok=True)
    vf = f"fps={fps},crop=in_w:in_h*{crop_top}:0:0,scale={scale_w}:-2"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", video, "-vf", vf,
         "-q:v", "4", os.path.join(outdir, "f_%05d.jpg")], check=True)
    return sorted(glob.glob(os.path.join(outdir, "*.jpg")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="../data/raw/walkley_sources.csv")
    ap.add_argument("--outdir", default="../data/raw/frames_for_colab")
    ap.add_argument("--videodir", default="../data/raw/video")
    ap.add_argument("--class", dest="cls", default=None)
    ap.add_argument("--ids", help="file with one video_id per line")
    ap.add_argument("--fps", type=float, default=0.5)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--crop-top", type=float, default=0.65)
    ap.add_argument("--scale-w", type=int, default=1280)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--keep-video", action="store_true")
    ap.add_argument("--zip", action="store_true")
    args = ap.parse_args()

    rows = list(_csv.DictReader(open(args.csv, encoding="utf-8")))

    wanted = None
    if args.ids:
        wanted = {l.strip() for l in open(args.ids)
                  if l.strip() and not l.startswith("#")}

    vids, seen = [], set()
    for r in rows:
        v = r.get("video_id")
        if not v or v in seen:
            continue
        if wanted is not None:
            if v not in wanted:
                continue
        elif args.cls and r.get("test_class") != args.cls and \
                args.cls.lower() not in (r.get("title") or "").lower():
            continue
        seen.add(v)
        vids.append((v, int(r.get("duration_s") or 0), r.get("title", "")[:50]))

    vids.sort(key=lambda x: -x[1])
    if args.limit:
        vids = vids[:args.limit]

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.videodir, exist_ok=True)
    print(f"{len(vids)} videos, fps={args.fps}, crop_top={args.crop_top}")

    total = 0
    for i, (vid, dur, title) in enumerate(vids, 1):
        fdir = os.path.join(args.outdir, vid)
        if os.path.exists(fdir) and glob.glob(os.path.join(fdir, "*.jpg")):
            print(f"  [{i}/{len(vids)}] {vid}  already extracted")
            continue
        print(f"  [{i}/{len(vids)}] {vid}  {dur//60}min  {title}")
        v = download(vid, args.videodir, args.height)
        if not v:
            print("      download failed")
            continue
        paths = frames(v, fdir, args.fps, args.crop_top, args.scale_w)
        mb = sum(os.path.getsize(p) for p in paths) / 1e6
        total += mb
        print(f"      {len(paths)} frames, {mb:.0f} MB")
        if not args.keep_video:
            os.remove(v)

    print(f"\n  total {total:.0f} MB in {args.outdir}")

    if args.zip:
        z = shutil.make_archive(args.outdir, "zip", args.outdir)
        print(f"  wrote {z}  ({os.path.getsize(z)/1e6:.0f} MB)")
        print("  upload that to Drive, then run the Colab notebook.")
    return 0


if __name__ == "__main__":
    sys.exit(main())