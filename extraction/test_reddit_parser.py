#!/usr/bin/env python3
"""
test_reddit_parser.py — regression tests for process_reddit_v2.py's
street-name extraction.

DESTINATION: extraction/test_reddit_parser.py

Every FIXED case below is a real phrase, pulled verbatim from real
Canotek/Smiths Falls Reddit text, that silently produced wrong or
missing output before being fixed on 2026-09-09/10. Run this after any
change to clean_street(), extract_turns(), or the regexes in
process_reddit_v2.py -- a script this easy to run has no excuse to
skip, and every fix tonight was found by testing real text, not by
reading the code.

Every KNOWN GAP case is a documented, deliberately-unfixed limitation.
These are expected to FAIL. If one starts passing, the fix landed --
move it to FIXED. If one starts producing a *different* wrong answer
than the one recorded here, that's a silent regression worth
investigating on its own, not progress.

Usage: python3 test_reddit_parser.py
Exit code 0 if all FIXED cases pass, 1 if any regressed.
"""

import sys
sys.path.insert(0, ".")
from process_reddit_v2 import clean_street, extract_turns, load_gazetteer, GAZ

GAZ.update(load_gazetteer("../data/osm.db"))


FIXED = [
    (
        "St-prefix truncation (St Lawrence St -> bare 'st', vanishes) "
        "-- would silently break any Saint-prefixed street, incl. "
        "Canotek's St Joseph Blvd",
        clean_street, ("St Lawrence St",), "st lawrence",
    ),
    (
        "Centre-token prefix ('Drive Test parking lot' fuzzy-matched "
        "to an unrelated real street 'driver' province-wide)",
        clean_street, ("Drive Test parking",), "@centre",
    ),
    (
        "Blacklist checked after gazetteer lookup, not before "
        "('Ottawa' matches a real unrelated street somewhere in the "
        "province, from 'Ottawa 174' regional-road phrasing)",
        clean_street, ("Ottawa",), None,
    ),
    (
        "'the median' -- confirms the blacklist reorder didn't break "
        "the original NOT_A_STREET entries",
        clean_street, ("the median",), None,
    ),
]

FIXED_TURNS = [
    (
        "'Follow curve onto X' not recognized as a straight-move verb "
        "-- silently dropped two real turns in CheekyChum7's route",
        "Follow curve onto Andrews Ave",
        [("straight", "andrews")],
    ),
    (
        "Street-capture crossing a blank line into the next sentence "
        "('Left on toulon\\n\\nRight onto...' capturing "
        "'toulon\\n\\nRight onto' as the street)",
        "Left on toulon\n\nRight onto Lavinia st",
        [("left", "toulon"), ("right", "lavinia")],
    ),
]

KNOWN_GAPS = [
    (
        "Parenthetical highway alias ('country rd 29(brockville)') "
        "-- digit/paren breaks the capture, 'brockville' (the real "
        "meaningful name) is never reached",
        "country rd 29(brockville)",
        "country",
    ),
]

KNOWN_GAP_TURNS = [
    (
        "Connector phrase between direction and preposition "
        "('Left from back of lot onto Percy st') -- 'from' isn't a "
        "recognized preposition, whole turn is missed",
        "Left from back of lot onto Percy st",
        [("left", "percy")],
        [],
    ),
    (
        "'at X onto Y' captures the wrong noun phrase "
        "('Turn Left at Amber light onto Van Horne Ave' captures "
        "'amber', drops the real destination 'Van Horne Ave')",
        "Turn Left at Amber light onto Van Horne Ave",
        [("left", "van horne")],
        [("left", "amber")],
    ),
    (
        "RETURN_TO_CENTRE requires adjacency, fails on an interrupting "
        "phrase ('back INTO BACK OF Drive Test centre')",
        "Turn Left back into back of Drive Test centre",
        [("straight", "@centre")],
        [],
    ),
]


def run():
    failures = 0

    print("=== FIXED: clean_street() cases ===")
    for desc, fn, args, expected in FIXED:
        got = fn(*args)
        ok = got == expected
        status = "PASS" if ok else "FAIL -- REGRESSION"
        print(f"  [{status}] {desc}")
        if not ok:
            print(f"           expected={expected!r}  got={got!r}")
            failures += 1

    print()
    print("=== FIXED: extract_turns() cases ===")
    for desc, text, expected in FIXED_TURNS:
        got = [(t["direction"], t["street"]) for t in extract_turns(text)]
        ok = got == expected
        status = "PASS" if ok else "FAIL -- REGRESSION"
        print(f"  [{status}] {desc}")
        if not ok:
            print(f"           expected={expected!r}  got={got!r}")
            failures += 1

    print()
    print("=== KNOWN GAPS: clean_street() (expected to fail) ===")
    for desc, text, currently in KNOWN_GAPS:
        got = clean_street(text)
        if got != currently:
            print(f"  [CHANGED] {desc}")
            print(f"           recorded wrong value={currently!r}  now={got!r}")
            print(f"           -- if this is now correct, move to FIXED. "
                  f"If it's a different wrong answer, investigate.")
        else:
            print(f"  [as documented] {desc}")

    print()
    print("=== KNOWN GAPS: extract_turns() (expected to fail) ===")
    for desc, text, correct, currently in KNOWN_GAP_TURNS:
        got = [(t["direction"], t["street"]) for t in extract_turns(text)]
        if got == correct:
            print(f"  [FIXED -- update this file] {desc}")
        elif got == currently:
            print(f"  [as documented] {desc}")
        else:
            print(f"  [CHANGED] {desc}")
            print(f"           recorded wrong output={currently!r}  now={got!r}")

    print()
    if failures:
        print(f"{failures} REGRESSION(S) in fixed cases -- do not ship.")
        return 1
    print("All fixed cases hold. Known gaps unchanged from documented state.")
    return 0


if __name__ == "__main__":
    sys.exit(run())