"""
count_sources.py

Counts candidate route sources for a DriveTest centre.
Metadata only. Answers one question: how many sources contain a
traceable turn sequence?

Usage:
    python count_sources.py --centre walkley
"""

import argparse
import csv
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

YT_KEY = os.getenv("YOUTUBE_API_KEY")
YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"

CENTRES = {
    "walkley": {
        "label": "Ottawa Walkley",
        "queries": [
            "Ottawa Walkley road test route",
            "Walkley G2 test route",
            "Walkley driving test Ottawa",
            "Ottawa G2 road test Walkley",
        ],
        "subreddits": ["ottawa", "CanadaDrivers"],
        "reddit_queries": ["Walkley road test", "Walkley G2", "Walkley driving test"],
    },
    "canotek": {
        "label": "Ottawa Canotek",
        "queries": [
            "Ottawa Canotek road test route",
            "Canotek G2 test route",
            "Canotek driving test Ottawa",
        ],
        "subreddits": ["ottawa", "CanadaDrivers"],
        "reddit_queries": ["Canotek road test", "Canotek G2"],
    },
    "smithsfalls": {
        "label": "Smiths Falls",
        "queries": [
            "Smiths Falls road test route",
            "Smiths Falls G test route",
        ],
        "subreddits": ["ottawa", "CanadaDrivers"],
        "reddit_queries": ["Smiths Falls road test", "Smiths Falls G test"],
    },
    "winchester": {
        "label": "Winchester",
        "queries": [
            "Winchester Ontario road test route",
            "Winchester G2 test route Ontario",
        ],
        "subreddits": ["ottawa", "CanadaDrivers"],
        "reddit_queries": ["Winchester road test", "Winchester G2"],
    },
}

ISO_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def iso_to_seconds(s):
    m = ISO_DUR.fullmatch(s or "")
    if not m:
        return 0
    h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


def youtube_search(queries, per_query=25):
    if not YT_KEY:
        print("  no YOUTUBE_API_KEY set, skipping YouTube", file=sys.stderr)
        return {}

    found = {}
    for q in queries:
        try:
            r = requests.get(
                YT_SEARCH,
                params={
                    "key": YT_KEY,
                    "q": q,
                    "part": "snippet",
                    "type": "video",
                    "maxResults": per_query,
                    "relevanceLanguage": "en",
                },
                timeout=20,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  search failed for {q!r}: {e}", file=sys.stderr)
            continue

        items = r.json().get("items", [])
        for it in items:
            vid = it["id"]["videoId"]
            if vid not in found:
                found[vid] = it["snippet"]
        print(f"  {q!r}: {len(items)} results")
        time.sleep(0.2)

    return found


def youtube_details(video_ids):
    details = {}
    ids = list(video_ids)
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        try:
            r = requests.get(
                YT_VIDEOS,
                params={
                    "key": YT_KEY,
                    "id": ",".join(batch),
                    "part": "contentDetails,statistics",
                },
                timeout=20,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  details failed: {e}", file=sys.stderr)
            continue

        for it in r.json().get("items", []):
            cd = it.get("contentDetails", {})
            details[it["id"]] = {
                "seconds": iso_to_seconds(cd.get("duration")),
                "captions": cd.get("caption") == "true",
                "views": it.get("statistics", {}).get("viewCount", ""),
            }
    return details


def reddit_search(subreddits, queries, limit=50):
    headers = {"User-Agent": "ontarioroadtestmap/0.1 source count script"}

    rows = []
    seen = set()
    for sub in subreddits:
        for q in queries:
            url = f"https://www.reddit.com/r/{sub}/search.json"
            try:
                r = requests.get(
                    url,
                    params={"q": q, "restrict_sr": 1, "limit": limit,
                            "sort": "relevance"},
                    headers=headers,
                    timeout=20,
                )
                if r.status_code == 429:
                    print(f"  rate limited on r/{sub}, backing off", file=sys.stderr)
                    time.sleep(30)
                    continue
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"  r/{sub} {q!r} failed: {e}", file=sys.stderr)
                time.sleep(3)
                continue

            children = r.json().get("data", {}).get("children", [])
            n = 0
            for c in children:
                p = c.get("data", {})
                pid = p.get("id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                n += 1
                rows.append({
                    "source_type": "reddit",
                    "url": "https://reddit.com" + p.get("permalink", ""),
                    "title": p.get("title", ""),
                    "published": time.strftime("%Y-%m-%d",
                                               time.gmtime(p.get("created_utc", 0))),
                    "duration_s": "",
                    "has_captions": "",
                    "extra": f"score={p.get('score','')} comments={p.get('num_comments','')}",
                    "traceable": "",
                    "notes": "",
                })
            print(f"  r/{sub} {q!r}: {n} new")
            time.sleep(2)

    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--centre", required=True, choices=sorted(CENTRES))
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-seconds", type=int, default=180)
    args = ap.parse_args()

    cfg = CENTRES[args.centre]
    out_path = args.out or f"{args.centre}_sources.csv"

    print(f"\n== {cfg['label']} ==")
    print("\nYouTube:")
    snippets = youtube_search(cfg["queries"])
    details = youtube_details(snippets.keys()) if snippets else {}

    rows = []
    for vid, snip in snippets.items():
        d = details.get(vid, {})
        secs = d.get("seconds", 0)
        rows.append({
            "source_type": "youtube",
            "url": f"https://youtube.com/watch?v={vid}",
            "title": snip.get("title", ""),
            "published": snip.get("publishedAt", "")[:10],
            "duration_s": secs,
            "has_captions": d.get("captions", ""),
            "extra": f"views={d.get('views','')} channel={snip.get('channelTitle','')}",
            "traceable": "",
            "notes": "SHORT" if secs and secs < args.min_seconds else "",
        })

    print("\nReddit:")
    rows += reddit_search(cfg["subreddits"], cfg["reddit_queries"])

    fields = ["source_type", "url", "title", "published",
              "duration_s", "has_captions", "extra", "traceable", "notes"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    yt = sum(1 for r in rows if r["source_type"] == "youtube")
    rd = sum(1 for r in rows if r["source_type"] == "reddit")
    short = sum(1 for r in rows if r["notes"] == "SHORT")

    print(f"\nWrote {out_path}")
    print(f"  youtube: {yt}  ({short} under {args.min_seconds}s)")
    print(f"  reddit:  {rd}")
    print(f"  total candidates: {len(rows)}")
    print("\nNow fill the `traceable` column: y/n.")
    print("y only if the source gives an ORDERED sequence of turns with street names.")


if __name__ == "__main__":
    main()