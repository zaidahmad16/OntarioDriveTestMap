#!/usr/bin/env python3
"""
process_reddit.py — turn a manually collected Reddit CSV into the project's
intermediate form, plus the numbers needed to settle whether text sources
carry route geometry.

Input columns expected:
  thread_key, post_id, subreddit, record_type, title, flair, post_author,
  post_age, post_body, num_comments, comment_id, comment_author,
  comment_age, comment_body, edited, status, source_file

Outputs (into --outdir):
  reddit_records.csv        one row per post/comment, scored, no usernames
  reddit_traces.json        intermediate-form candidates (ordered turns only)
  reddit_failpoints.csv     fail-point mentions, for the fail_points table
  street_candidates.csv     street-like strings NOT in the gazetteer
  summary.txt               the metrics that go in Report Material

Privacy: usernames are dropped at ingest and replaced with a salted hash so
correlated-source detection stays possible without storing the name.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import pandas as pd

# --------------------------------------------------------------------------
# Gazetteer. Ottawa-area streets relevant to the four launch centres.
# Kept flat and lowercase; matching is done on a normalised body.
# --------------------------------------------------------------------------

GAZETTEER = [
    # Walkley / Hunt Club / Alta Vista
    "walkley", "albion", "conroy", "heron", "bank", "riverside", "hunt club",
    "airport parkway", "alta vista", "ledbury", "herongate", "ridgemont",
    "ellwood", "urbandale", "kaladar", "sandalwood", "plesser", "clover",
    "featherston", "brookfield", "russell", "don reid", "lester", "hawthorne",
    "smyth", "pleasant park", "kilborn", "chomley", "othello", "canterbury",
    "st laurent", "saint laurent", "coronation", "trainyards", "belfast",
    "industrial", "terminal", "sheffield", "johnston", "data centre",
    # Canotek / east end
    "canotek", "innes", "cyrville", "ogilvie", "blair", "montreal road",
    "aviation parkway", "shefford", "bathgate", "labrie", "michael",
    "startop", "belcourt", "orleans", "jeanne d'arc", "tenth line",
    # South / airport
    "lester road", "leitrim", "limebank", "bowesville", "uplands", "brookdale",
    "hunt club road", "mitch owens", "rideau", "prince of wales", "fisher",
    "meadowlands", "merivale", "baseline", "woodroffe", "greenbank",
    # Highways
    "417", "416", "queensway", "highway 7", "highway 15", "highway 43",
    # Smiths Falls / Winchester
    "beckwith", "cornelia", "lombard", "chambers", "elmsley", "abbott",
    "county road 31", "main street", "st lawrence",
    # discovered from this Reddit corpus, absent from the YouTube gazetteer.
    # These are the residential G2 streets, which is what text sources
    # turned out to be good for.
    "cedarwood", "baycrest", "heatherington", "fairlea", "briar hill",
    "amberdale", "cahill", "mccarthy", "paul anka", "loyola", "eastvale",
    "sieveright", "delta", "kaladar", "pleasant park", "erie", "clementine",
]

# Not streets, however the regex captures them.
NOT_A_STREET = {
    "the median", "the highway", "the road", "the street", "the lot",
}

# Not streets either, but they ARE locations, and every route in the
# corpus starts and ends at one. Normalised to a single token so the
# snapper can resolve them against the centres table rather than
# hunting for a street junction that does not exist.
CENTRE_TOKENS = {
    "drivetest", "drive test", "walkley drivetest", "test centre",
    "test center", "testcentre", "the centre", "the center", "centre",
    "center", "test site", "drivetest centre", "drivetest center",
}

# Common spelling variants seen in casual writing. Applied before snapping.
ALIASES = {
    "walkey": "walkley", "wakley": "walkley", "walkly": "walkley",
    "huntclub": "hunt club", "hunt club rd": "hunt club",
    "airport pkwy": "airport parkway", "the parkway": "airport parkway",
    "parkway": "airport parkway", "hetherington": "heatherington",
    "loyala": "loyola", "montreal": "montreal road", "st laurent": "saint laurent",
}

STREET_SUFFIX = (
    r"(?:Rd|Road|St|Street|Ave|Avenue|Dr|Drive|Blvd|Boulevard|Pkwy|Parkway|"
    r"Cres|Crescent|Way|Lane|Ln|Crt|Court|Terrace|Terr|Circle|Cir|Place|Pl)"
)

# A capitalised name followed by a street suffix. Catches streets the
# gazetteer does not know about yet, which is how the gazetteer grows.
STREET_LIKE = re.compile(
    r"\b([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2})\s+" + STREET_SUFFIX + r"\b"
)

# --------------------------------------------------------------------------
# Turn extraction
# --------------------------------------------------------------------------

DIRECTION = r"(left|right|straight)"

# "turn left onto Heron", "left on Bank St", "turned right at Walkley"
TURN_WITH_STREET = re.compile(
    r"\b(?:turn(?:ed|ing|s)?\s+|go\s+|head(?:ed|ing)?\s+|make\s+a\s+|took\s+a\s+)?"
    + DIRECTION +
    r"\s+(?:turn\s+)?(?:on|onto|at|in\s?to|into|down|to|towards?|toward)\s+(?:the\s+)?"
    r"([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2})",
    re.IGNORECASE,
)

# "continue on X", "merge onto X", "stay on X" — straight-ahead movement
STRAIGHT_WITH_STREET = re.compile(
    r"\b(?:continue|merge|stay|proceed|drive|get)\s+(?:straight\s+)?"
    r"(?:on|onto|along)\s+(?:the\s+)?"
    r"([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2})",
    re.IGNORECASE,
)

# Bare turn language with no street attached. Counted separately, because
# this is the manoeuvre-advice signal, not the route-geometry signal.
BARE_TURN = re.compile(
    r"\b(?:turn(?:ed|ing|s)?|merge[sd]?|lane\s+change)\s+" + DIRECTION + r"\b",
    re.IGNORECASE,
)

# Words that look like a street in the regex but are not one.
STOPWORDS = {
    "the", "a", "an", "it", "me", "my", "you", "your", "him", "her", "them",
    "this", "that", "there", "here", "then", "when", "where", "which", "who",
    "and", "but", "so", "if", "of", "to", "in", "on", "at", "for", "with",
    "side", "lane", "turn", "turns", "road", "street", "way", "exit", "light",
    "lights", "red", "green", "stop", "sign", "signal", "test", "route",
    "examiner", "instructor", "car", "traffic", "median", "highway", "hwy",
    "time", "one", "two", "some", "any", "all", "each", "other", "another",
    "residential", "main", "same", "first", "second", "third", "last", "next",
    "left", "right", "straight", "north", "south", "east", "west", "centre",
    "center", "parking", "lot", "school", "zone", "speed", "limit", "day",
    "today", "yesterday", "everything", "nothing", "something", "anything",
    "roads", "streets", "around", "every", "until", "before", "after",
    "back", "past", "toward", "towards", "through", "onto", "into", "just",
    "divider", "intersection", "intersections", "corner", "block", "area",
    "medium", "farthest", "screwed", "pass", "check", "don't", "dont",
}

# --------------------------------------------------------------------------
# Fail points
# --------------------------------------------------------------------------

FAIL_PHRASE = re.compile(
    r"\b(?:auto(?:matic)?\s+fail|failed?\s+(?:for|on|because|due\s+to|at|from)|"
    r"instant\s+fail|critical\s+error|major\s+(?:error|mistake)|"
    r"docked|deduct(?:ed|ion)|lost\s+(?:marks|points))\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Centres
# --------------------------------------------------------------------------

CENTRES = {
    "walkley": ["walkley"],
    "canotek": ["canotek"],
    "smithsfalls": ["smiths falls", "smith falls", "smithsfalls"],
    "winchester": ["winchester"],
    # tracked but not launch centres, so volume elsewhere is visible
    "other_ottawa": ["ottawa"],
    "toronto_area": ["etobicoke", "downsview", "port union", "metro east",
                     "oshawa", "brampton", "mississauga", "newmarket",
                     "scarborough", "toronto"],
}

TEST_CLASS_G2 = re.compile(r"\bg2\b", re.IGNORECASE)
TEST_CLASS_G = re.compile(r"\bg\b(?!\s*[12])|full\s+g\b", re.IGNORECASE)

AGE = re.compile(r"^\s*(\d+)\s*(d|w|mo|y|h|m)\b", re.IGNORECASE)
AGE_DAYS = {"h": 0, "m": 0, "d": 1, "w": 7, "mo": 30, "y": 365}


# --------------------------------------------------------------------------


def anon(name, salt):
    """Stable pseudonym. The username itself is never written out."""
    if not name or pd.isna(name) or str(name).strip() in ("", "[deleted]"):
        return ""
    h = hashlib.sha256((salt + str(name).strip().lower()).encode()).hexdigest()
    return "a_" + h[:12]


def age_to_date(age_str, scraped_on):
    """'7mo' or '9d ago' -> approximate date. Coarse by nature."""
    if not age_str or pd.isna(age_str):
        return None
    m = AGE.match(str(age_str))
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return scraped_on - timedelta(days=n * AGE_DAYS.get(unit, 1))


SUFFIX_WORDS = {
    "rd", "road", "st", "street", "ave", "avenue", "dr", "drive", "blvd",
    "boulevard", "pkwy", "parkway", "cres", "crescent", "way", "lane", "ln",
    "crt", "court", "terrace", "terr", "circle", "cir", "place", "pl",
}


def clean_street(raw):
    """Normalise a captured street candidate, or return None if it is junk.

    The regex over-captures, because casual writing runs straight from the
    street name into the rest of the sentence ("right on Walkley you pass
    the..."). So truncate at the first token that cannot be part of a street
    name, then alias and snap.
    """
    if not raw:
        return None
    s = re.sub(r"\s+", " ", raw).strip().strip(".,;:!?-'\"")
    if not s:
        return None

    # Centre phrases are checked BEFORE any street cleaning. "test centre"
    # would otherwise truncate to nothing, since "test" is a stopword, and
    # the route would lose its final waypoint. A centre is a location, not
    # a street, so street-name rules should not apply to it.
    low = s.lower().strip()
    if low in CENTRE_TOKENS or any(
            low == f"{a} {b}" for a in ("the", "a") for b in CENTRE_TOKENS):
        return "@centre"

    words = s.lower().split()

    # Truncate at the first token that cannot belong to a street name.
    kept = []
    for w in words:
        w = w.strip(".,;:!?'\"")
        if not w:
            break
        if w in SUFFIX_WORDS:
            kept.append(w)
            break                      # a suffix ends the name
        if w in STOPWORDS:
            break
        if len(kept) >= 3:
            break
        kept.append(w)

    if not kept:
        return None

    # Drop a trailing suffix word so "baycrest dr" and "baycrest" unify —
    # unless the suffix is part of the real name ("airport parkway").
    if len(kept) > 1 and kept[-1] in SUFFIX_WORDS:
        if " ".join(kept) not in GAZETTEER:
            kept = kept[:-1]

    name = " ".join(kept)
    name = ALIASES.get(name, name)

    if name in CENTRE_TOKENS:
        return "@centre"          # resolved later against the centres table
    if name in NOT_A_STREET or name in STOPWORDS:
        return None
    if len(name) < 3 or name.isdigit() and len(name) < 3:
        return None

    # Fuzzy-snap to the gazetteer. Catches misspellings the alias list
    # does not enumerate. Conservative cutoff; a miss is better than a
    # wrong street, since a wrong street looks like consensus later.
    if name not in GAZETTEER:
        import difflib
        near = difflib.get_close_matches(name, GAZETTEER, n=1, cutoff=0.86)
        if near:
            name = near[0]

    return name


def in_gazetteer(street):
    if not street:
        return False
    return any(g == street or g in street or street in g for g in GAZETTEER)


# A single comment can hold several labelled routes. "Route 1:", "Route 2:",
# sometimes bolded. Treating them as one trace produces an impossible path
# that doubles back on itself, so split before extracting turns.
ROUTE_MARKER = re.compile(
    r"(?:^|\s)\**\s*(?:route|rt\.?)\s*#?\s*(\d+)\s*\**\s*:?",
    re.IGNORECASE | re.MULTILINE)


def split_routes(text):
    """-> [(label, segment_text), ...]. One entry if no markers found."""
    if not text:
        return [("", "")]
    marks = list(ROUTE_MARKER.finditer(text))
    if len(marks) < 2:          # one marker is a mention, not a list
        return [("", text)]
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        seg = text[m.end():end].strip()
        if seg:
            out.append((f"route{m.group(1)}", seg))
    return out or [("", text)]


# Routes end at the centre, and often say so without a direction:
# "Back to test centre", "return to the DriveTest". No direction word, so
# the turn regex cannot see it, and the route stops one junction early.
RETURN_TO_CENTRE = re.compile(
    r"\b(?:back|return(?:ed|ing)?|head(?:ed|ing)?\s+back|finish(?:ed)?)\s+"
    r"(?:to|at|into|in\s?to)\s+(?:the\s+)?"
    r"(drive\s?test|test\s+cent(?:re|er)|cent(?:re|er)|test\s+site)",
    re.IGNORECASE)


# Routes end at the centre, and often say so without a direction:
# "Back to test centre", "return to the DriveTest". No direction word, so
# the turn regex cannot see it, and the route stops one junction early.
RETURN_TO_CENTRE = re.compile(
    r"\b(?:back|return(?:ed|ing)?|head(?:ed|ing)?\s+back|finish(?:ed)?)\s+"
    r"(?:to|at|into|in\s?to)\s+(?:the\s+)?"
    r"(drive\s?test|test\s+cent(?:re|er)|cent(?:re|er)|test\s+site)",
    re.IGNORECASE)


def extract_turns(text):
    """Ordered list of {direction, street}, in the order they appear.

    Only turns with a street attached count. A bare 'turn left' constrains
    no geometry and is deliberately excluded from the sequence.
    """
    if not text:
        return []
    hits = []
    for m in TURN_WITH_STREET.finditer(text):
        st = clean_street(m.group(2))
        if st:
            hits.append((m.start(), m.group(1).lower(), st))
    for m in STRAIGHT_WITH_STREET.finditer(text):
        st = clean_street(m.group(1))
        if st:
            hits.append((m.start(), "straight", st))
    for m in RETURN_TO_CENTRE.finditer(text):
        hits.append((m.start(), "straight", "@centre"))

    for m in RETURN_TO_CENTRE.finditer(text):
        hits.append((m.start(), "straight", "@centre"))

    hits.sort(key=lambda x: x[0])

    turns = []
    for _, direction, street in hits:
        # Collapse only IMMEDIATE repetition (the same phrase matched twice).
        # A route legitimately returns to the same street several times, so
        # global dedup would flatten a loop into a line.
        if turns and turns[-1] == {"direction": direction, "street": street}:
            continue
        turns.append({"direction": direction, "street": street})
    return turns


def find_streets(text):
    """Gazetteer hits plus regex street-like strings."""
    if not text:
        return set(), set()
    low = text.lower()
    known = {g for g in GAZETTEER if re.search(r"\b" + re.escape(g) + r"\b", low)}
    candidates = set()
    for m in STREET_LIKE.finditer(text):
        st = clean_street(m.group(1))
        if st and not in_gazetteer(st):
            candidates.add(st)
    return known, candidates


def guess_class(*texts):
    blob = " ".join(t for t in texts if t and not pd.isna(t))
    g2 = bool(TEST_CLASS_G2.search(blob))
    g = bool(TEST_CLASS_G.search(blob))
    if g2 and g:
        return "ambiguous"
    if g2:
        return "G2"
    if g:
        return "G"
    return "unknown"


def guess_centre(*texts):
    blob = " ".join(t for t in texts if t and not pd.isna(t)).lower()
    found = []
    for centre, keys in CENTRES.items():
        if any(k in blob for k in keys):
            found.append(centre)
    # a named launch centre wins over the generic ottawa bucket
    launch = [c for c in found if c in ("walkley", "canotek",
                                        "smithsfalls", "winchester")]
    if launch:
        return "|".join(launch)
    return "|".join(found) if found else "unknown"


def reliability(n_turns, n_streets, record_type):
    """Design-doc weights, applied to what the text actually contains.

    0.5  detailed post with an ordered turn sequence
    0.2  passing mention naming a couple of streets
    0.0  no route geometry — kept for fail points only
    """
    if n_turns >= 3:
        return 0.5
    if n_turns >= 2:
        return 0.35
    if n_streets >= 2:
        return 0.2
    if n_streets == 1:
        return 0.1
    return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--scraped-on", default="2026-08-30")
    ap.add_argument("--salt", default="ontarioroadtestmap")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    scraped_on = date.fromisoformat(args.scraped_on)

    df = pd.read_csv(args.csv)
    df = df.where(pd.notna(df), None)

    # Thread context. A comment saying "I turned left on Baycrest" carries no
    # centre name; the centre is stated in the post it replies to. Build a
    # per-thread blob once and let comments inherit from it.
    thread_ctx = {}
    for _, r in df.iterrows():
        key = r["thread_key"]
        blob = " ".join(str(x) for x in
                        (r["title"], r["flair"], r["post_body"]) if x)
        if blob and (key not in thread_ctx or len(blob) > len(thread_ctx[key])):
            thread_ctx[key] = blob

    records = []
    street_candidates = Counter()
    failpoints = []

    for _, r in df.iterrows():
        is_comment = r["record_type"] == "comment"
        body = r["comment_body"] if is_comment else r["post_body"]
        body = "" if body is None else str(body)
        title = "" if r["title"] is None else str(r["title"])
        author = r["comment_author"] if is_comment else r["post_author"]
        age = r["comment_age"] if is_comment else r["post_age"]

        # A comment row repeats the parent post body in post_body. Only the
        # comment's own text is that comment's evidence.
        turns = extract_turns(body)
        known, cand = find_streets(body)
        street_candidates.update(cand)

        n_bare = len(BARE_TURN.findall(body))
        has_fail = bool(FAIL_PHRASE.search(body))

        rec = {
            "record_id": (r["comment_id"] if is_comment else r["post_id"]),
            "thread_key": r["thread_key"],
            "subreddit": r["subreddit"],
            "record_type": r["record_type"],
            "author_hash": anon(author, args.salt),
            "observed_at": (age_to_date(age, scraped_on).isoformat()
                            if age_to_date(age, scraped_on) else ""),
            "centre": guess_centre(title, body, r["flair"],
                                   thread_ctx.get(r["thread_key"], "")),
            "test_class": guess_class(title, r["flair"], body,
                                      thread_ctx.get(r["thread_key"], "")),
            "n_turns_with_street": len(turns),
            "n_bare_turns": n_bare,
            "n_streets_known": len(known),
            "n_streets_new": len(cand),
            "streets": ";".join(sorted(known)),
            "turns": json.dumps(turns, ensure_ascii=False),
            "has_fail_language": has_fail,
            "body_chars": len(body),
            "status": r["status"] or "",
        }
        rec["_body"] = body
        rec["reliability"] = reliability(len(turns), len(known),
                                         r["record_type"])
        rec["traceable"] = "yes" if len(turns) >= 2 else (
            "partial" if len(turns) == 1 or len(known) >= 2 else "no")
        records.append(rec)

        if has_fail:
            failpoints.append({
                "record_id": rec["record_id"],
                "centre": rec["centre"],
                "test_class": rec["test_class"],
                "observed_at": rec["observed_at"],
                "streets": rec["streets"],
                "excerpt_chars": len(body),
            })

    out = pd.DataFrame(records).drop(columns=["_body"])
    out.to_csv(os.path.join(args.outdir, "reddit_records.csv"), index=False)

    # ---- intermediate form -------------------------------------------------
    traces = []
    for rec in records:
        # split first: one record can hold several labelled routes
        segs = split_routes(rec.get("_body", ""))
        if len(segs) > 1:
            for label, seg in segs:
                st = extract_turns(seg)
                if len(st) >= 2:
                    traces.append({
                        "source_id": f"reddit:{rec['record_id']}#{label}",
                        "centre_id": rec["centre"],
                        "test_class": rec["test_class"],
                        "reliability": rec["reliability"],
                        "observed_at": rec["observed_at"],
                        "author_hash": rec["author_hash"],
                        "turns": st,
                    })
            continue
        turns = json.loads(rec["turns"])
        if len(turns) < 2:
            continue
        traces.append({
            "source_id": f"reddit:{rec['record_id']}",
            "centre_id": rec["centre"],
            "test_class": rec["test_class"],
            "reliability": rec["reliability"],
            "observed_at": rec["observed_at"],
            "author_hash": rec["author_hash"],
            "turns": turns,
        })
    with open(os.path.join(args.outdir, "reddit_traces.json"), "w") as f:
        json.dump(traces, f, indent=2, ensure_ascii=False)

    pd.DataFrame(failpoints).to_csv(
        os.path.join(args.outdir, "reddit_failpoints.csv"), index=False)

    with open(os.path.join(args.outdir, "street_candidates.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "count"])
        for s, n in street_candidates.most_common():
            w.writerow([s, n])

    # ---- summary -----------------------------------------------------------
    lines = []
    A = lines.append
    A(f"Input: {args.csv}")
    A(f"Rows: {len(out)}  threads: {out.thread_key.nunique()}  "
      f"authors: {out[out.author_hash != ''].author_hash.nunique()}")
    A("")
    A("-- traceability (the number that matters) --")
    for k, v in out.traceable.value_counts().items():
        A(f"  {k:8s} {v:5d}   ({v / len(out) * 100:.1f}%)")
    A(f"  records with >=2 turns naming a street: "
      f"{(out.n_turns_with_street >= 2).sum()}")
    A(f"  records with >=3 turns naming a street: "
      f"{(out.n_turns_with_street >= 3).sum()}")
    A(f"  records with bare turn language only:   "
      f"{((out.n_bare_turns > 0) & (out.n_turns_with_street == 0)).sum()}")
    A("")
    A("-- centre attribution --")
    for k, v in out.centre.value_counts().head(12).items():
        A(f"  {k:28s} {v:5d}")
    A("")
    A("-- launch centres, traceable records --")
    for c in ("walkley", "canotek", "smithsfalls", "winchester"):
        sub = out[out.centre.str.contains(c, na=False)]
        tr = sub[sub.n_turns_with_street >= 2]
        A(f"  {c:12s} rows {len(sub):4d}   traceable {len(tr):3d}   "
          f"total weight {sub.reliability.sum():.2f}")
    A("")
    A("-- test class --")
    for k, v in out.test_class.value_counts().items():
        A(f"  {k:10s} {v:5d}")
    A("")
    A("-- street mentions, gazetteer --")
    tally = Counter()
    for s in out.streets:
        if s:
            tally.update(s.split(";"))
    for s, n in tally.most_common(25):
        A(f"  {s:22s} {n:4d}")
    A("")
    A(f"-- street-like strings NOT in gazetteer: {len(street_candidates)} --")
    for s, n in street_candidates.most_common(20):
        A(f"  {s:22s} {n:4d}")
    A("")
    A("-- fail-point language --")
    A(f"  records with fail language: {len(failpoints)}")
    fp = pd.DataFrame(failpoints)
    if len(fp):
        A(f"  of those, naming >=1 street: {(fp.streets != '').sum()}")
    A("")
    A("-- source independence --")
    multi = out[out.author_hash != ""].author_hash.value_counts()
    A(f"  distinct authors: {len(multi)}")
    A(f"  authors with >1 record: {(multi > 1).sum()}")
    A(f"  largest single-author share: {multi.max() if len(multi) else 0} records")
    A("")
    A("-- total corroboration weight, all rows --")
    A(f"  {out.reliability.sum():.2f}  (publish threshold is 1.5 per route)")

    text = "\n".join(lines)
    with open(os.path.join(args.outdir, "summary.txt"), "w") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    sys.exit(main())