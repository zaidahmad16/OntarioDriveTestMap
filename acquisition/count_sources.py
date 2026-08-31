"""
count_sources.py

The one collector. Replaces the earlier thin collector and youtube_deep.py.

Collects YouTube candidates for a test centre, pulls everything YouTube
gives without downloading video (description, transcript, comments),
guesses test class, scores each video by how many distinct streets it
names, and writes a CSV sorted best-first.

It does NOT decide what is usable. It ranks the queue so you read the
most promising videos first and fill the `traceable` column yourself.

Setup:
    pip install requests python-dotenv youtube-transcript-api
    .env must contain:  YOUTUBE_API_KEY=...

Usage:
    python count_sources.py --centre walkley
    python count_sources.py --centre walkley --no-comments
    python count_sources.py --all

Outputs:
    out/<centre>_sources.csv       one row per video, sorted by street count
    out/transcripts/<videoid>.txt  transcript for anything naming a street

Quota: each search costs 100 units of a 10,000/day allowance. Details
and comments cost 1 each. One centre is roughly 800-900 units.
"""

import argparse
import csv
import os
import re
import sys
import time
from collections import Counter

import requests
from dotenv import load_dotenv

load_dotenv()

YT_KEY = os.getenv("YOUTUBE_API_KEY")
API = "https://www.googleapis.com/youtube/v3"

OUT_DIR = "out"
TRANSCRIPT_DIR = os.path.join(OUT_DIR, "transcripts")

# ---------------------------------------------------------------------
# Centres
#
# Street lists are hand-seeded from the road network around each centre.
# Replace with a gazetteer generated from the OSM Ottawa extract once
# that exists. Lowercase, no punctuation, substring matched.
# ---------------------------------------------------------------------

CENTRES = {
    "walkley": {
        "label": "Ottawa Walkley",
        "address": "1570 Walkley Rd",
        "tests": ["G2", "G"],
        "queries": [
            "Ottawa Walkley road test route",
            "Walkley G2 test route",
            "Walkley G test route Ottawa",
            "Walkley driving test Ottawa",
            "Ottawa G2 road test Walkley",
            "1570 Walkley road test",
            "Ottawa Walkley drive test full route",
            "G2 test Ottawa south route",
        ],
        "streets": [
            "walkley", "albion", "conroy", "heron", "st laurent", "saint laurent",
            "bank street", "bank st", "airport parkway", "riverside", "russell road",
            "hunt club", "bronson", "baseline", "ramsayville", "don reid",
            "lester", "mitch owens", "queensway", "alta vista", "ledbury",
            "herongate", "ridgemont", "ellwood", "urbandale", "kaladar",
            "sandalwood", "plesser", "clover", "featherston", "brookfield",
        ],
    },
    "canotek": {
        "label": "Ottawa Canotek",
        "address": "5303 Canotek Rd",
        "tests": ["G2", "G"],
        "queries": [
            "Ottawa Canotek road test route",
            "Canotek G2 test route",
            "Canotek G test route Ottawa",
            "Canotek driving test Ottawa",
            "5303 Canotek road test",
            "Ottawa east drive test route",
        ],
        "streets": [
            "canotek", "belfast", "ogilvie", "st joseph", "saint joseph",
            "montreal road", "blair", "innes", "cyrville", "st laurent",
            "aviation parkway", "queensway", "trim", "tenth line", "gloucester",
            "beacon hill", "shefford", "bantree", "michael", "star top",
        ],
    },
    "smithsfalls": {
        "label": "Smiths Falls",
        "address": "91 Cornelia St W",
        "tests": ["G2", "G"],
        "queries": [
            "Smiths Falls road test route",
            "Smiths Falls G test route",
            "Smiths Falls G2 test route",
            "Smiths Falls DriveTest driving test",
        ],
        "streets": [
            "cornelia", "beckwith", "chambers", "lombard", "abbott",
            "highway 15", "hwy 15", "highway 43", "hwy 43", "brockville",
            "elmsley", "jasper", "queen street", "william",
        ],
    },
    "winchester": {
        "label": "Winchester",
        "address": "12015 Dawley Dr",
        "tests": ["G2"],  # G2 only, no G road test offered here
        "queries": [
            "Winchester Ontario road test route",
            "Winchester G2 test route Ontario",
            "Winchester DriveTest road test",
        ],
        "streets": [
            "dawley", "main street", "st lawrence", "clarence", "gladstone",
            "county road 31", "county road 43", "highway 31", "mill street",
        ],
    },
}

TURN_PHRASES = [
    "turn left", "turn right", "left onto", "right onto", "left on",
    "right on", "make a left", "make a right", "go straight", "keep straight",
    "continue on", "continue straight", "merge onto", "exit onto",
    "at the lights", "at the stop sign", "u-turn", "three point turn",
    "parallel park", "roundabout", "pull over", "lane change",
]

# Test class detection. G2 checked before bare G to avoid false matches.
G2_HINTS = ["g2 test", "g2 road test", "g2 exam", "my g2", "g2 driving test",
            "passed my g2", "g2 route"]
