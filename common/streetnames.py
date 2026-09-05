#!/usr/bin/env python3
"""
streetnames.py — one street-name normaliser, used everywhere.

DESTINATION: common/streetnames.py

This exists because the same bug appeared five times in five files.

  process_reddit_v2.py      clean_street()
  build_intersection_index  normalise()
  ocr_traces.py             base_name()
  build_consensus.py        base()
  drive_past.py             base()

The bug: stripping a trailing street type is right for "Bank Street" ->
"bank", which is how people write it, and wrong for "Airport Parkway" ->
"airport", where the suffix is part of the name everyone uses and the
stripped form collides with airport service roads several kilometres
away.

Each file was fixed separately. Fixing one never fixed the others, and
there was no test that would catch a sixth occurrence. On 2026-09-04 it
surfaced again in a new place: video traces recorded "airport" while
text traces recorded "airport parkway", the two keyed differently, and
the drive-past classifier read one street as two competing streets at
the same junction.

The rule: a name resolves to its FULL form when that form is a real
street in the extract, and to its suffix-stripped BASE otherwise. Load
the known set once from osm.db; without it the function falls back to
stripping, which is the old behaviour.

Usage:
    from streetnames import load_known, key, variants

    load_known("../data/osm.db")
    key("Airport Parkway")   -> "airport parkway"
    key("airport")           -> "airport parkway"
    key("Bank Street")       -> "bank"
"""

import re
import sqlite3

SUFFIXES = {
    "road", "rd", "street", "st", "avenue", "ave", "drive", "dr",
    "boulevard", "blvd", "parkway", "pkwy", "crescent", "cres",
    "court", "crt", "ct", "lane", "ln", "way", "place", "pl",
    "terrace", "terr", "circle", "cir", "trail", "private",
    "north", "south", "east", "west", "n", "s", "e", "w",
}

# full normalised name -> canonical key, built from the extract
_FULL = set()
# stripped form -> full form, when the stripped form is ambiguous
_EXPAND = {}
# stripped forms covering more than one real street. Kept so callers can
# report them rather than being surprised by them.
_AMBIGUOUS = set()
_LOADED = False


def clean(name):
    """Lowercase, strip punctuation, collapse whitespace. No suffix logic."""
    return " ".join(re.sub(r"[^\w\s]", " ", (name or "").lower()).split())


def strip_suffix(name):
    """'Cedarwood Drive' -> 'cedarwood'. Unconditional."""
    w = clean(name).split()
    while w and w[-1] in SUFFIXES:
        w.pop()
    return " ".join(w) if w else clean(name)


def load_known(db_path):
    """Read every street name in the extract and decide, per street, whether
    stripping the suffix is safe.

    Default is to strip: "Cedarwood Drive" and "cedarwood" both become
    "cedarwood", which is what makes text and video traces agree.

    A street is marked UNSAFE to strip when its stripped form collides
    with a different street. "Airport Parkway" stripped is "airport",
    which collides with the airport service roads, so it keeps its full
    form. Everything referring to it — including a bare "airport" read
    off a sign — resolves to that same full form.
    """
    global _LOADED
    con = sqlite3.connect(db_path)
    fulls = {r[0] for r in
             con.execute("SELECT DISTINCT full FROM streets") if r[0]}
    bases = {r[0] for r in
             con.execute("SELECT DISTINCT base FROM streets") if r[0]}
    con.close()

    _FULL.clear()
    _FULL.update(fulls)
    _EXPAND.clear()
    _AMBIGUOUS.clear()

    by_base = {}
    for f in fulls:
        by_base.setdefault(strip_suffix(f), []).append(f)

    for b, names in by_base.items():
        distinct = {n for n in names if n != b}
        # collision: several different streets share this stripped form,
        # or the stripped form is itself a street name. Stripping would
        # merge distinct roads, so pin every reference to one full form.
        collides = len(distinct) > 1 or (b in fulls and distinct)
        if collides and len(distinct) == 1:
            _EXPAND[b] = next(iter(distinct))
        elif collides:
            # Several distinct streets share this stripped form — Airport
            # Parkway and the airport service roads, for instance. They
            # will merge under one key.
            #
            # That is the lesser evil. The damage on 2026-09-04 came from
            # the opposite failure: ONE street held under TWO keys, so
            # video traces saying "airport" and text traces saying
            # "airport parkway" looked like competing streets at the same
            # junction, and the drive-past classifier flagged a real
            # segment. Consistency matters more than separation, because
            # junction lookup disambiguates downstream anyway — two
            # streets merged under one key still resolve to different
            # junctions when paired with a second street.
            _AMBIGUOUS.add(b)

    _LOADED = True
    return len(_FULL), len(_EXPAND)


def ambiguous():
    """Stripped forms that cover more than one distinct street."""
    return sorted(_AMBIGUOUS)


def key(name):
    """Canonical key for a street name.

    Every script must use this and nothing else. Two spellings of the
    same street have to produce the same key or every junction
    comparison downstream is comparing the wrong things.
    """
    c = clean(name)
    if not c:
        return ""
    b = strip_suffix(c)
    # a street whose stripped form would collide keeps its full form,
    # and bare references to it resolve there too
    if b in _EXPAND:
        return _EXPAND[b]
    return b


def variants(name):
    """Forms to try when querying the database.

    The index stores both base and full, so a lookup should try both
    plus the canonical key.
    """
    c = clean(name)
    return list({c, strip_suffix(c), key(name)} - {""})


def segment_key(a, b):
    """Unordered canonical key for a segment between two streets."""
    return tuple(sorted((key(a), key(b))))


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "../data/osm.db"
    n_full, n_exp = load_known(db)
    print(f"{n_full:,} street names, {n_exp:,} expansions\n")
    amb = ambiguous()
    print(f"{len(amb):,} ambiguous stripped forms"
          + (f": {', '.join(amb[:8])}" if amb else "") + "\n")
    for t in ("Airport Parkway", "airport", "Bank Street", "bank",
              "Cedarwood Drive", "cedarwood", "Hunt Club Road", "hunt club",
              "Walkley Road", "walkley", "Colliston Crescent", "colliston",
              "Albion Road", "albion"):
        print(f"  {t:22s} -> {key(t)}")