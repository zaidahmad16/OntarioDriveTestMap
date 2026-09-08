#!/usr/bin/env python3
"""
count_sources.py — find candidate videos for a DriveTest centre.

DESTINATION: acquisition/count_sources.py

Searches YouTube, filters to plausible road-test recordings, and scores
each by how much route information it might contain.

Three changes from the Walkley-only version:

  CENTRE IS A PARAMETER, not a constant. Search terms, aliases, the
  neighbouring centres to exclude, and the output path all come from
  the CENTRES table below.

  THE `centre` COLUMN NO LONGER LIES. It used to stamp the search term
  onto every row, so two Canotek videos and one Smiths Falls video were
  labelled walkley. Now `centre_searched` records what was asked for and
  `centre_evidence` records what the title, description or streets
  actually support. Anything downstream should read the second.

  THE GAZETTEER COMES FROM osm.db, not a hardcoded list. The old list
  had roughly ninety Walkley-area streets and almost nothing east of the
  canal, so `streets_found` would have come back empty for most Canotek
  videos. Streets are now read from the extract within a radius of the
  centre.

Usage:
    python3 count_sources.py --centre walkley
    python3 count_sources.py --centre canotek --max-results 120
    python3 count_sources.py --centre canotek --no-transcripts
"""

import argparse
import csv
import os
import re
import sqlite3
import sys
import time
from collections import Counter

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

API = "https://www.googleapis.com/youtube/v3"

# --------------------------------------------------------------------------
# Per-centre configuration.
#
# `aliases` are what people actually call the place in a title. `siblings`
# are the other centres in the same city, used to reject a video that is
# clearly about one of them — the failure that put Canotek and Smiths Falls
# videos in the Walkley file.
# --------------------------------------------------------------------------

CENTRES = {
    "walkley": {
        "name": "Ottawa Walkley",
        "lat": 45.376146, "lon": -75.647589,
        "aliases": ["walkley"],
        "siblings": ["canotek", "smiths falls", "smithsfalls", "winchester",
                     "kanata", "almonte", "carleton place"],
        "queries": [
            "Ottawa Walkley G2 road test route",
            "Ottawa Walkley G road test route",
            "Walkley DriveTest Ottawa test route dashcam",
            "Ottawa Walkley driving test full route",
        ],
    },
    "canotek": {
        "name": "Ottawa Canotek",
        # approximate — 5303 Canotek Rd. Verified against osm.db at run
        # time if the centre is tagged amenity=driver_testing there.
        "lat": 45.4497, "lon": -75.5744,
        # "gloucester" removed: Walkley Road is also in the former City
        # of Gloucester, so the alias let two Walkley videos through the
        # sibling filter and into the verified Canotek set.
        "aliases": ["canotek"],
        "siblings": ["walkley", "smiths falls", "smithsfalls", "winchester",
                     "kanata", "almonte", "carleton place"],
        # Two of the genuine Canotek dashcam videos have Arabic and
        # French titles, so English-only queries under-collect here.
        "queries": [
            "Ottawa Canotek G2 road test route",
            "Ottawa Canotek G road test route",
            "Canotek DriveTest Ottawa test route dashcam",
            "Ottawa Canotek driving test full route",
            "Canotek road test Blair",
            "Ottawa east end driving test route",
            "Canotek G2 test Innes Ogilvie",
            "امتحان القيادة أوتاوا كانوتيك",
            "examen de conduite Ottawa Canotek",
        ],
    },
    "smithsfalls": {
        "name": "Smiths Falls",
        "lat": 44.9012, "lon": -76.0214,
        "aliases": ["smiths falls", "smith falls", "smithsfalls"],
        "siblings": ["walkley", "canotek", "winchester", "ottawa"],
        "queries": [
            "Smiths Falls G2 road test route",
            "Smiths Falls G road test route",
            "Smiths Falls DriveTest route dashcam",
        ],
    },
    "winchester": {
        "name": "Winchester",
        "lat": 45.0847, "lon": -75.3495,
        "aliases": ["winchester"],
        "siblings": ["walkley", "canotek", "smiths falls", "ottawa"],
        "queries": [
            "Winchester Ontario G2 road test route",
            "Winchester DriveTest road test route",
        ],
    },
}

# Titles that are about the test but are not a recording of one.
BAD_TITLE = re.compile(
    r"\b(vlog|reaction|prank|fail compilation|asmr|shorts?|"
    r"how to book|booking|appointment|waiting|line ?up|queue)\b",
    re.IGNORECASE)

GOOD_TITLE = re.compile(
    r"\b(route|test|drive|driving|dashcam|dash cam|road test|"
    r"g2|full|passed|exam)\b", re.IGNORECASE)

