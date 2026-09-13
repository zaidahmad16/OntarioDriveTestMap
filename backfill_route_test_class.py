#!/usr/bin/env python3
"""
backfill_route_test_class.py — derive route_lines.test_class /
mixed_classes for routes that are ALREADY published in Postgres,
without touching geometry or family membership.

DESTINATION: backfill_route_test_class.py (repo root)

Why this exists instead of just re-running consensus_geometry.py: doing
that re-derives route families from scratch, and a dry-run comparison
against the currently-published Walkley data showed it changes which
routes get published (8 lines -> 6, renumbered families) because the
clustering algorithm has moved on since the last real run. That's a
data-layer clustering change, out of scope for a schema backfill and
liable to silently drop already-verified route geometry.

Instead: for each already-published route_line, find which traces at
that centre actually share its (already key()-normalized) segments --
by re-deriving each trace's own consecutive-turn segment pairs the same
way consensus_geometry.py's trace_segments() does -- and only credit a
trace as "belonging" to the family if it covers at least half of the
route_line's own segments. A single shared hub junction (e.g. two
unrelated routes both passing "baycrest x walkley" near the test
centre) is not evidence a trace drove that specific route; requiring
majority coverage was added after a spot-check found EVERY trace
matched to Walkley family 2 shared exactly one generic hub segment and
none of the family's actual defining segments -- a fully spurious
match under the old "any overlap counts" rule.

test_class / mixed_classes themselves come from classvote.class_vote(),
the same function consensus_geometry.py uses when it first computes
these fields from the in-memory trace-to-family clustering. One rule,
two call sites -- see common/classvote.py's own docstring for why that
split existed and had to be collapsed.

Usage:
    python3 backfill_route_test_class.py --dry-run     # print only
    python3 backfill_route_test_class.py                # print + write
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))
from streetnames import load_known, key
from classvote import class_vote

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")


def trace_segment_keys(turns):
    streets = [t["street"] for t in turns]
    out = set()
    for a, b in zip(streets, streets[1:]):
        if a == b or a.startswith("@") or b.startswith("@"):
            continue
        out.add(tuple(sorted((key(a), key(b)))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "osm.db"))
    ap.add_argument("--dry-run", action="store_true",
                     help="print the computed values, write nothing")
    args = ap.parse_args()

    if not DATABASE_URL:
        sys.exit("Set DATABASE_PUBLIC_URL (or DATABASE_URL) before running this.")

    load_known(args.db)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT id, centre_id, family, run FROM route_lines ORDER BY centre_id, family, run")
    route_lines = cur.fetchall()

    buckets = {"classed": 0, "mixed": 0, "null": 0}

    for rl in route_lines:
        cur.execute(
            "SELECT street_a, street_b FROM route_line_segments WHERE route_line_id = %s",
            (rl["id"],),
        )
        rl_segs = {tuple(sorted((r["street_a"], r["street_b"]))) for r in cur.fetchall()}
        min_overlap = math.ceil(len(rl_segs) / 2) if rl_segs else 1

        cur.execute("SELECT id, test_class FROM traces WHERE centre_id = %s", (rl["centre_id"],))
        traces = cur.fetchall()

        matched_classes = []
        matched = 0
        for t in traces:
            cur.execute(
                "SELECT direction, street FROM trace_turns WHERE trace_id = %s ORDER BY turn_order",
                (t["id"],),
            )
            turns = cur.fetchall()
            tseg = trace_segment_keys(turns)
            if len(tseg & rl_segs) >= min_overlap:
                matched += 1
                matched_classes.append(t["test_class"])

        test_class, mixed, counts = class_vote(matched_classes)
        label = (f"route_line {rl['id']} ({rl['centre_id']} family {rl['family']} run {rl['run']}, "
                 f"{len(rl_segs)} segs, need >={min_overlap})")

        if test_class is None:
            buckets["null"] += 1
            print(f"  {label}: {matched} matched traces, no confirmed G/G2 among them -- NULL")
        else:
            buckets["mixed" if mixed else "classed"] += 1
            counts_str = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            flag = "  ! MIXED" if mixed else ""
            print(f"  {label}: {matched} matched traces, confirmed votes [{counts_str}] -> {test_class}{flag}")

        if not args.dry_run:
            cur.execute(
                "UPDATE route_lines SET test_class = %s, mixed_classes = %s WHERE id = %s",
                (test_class, mixed, rl["id"]),
            )

    if args.dry_run:
        print(f"\n[dry run, nothing written] classed={buckets['classed']} "
              f"mixed={buckets['mixed']} null={buckets['null']}")
    else:
        conn.commit()
        print(f"\nWrote {len(route_lines)} route_lines. "
              f"classed={buckets['classed']} mixed={buckets['mixed']} null={buckets['null']}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