G_HINTS = ["full g", "g test", "g road test", "g exam", "my g test",
           "passed my g", "highway test", "g2 exit"]

ISO_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")

# Other Ontario centres that bleed into generic queries. A video whose
# title names one of these and not the target centre is a different city.
OTHER_CENTRES = [
    "oshawa", "oakville", "walkerton", "toronto", "mississauga", "brampton",
    "scarborough", "etobicoke", "hamilton", "london", "kitchener", "guelph",
    "barrie", "sudbury", "windsor", "kingston", "peterborough", "newmarket",
    "burlington", "whitby", "aurora", "markham", "vaughan", "milton",
    "orangeville", "lindsay", "clarington", "port union", "downsview",
]


def wrong_city(title, centre_key, cfg):
    """True if the title names another centre's city and not this one."""
    low = (title or "").lower()
    names = [centre_key, cfg["label"].lower()]
    names += [w for w in cfg["label"].lower().split() if len(w) > 4]
    if any(n in low for n in names):
        return False
    return any(c in low for c in OTHER_CENTRES)


def iso_to_seconds(s):
    m = ISO_DUR.fullmatch(s or "")
    if not m:
        return 0
    h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


def api_get(endpoint, params):
    params = dict(params, key=YT_KEY)
    try:
        r = requests.get(f"{API}/{endpoint}", params=params, timeout=25)
        if r.status_code == 403:
            print(f"    403: {r.text[:160]}", file=sys.stderr)
            return None
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"    request failed: {e}", file=sys.stderr)
        return None


def search_videos(queries, per_query=50):
    found = {}
    for q in queries:
        data = api_get("search", {
            "q": q, "part": "snippet", "type": "video",
            "maxResults": per_query, "relevanceLanguage": "en",
        })
        if not data:
            continue
        items = data.get("items", [])
        new = 0
        for it in items:
            vid = it["id"]["videoId"]
            if vid not in found:
                found[vid] = it["snippet"]
                new += 1
        print(f"  {q!r}: {len(items)} results, {new} new")
        time.sleep(0.2)
    return found


def fetch_details(video_ids):
    out = {}
    ids = list(video_ids)
    for i in range(0, len(ids), 50):
        data = api_get("videos", {
            "id": ",".join(ids[i:i + 50]),
            "part": "contentDetails,statistics,snippet",
        })
        if not data:
            continue
        for it in data.get("items", []):
            cd, sn = it.get("contentDetails", {}), it.get("snippet", {})
            out[it["id"]] = {
                "seconds": iso_to_seconds(cd.get("duration")),
                "views": it.get("statistics", {}).get("viewCount", ""),
                "description": sn.get("description", ""),
                "channel": sn.get("channelTitle", ""),
                "tags": " ".join(sn.get("tags", []) or []),
            }
    return out


def fetch_comments(video_id, limit=40):
    """Comments often name streets the narrator skipped."""
    data = api_get("commentThreads", {
        "videoId": video_id, "part": "snippet",
        "maxResults": min(limit, 100), "order": "relevance",
        "textFormat": "plainText",
    })
    if not data:
        return ""
    texts = []
    for it in data.get("items", []):
        top = it.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
        if top.get("textDisplay"):
            texts.append(top["textDisplay"])
    return "\n".join(texts)


