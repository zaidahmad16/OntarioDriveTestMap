#!/usr/bin/env python3
"""
recover_low_confidence_routes.py — draw the real, same-family segments
that consensus_geometry.py's own --threshold currently drops.

DESTINATION: analysis/recover_low_confidence_routes.py

Why this exists: a lot of what looks like "disconnected dots" on the
map is not missing data -- it's real, family-clustered segments that
fell below the publish threshold (0.5 summed weight) and were correctly
left out of the official route_lines, per consensus_geometry.py's own
stated design ("does not silently drop low-confidence segments...
deciding what's publishable is a display question, not a geometry
one"). This script draws that "not currently published" tier as its
own explicitly-marked layer, instead of leaving it invisible.

Safety boundary, the whole reason this is a separate script rather than
just lowering --threshold: it ONLY walks segments within a family that
consensus_geometry.py's own trace-level clustering already judged
coherent (min_family traces, same HDBSCAN/fallback cluster). It never
connects segments across different families or from unclustered (-1)
traces. snap_traces.py's own docstring documents exactly the failure
mode being avoided here: OSRM will happily weld two unrelated waypoint
sequences into one plausible-looking road path (a real incident,
"dkswtcc", produced a fabricated 15km route this way). Every edge drawn
here comes from a real, already-trusted family -- nothing here invents
a connection between things that were never judged to be the same
route.

This is additive-only against Postgres: it INSERTs new route_lines rows
(below_threshold=true) and never touches, deletes, or re-derives the
existing published rows. A fresh full run of consensus_geometry.py's
own clustering was checked and found to shift which routes get
published entirely (family/run renumbering, different route counts)
because the algorithm has moved on since the data was last migrated --
regenerating everything was rejected for exactly that reason. This
script leaves that already-migrated, already-verified data alone.

Usage:
    python3 recover_low_confidence_routes.py --dry-run
    python3 recover_low_confidence_routes.py            # writes to Postgres
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consensus_geometry as cg

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")

CENTRE_TRACE_FILES = {
    "canotek": ["reddit_traces.json", "ocr_traces.json"],
    "walkley": ["reddit_traces.json", "ocr_traces.json", "ocr_traces_g.json"],
    "smithsfalls": ["reddit_traces.json"],
}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "out")


def load_traces(centre_id, g):
    T = []
    for fname in CENTRE_TRACE_FILES.get(centre_id, []):
        path = os.path.join(DATA_DIR, centre_id, fname)
        if not os.path.exists(path):
            continue
        for t in json.load(open(path)):
            if centre_id not in str(t.get("centre_id", "")):
                continue
            if len(cg.trace_segments(t, g)) >= 2:
                T.append(t)
    return T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "osm.db"))
    ap.add_argument("--threshold", type=float, default=0.5,
                     help="must match consensus_geometry.py's own default, "
                          "this is the line that separates 'published' from "
                          "'recoverable' -- see module docstring")
    ap.add_argument("--min-family", type=int, default=3)
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would be recovered, write nothing")
    args = ap.parse_args()

    if not args.dry_run and not DATABASE_URL:
        sys.exit("Set DATABASE_PUBLIC_URL (or DATABASE_URL) before running this.")

    cg.load_known(args.db)
    g = cg.Graph(args.db)

    conn = cur = None
    if not args.dry_run:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

    total_recovered = 0
    for centre_id in CENTRE_TRACE_FILES:
        T = load_traces(centre_id, g)
        fam = cg.families(T, g, 2)
        real = {k: v for k, v in fam.items()
                if k >= 0 and len(v) >= args.min_family}

        for fid in sorted(real):
            traces = real[fid]
            seg = cg.support(traces, g)
            authors = len({t.get("author_hash") or t["source_id"] for t in traces})
            test_class, mixed_classes, _ = cg.class_vote(
                t.get("test_class") for t in traces)

            # order_walk() must get a list, not a set -- it builds its
            # adjacency dict by iterating this argument directly, and a
            # set's iteration order depends on Python's per-process hash
            # seed for strings. This is the exact non-determinism bug
            # consensus_geometry.py's own main loop already hit and fixed
            # once (see git history: "Fix age_weight half-life drift and
            # route-line nondeterminism") -- confirmed by hand here too,
            # passing a set produced a different run split on a second
            # run with identical input.
            below = [k for k, v in seg.items()
                     if sum(v["w"].values()) < args.threshold]
            extra_runs = cg.order_walk(seg, below)

            if not args.dry_run:
                cur.execute(
                    "SELECT COALESCE(MAX(run), -1) AS m FROM route_lines "
                    "WHERE centre_id = %s AND family = %s",
                    (centre_id, fid),
                )
                run_start = cur.fetchone()["m"] + 1
            else:
                run_start = 0

            for i, run in enumerate(extra_runs):
                feat = cg.build_route_feature(
                    seg, run, run_start + i, fid, traces, authors,
                    test_class, mixed_classes, args, below_threshold=True, g=g)
                if not feat:
                    continue
                p = feat["properties"]
                streets = sorted({s for k in run for s in k})
                print(f"  RECOVER {centre_id} family {fid} run {p['run']}: "
                      f"{len(run)} below-threshold segments, "
                      f"{', '.join(streets)}")
                total_recovered += 1

                if not args.dry_run:
                    cur.execute(
                        """
                        INSERT INTO route_lines
                            (centre_id, family, run, trace_count, authors,
                             distance_m, geometry, test_class, mixed_classes,
                             below_threshold, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            centre_id, fid, p["run"], p["traces"], p["authors"],
                            p["distance_m"], json.dumps(feat["geometry"]["coordinates"]),
                            test_class, mixed_classes, True,
                            "recover_low_confidence_routes",
                        ),
                    )
                    cg.insert_steps(cur, cur.fetchone()["id"], feat.get("_steps", []))

    if args.dry_run:
        print(f"\n[dry run, nothing written] {total_recovered} recoverable "
              f"low-confidence route(s) found")
    else:
        conn.commit()
        cur.close()
        conn.close()
        print(f"\nInserted {total_recovered} low-confidence route(s).")


if __name__ == "__main__":
    main()
