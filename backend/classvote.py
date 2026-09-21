#!/usr/bin/env python3
"""
classvote.py — one majority-vote rule for a route family's test_class.

DESTINATION: common/classvote.py

This exists for the same reason streetnames.py does: the same
computation appeared twice (consensus_geometry.py's own family loop,
and the standalone Postgres backfill script that derives test_class for
already-published route_lines), and two copies drift the first time
either one changes.

Usage:
    from classvote import class_vote

    dominant, mixed, counts = class_vote(t.get("test_class") for t in traces)
"""

from collections import defaultdict

# guess_class() (extraction/process_reddit_v2.py) returns "ambiguous" when
# a trace's text matched BOTH the G and G2 keyword patterns, and "unknown"
# when it matched neither. Both are uncertainty about the trace, not a
# third confirmed route class -- a family where 6 traces say "ambiguous"
# and 5 say "G2" is not evidence of a blended route, it's 5 confirmed G2
# traces and 6 traces we simply couldn't classify. Letting "ambiguous"
# cast an equal vote let uncertain readings outvote (and mislabel as
# "mixed") families that were actually a clean, single, confirmed class.
# Fix: only confirmed "G"/"G2" labels are votes. Everything else abstains.
CONFIRMED_CLASSES = {"G", "G2"}


def class_vote(test_classes):
    """Majority vote over a family's trace test_class labels.

    Returns (dominant, mixed, counts):
      dominant -- the winning confirmed class, or None if no trace in
                  the family carries a confirmed G/G2 label at all.
      mixed    -- True only when confirmed G and confirmed G2 votes are
                  BOTH present -- real evidence of a blended-class
                  family, not merely some traces being unclassifiable.
      counts   -- {class: count} over confirmed votes only, for callers
                  that want to print/report the tally.
    """
    counts = defaultdict(int)
    for tc in test_classes:
        if tc in CONFIRMED_CLASSES:
            counts[tc] += 1
    dominant = max(counts, key=counts.get) if counts else None
    mixed = len(counts) > 1
    return dominant, mixed, dict(counts)