def fetch_transcript(video_id):
    """Manual captions preferred over auto-generated.

    youtube-transcript-api changed its interface in v1.0: the static
    list_transcripts() became an instance method list(). This handles
    both so it works whichever version is installed.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None, "not installed"

    langs = ["en", "en-CA", "en-US"]

    try:
        # v1.0+ interface
        if hasattr(YouTubeTranscriptApi, "list") and not hasattr(
                YouTubeTranscriptApi, "list_transcripts"):
            listing = YouTubeTranscriptApi().list(video_id)
        else:
            listing = YouTubeTranscriptApi.list_transcripts(video_id)

        try:
            tr, kind = listing.find_manually_created_transcript(langs), "manual"
        except Exception:
            tr, kind = listing.find_generated_transcript(langs), "auto"

        fetched = tr.fetch()
        # v1.0+ yields snippet objects with .text; older yields dicts
        parts = []
        for chunk in fetched:
            if isinstance(chunk, dict):
                parts.append(chunk.get("text", ""))
            else:
                parts.append(getattr(chunk, "text", ""))
        return " ".join(p for p in parts if p), kind

    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:60]}"


def guess_test_class(text):
    """Returns G2, G, ambiguous, or unknown. Non-G2/G needs review."""
    low = (text or "").lower()
    g2 = any(h in low for h in G2_HINTS)
    g = any(h in low for h in G_HINTS)
    if g2 and not g:
        return "G2"
    if g and not g2:
        return "G"
    if g2 and g:
        return "ambiguous"
    return "unknown"


def score(text, streets):
    """Distinct streets matter more than mention count.

    A video saying "Walkley" thirty times names one street, not thirty.
    """
    low = (text or "").lower()
    hits = sorted({s for s in streets if s in low})
    turns = sum(low.count(t) for t in TURN_PHRASES)
    return len(hits), turns, hits


def run_centre(key, want_comments=True):
    cfg = CENTRES[key]
    print(f"\n{'=' * 60}")
    print(f"{cfg['label']}  ({cfg['address']})  tests: {', '.join(cfg['tests'])}")
    print(f"{'=' * 60}")

    print(f"\nSearching, {len(cfg['queries'])} queries "
          f"({len(cfg['queries']) * 100} quota units)")
    snippets = search_videos(cfg["queries"])
    if not snippets:
        print("  nothing found")
        return None

    print(f"\nDetails for {len(snippets)} videos")
    details = fetch_details(snippets.keys())

    rows = []
    street_tally = Counter()
    tr_errors = Counter()
    skipped = 0

    for n, (vid, snip) in enumerate(snippets.items(), 1):
        d = details.get(vid, {})
        title = snip.get("title", "")

        if wrong_city(title, key, cfg):
            skipped += 1
            continue

        print(f"  [{n}/{len(snippets)}] {title[:58]}")

        tr_text, tr_kind = fetch_transcript(vid)
        if not tr_text:
            tr_errors[tr_kind] += 1
        cm_text = fetch_comments(vid) if want_comments else ""

        blob = " ".join(filter(None, [
            title, d.get("description", ""), d.get("tags", ""),
            tr_text or "", cm_text,
        ]))

        n_streets, n_turns, hits = score(blob, cfg["streets"])
        street_tally.update(hits)
        test_class = guess_test_class(title + " " + d.get("description", ""))

        if hits and tr_text:
            with open(os.path.join(TRANSCRIPT_DIR, f"{vid}.txt"),
                      "w", encoding="utf-8") as f:
                f.write(f"# {title}\n# https://youtube.com/watch?v={vid}\n")
                f.write(f"# transcript: {tr_kind}\n# streets: {', '.join(hits)}\n\n")
                f.write(tr_text + "\n")

        secs = d.get("seconds", 0)
        flags = []
        if secs and secs < 180:
            flags.append("SHORT")
        if test_class in ("unknown", "ambiguous"):
            flags.append("CLASS?")

        rows.append({
            "centre": key,
            "video_id": vid,
            "url": f"https://youtube.com/watch?v={vid}",
            "title": title,
            "channel": d.get("channel", ""),
            "published": snip.get("publishedAt", "")[:10],
            "duration_s": secs,
            "views": d.get("views", ""),
            "test_class": test_class,
            "transcript": tr_kind if tr_text else "none",
            "n_streets": n_streets,
            "n_turns": n_turns,
            "streets_found": "; ".join(hits),
            "traceable": "",
            "flags": " ".join(flags),
        })
        time.sleep(0.3)

    rows.sort(key=lambda r: (r["n_streets"], r["n_turns"]), reverse=True)

    out_path = os.path.join(OUT_DIR, f"{key}_sources.csv")
    fields = ["centre", "video_id", "url", "title", "channel", "published",
              "duration_s", "views", "test_class", "transcript", "n_streets",
              "n_turns", "streets_found", "traceable", "flags"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    with_tr = sum(1 for r in rows if r["transcript"] != "none")
    strong = sum(1 for r in rows if r["n_streets"] >= 3)
    by_class = Counter(r["test_class"] for r in rows)

    print(f"\n  wrote {out_path}")
    print(f"    videos kept:       {len(rows)}")
    print(f"    skipped, other city: {skipped}")
    print(f"    with transcript:   {with_tr}")
    print(f"    naming 3+ streets: {strong}   <- read these first")
    print(f"    by class:          {dict(by_class)}")
    if tr_errors:
        print("    transcript failures:")
        for reason, count in tr_errors.most_common(5):
            print(f"      {count:3d}  {reason}")
    if street_tally:
        top = ", ".join(f"{s}({c})" for s, c in street_tally.most_common(8))
        print(f"    top streets:       {top}")

    return {"centre": key, "total": len(rows), "strong": strong,
            "with_transcript": with_tr}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--centre", choices=sorted(CENTRES))
    g.add_argument("--all", action="store_true")
    ap.add_argument("--no-comments", action="store_true",
                    help="skip comments, faster and cheaper")
    args = ap.parse_args()

    if not YT_KEY:
        sys.exit("No YOUTUBE_API_KEY in .env")

    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

    targets = sorted(CENTRES) if args.all else [args.centre]
    summaries = [s for s in
                 (run_centre(t, not args.no_comments) for t in targets) if s]

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    for s in summaries:
        print(f"  {s['centre']:14s} {s['total']:3d} videos, "
              f"{s['strong']:2d} naming 3+ streets")
    print(f"{'=' * 60}")
    print("\nNow open the CSVs and fill `traceable` by hand: y or n.")
    print("y only if the source gives an ORDERED sequence of turns with")
    print("street names. Naming six streets in no order is n.")
    print("\nThat count, per centre per test class, is the number the whole")
    print("consensus design depends on. Roughly 4-6 traces per centre per")
    print("class is where clustering starts to mean anything.")


if __name__ == "__main__":
    main()