# A title advertising a route map overlay. Those videos read every street
# on screen at once rather than as the car passes them, so they need
# flagging before OCR rather than filtering afterwards.
MAP_TITLE = re.compile(
    r"\b(route map|google map|map of|complete route|route guide|"
    r"guide|overlay|tips)\b", re.IGNORECASE)

TURN_PHRASE = re.compile(
    r"\b(turn(?:ed|ing|s)?|merge[sd]?|exit|left|right|straight)\b",
    re.IGNORECASE)

# test class from the TITLE only. Descriptions list both classes for SEO,
# which is what produced 21 "ambiguous" rows on the first Walkley run.
G2_TITLE = re.compile(r"\bg\s?2\b", re.IGNORECASE)
G_TITLE = re.compile(r"\bg\b(?!\s?2)|full\s+g\b", re.IGNORECASE)


def load_gazetteer(db, lat, lon, radius_deg=0.06):
    """Street names near a centre, from the OSM extract.

    Replaces the hardcoded ninety-entry Walkley list. Without this,
    `streets_found` is empty for any centre the list was not written for.
    """
    if not os.path.exists(db):
        print(f"  ! {db} not found — street detection disabled")
        return set()
    con = sqlite3.connect(db)
    names = set()
    for b, f in con.execute(
            "SELECT DISTINCT js.base, js.full FROM junction_streets js "
            "JOIN junctions j ON j.node_id = js.node_id "
            "WHERE ABS(j.lat-?) < ? AND ABS(j.lon-?) < ?",
            (lat, radius_deg * 0.72, lon, radius_deg)):
        for v in (b, f):
            if v and len(v) >= 5:
                names.add(v)
    con.close()
    return names


# Street names that are also ordinary words. Matching these produces
# constant false positives — "looking good", "green light", "go back".
FALSE_FRIENDS = {
    "first", "second", "third", "fourth", "cross", "toronto", "back",
    "front", "center", "centre", "green", "good", "little", "middle",
    "park", "ready", "forward", "best", "line", "school", "ontario",
    "canada", "station", "market", "queen", "king", "church", "main",
    "river", "lake", "hill", "bridge", "north", "south", "east", "west",
}


def centre_coords(db, cfg):
    """Prefer the coordinate in osm.db over the hardcoded one."""
    if not os.path.exists(db):
        return cfg["lat"], cfg["lon"]
    con = sqlite3.connect(db)
    try:
        for cid, lat, lon in con.execute(
                "SELECT centre_id, lat, lon FROM centres"):
            if any(a.replace(" ", "") in (cid or "").lower()
                   for a in cfg["aliases"]):
                con.close()
                return lat, lon
    except sqlite3.OperationalError:
        pass
    con.close()
    return cfg["lat"], cfg["lon"]


def search(key, query, max_results):
    """Paged search. Returns video ids."""
    ids, token = [], None
    while len(ids) < max_results:
        p = {"key": key, "part": "id", "q": query, "type": "video",
             "maxResults": min(50, max_results - len(ids)),
             "relevanceLanguage": "en"}
        if token:
            p["pageToken"] = token
        r = requests.get(f"{API}/search", params=p, timeout=30)
        if r.status_code != 200:
            print(f"  search failed {r.status_code}: {r.text[:160]}")
            break
        d = r.json()
        ids += [i["id"]["videoId"] for i in d.get("items", [])
                if i.get("id", {}).get("videoId")]
        token = d.get("nextPageToken")
        if not token:
            break
        time.sleep(0.2)
    return ids


def details(key, ids):
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        r = requests.get(f"{API}/videos", params={
            "key": key, "id": ",".join(chunk),
            "part": "snippet,contentDetails,statistics"}, timeout=30)
        if r.status_code != 200:
            print(f"  details failed {r.status_code}")
            continue
        out += r.json().get("items", [])
        time.sleep(0.2)
    return out


def duration_s(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def attribute_centre(title, desc, cfg, streets):
    """What the video is actually about, as opposed to what was searched.

    Returns (evidence, reason). Evidence is the centre id when something
    supports it, "other:<name>" when a sibling centre is named, and
    "unverified" when nothing does.
    """
    blob = f"{title} {desc}".lower()

    for sib in cfg["siblings"]:
        if sib in blob and not any(a in blob for a in cfg["aliases"]):
            return f"other:{sib}", "sibling centre named in title/description"

    if any(a in title.lower() for a in cfg["aliases"]):
        return cfg["id"], "centre named in title"
    if any(a in blob for a in cfg["aliases"]):
        return cfg["id"], "centre named in description"
    if len(streets) >= 2:
        return cfg["id"], f"{len(streets)} local streets named"
    return "unverified", "no centre named, no local streets"


def transcript(vid):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        f = api.fetch(vid, languages=["en", "en-CA", "en-US"])
        return " ".join(s.text for s in f), "auto"
    except Exception as e:
        n = type(e).__name__
        if "Disabled" in n:
            return "", "none"
        return "", n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--centre", required=True, choices=sorted(CENTRES))
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--out", default=None,
                    help="default: ../data/raw/<centre>_sources.csv")
    ap.add_argument("--max-results", type=int, default=100)
    ap.add_argument("--radius", type=float, default=0.06)
    ap.add_argument("--no-transcripts", action="store_true")
    ap.add_argument("--min-seconds", type=int, default=180)
    args = ap.parse_args()

    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY not set (check .env)")
        return 1

    cfg = dict(CENTRES[args.centre])
    cfg["id"] = args.centre
    lat, lon = centre_coords(args.db, cfg)
    out_path = args.out or f"../data/raw/{args.centre}_sources.csv"

    gaz = load_gazetteer(args.db, lat, lon, args.radius) - FALSE_FRIENDS
    print(f"centre: {cfg['name']}  ({lat:.5f}, {lon:.5f})")
    print(f"gazetteer: {len(gaz):,} streets within ~{args.radius*111:.0f} km")
    print(f"queries: {len(cfg['queries'])}\n")

    seen, ids = set(), []
    for q in cfg["queries"]:
        found = search(key, q, args.max_results)
        new = [v for v in found if v not in seen]
        seen.update(new)
        ids += new
        print(f"  {len(found):3d} results, {len(new):3d} new  |  {q}")
    print(f"\n{len(ids)} unique videos\n")

    rows, kept, rejected = [], 0, Counter()
    for it in details(key, ids):
        sn = it["snippet"]
        vid = it["id"]
        title = sn.get("title", "")
        desc = sn.get("description", "")
        dur = duration_s(it.get("contentDetails", {}).get("duration"))

        if BAD_TITLE.search(title):
            rejected["title looks non-route"] += 1
            continue
        if not GOOD_TITLE.search(title):
            rejected["title lacks route words"] += 1
            continue
        if dur < args.min_seconds:
            rejected["too short"] += 1
            continue

        blob = f"{title} {desc}".lower()
        streets = sorted({g for g in gaz
                          if re.search(r"\b" + re.escape(g) + r"\b", blob)})

        evidence, why = attribute_centre(title, desc, cfg, streets)
        if evidence.startswith("other:"):
            rejected[f"about {evidence[6:]}"] += 1
            continue

        text, tstate = "", "skipped"
        n_streets, n_turns = len(streets), 0
        if not args.no_transcripts:
            text, tstate = transcript(vid)
            if text:
                low = " " + re.sub(r"[^\w\s]", " ", text.lower()) + " "
                tstreets = {g for g in gaz if f" {g} " in low}
                streets = sorted(set(streets) | tstreets)
                n_streets = len(streets)
                n_turns = len(TURN_PHRASE.findall(text))

        cls = ("G2" if G2_TITLE.search(title) else
               "G" if G_TITLE.search(title) else "unknown")

        flags = []
        if MAP_TITLE.search(title):
            flags.append("MAP")
        if evidence == "unverified":
            flags.append("UNVERIFIED")

        rows.append({
            "centre_searched": args.centre,
            "centre_evidence": evidence,
            "evidence_reason": why,
            "video_id": vid,
            "url": f"https://youtube.com/watch?v={vid}",
            "title": title,
            "channel": sn.get("channelTitle", ""),
            "published": (sn.get("publishedAt") or "")[:10],
            "duration_s": dur,
            "views": it.get("statistics", {}).get("viewCount", ""),
            "test_class": cls,
            "transcript": tstate,
            "n_streets": n_streets,
            "n_turns": n_turns,
            "streets_found": "; ".join(streets),
            "flags": "|".join(flags),
        })
        kept += 1

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["centre_searched"])
        w.writeheader()
        w.writerows(rows)

    print(f"kept {kept}, rejected {sum(rejected.values())}")
    for k, v in rejected.most_common():
        print(f"   {k}: {v}")

    if rows:
        print(f"\ntest class: {Counter(r['test_class'] for r in rows)}")
        print(f"transcripts: {Counter(r['transcript'] for r in rows)}")
        print(f"attribution: {Counter(r['centre_evidence'] for r in rows)}")
        named = [r for r in rows if r["n_streets"] > 0]
        print(f"naming >=1 local street: {len(named)}")
        print(f"naming >=3: {len([r for r in rows if r['n_streets'] >= 3])}")
        print("\nbest candidates:")
        for r in sorted(rows, key=lambda x: -x["n_streets"])[:10]:
            print(f"  {r['video_id']}  {r['duration_s']//60:3d}min  "
                  f"{r['n_streets']:2d} streets  {r['title'][:52]}")

    print(f"\nwrote {out_path}")
    print("\nThe centre_evidence column is the one to trust. centre_searched")
    print("is only what was asked for — stamping it as fact is what put")
    print("Canotek and Smiths Falls videos in the Walkley file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())