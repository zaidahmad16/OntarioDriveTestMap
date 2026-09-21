#!/usr/bin/env python3
"""
promote_submissions.py — auto-promote corroborated community submissions
into real route_lines rows, with no human review.

DESTINATION: scripts/promote_submissions.py

Notion "Frontend Design" backlog, Medium-Hard tier: "Auto-clustering
promotion pipeline for submissions." backend/main.py's /submissions
docstring already flags this as the un-built next step -- this is it.

Reuses the SAME clustering/confidence machinery the ground-truth
pipeline uses (analysis/consensus_geometry.py: families(), support(),
order_walk(), build_route_feature()), not a parallel system. A
user_submission + its turns is converted into the same trace dict shape
consensus_geometry.py already understands ({source_id, test_class,
author_hash, observed_at, turns:[{street}]}), so nothing downstream of
that conversion needs to know submissions exist as a separate concept.

Guardrails (the actual "real new engineering" this backlog item calls
out, since the clustering itself is reused, not invented):

  outlier rejection -- families() clusters submissions into HDBSCAN (or
  hierarchical-fallback) groups; a submission that doesn't cohere with
  anything else lands in the noise cluster (label -1) and is dropped,
  same as consensus_geometry.py's own trace clustering. Never force a
  lone submission into a route.

  independent-session dedupe -- author_hash is set to f"user:{user_id}"
  per submission, so two submissions from the same account collapse to
  one author in support()'s per-segment weighting (`s["w"][who] = max(...)`
  already does this, unchanged) rather than counting as two corroborating
  people. A family also needs >= MIN_AUTHORS *distinct* users, not just
  >= MIN_AUTHORS submissions, before it's eligible at all.

  safe rollback via the source column -- every row this script writes is
  source='user_submission_promoted'. Re-running is idempotent: for each
  (centre_id, test_class) scope touched, existing rows with that source
  are deleted and rebuilt fresh from the current submissions, exactly
  like migrate_to_postgres.py's per-centre delete-then-reinsert pattern
  -- so this never accumulates stale/duplicate promotions, and manually
  running `DELETE FROM route_lines WHERE source='user_submission_promoted'`
  cleanly undoes everything this script has ever done, with zero effect
  on any other source's rows.

  status is recomputed, not a one-way ratchet -- a submission can go
  pending -> promoted or promoted -> pending across runs (e.g. a later
  submission turns out to be an outlier relative to the rest and the
  family's clustering shifts). user_submissions.status/
  promoted_route_line_id always reflect the current run's real result,
  never a stale claim from an earlier one.

Known, honest scope limit: a submission for a centre not yet in the
`centres` table (centre_id IS NULL) can't be promoted -- route_lines.
centre_id is a NOT NULL FK to centres. Left pending forever until that
centre is added; not silently dropped, not worked around.

Usage:
    python3 promote_submissions.py --dry-run     # print what would happen
    python3 promote_submissions.py --apply       # write it
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "analysis"))
import consensus_geometry as cg

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")

SOURCE = "user_submission_promoted"


def load_submission_traces(cur):
    """Every user_submission + its turns, as consensus_geometry.py trace
    dicts, grouped by (centre_id, test_class). Submissions with no
    centre_id (an untracked centre) are returned separately -- route_lines
    has no home for them yet, see module docstring."""
    cur.execute(
        """
        SELECT s.id, s.centre_id, s.centre_name, s.test_class, s.user_id,
               s.created_at,
               array_agg(t.street ORDER BY t.turn_order) AS streets
        FROM user_submissions s
        JOIN user_submission_turns t ON t.submission_id = s.id
        GROUP BY s.id
        """
    )
    by_scope = defaultdict(list)
    unhomed = []
    for row in cur.fetchall():
        trace = {
            "source_id": f"user_submission:{row['id']}",
            "test_class": row["test_class"],
            "author_hash": f"user:{row['user_id']}",
            "observed_at": row["created_at"].date().isoformat(),
            "turns": [{"street": s} for s in row["streets"]],
            "_submission_id": row["id"],
        }
        if row["centre_id"]:
            by_scope[(row["centre_id"], row["test_class"])].append(trace)
        else:
            unhomed.append(row["id"])
    return by_scope, unhomed


def promote_scope(cur, g, centre_id, test_class, traces, threshold,
                   min_authors, min_family, args):
    """One (centre, class) scope: cluster its submissions, build a route
    for every family that clears both bars, replace this scope's prior
    promotions with the fresh result.

    Returns (n_clusters_found, n_routes_written, n_submissions_promoted).
    """
    fam = cg.families(traces, g, min_family)
    real = {k: v for k, v in fam.items() if k >= 0}

    # (feature, submission_ids) per route this scope will end up with.
    to_write = []
    for fid, fam_traces in real.items():
        authors = {t["author_hash"] for t in fam_traces}
        if len(authors) < min_authors:
            continue  # not enough independent people, not a route yet

        seg = cg.support(fam_traces, g)
        keep = [k for k, v in seg.items() if sum(v["w"].values()) >= threshold]
        if not keep:
            continue  # clustered, but no segment individually corroborated enough

        sub_ids = [t["_submission_id"] for t in fam_traces]
        for run_idx, run in enumerate(cg.order_walk(seg, keep)):
            feat = cg.build_route_feature(
                seg, run, run_idx, fid, fam_traces, len(authors),
                test_class, False, args, below_threshold=False, g=g)
            if feat:
                to_write.append((feat, sub_ids))

    if args.dry_run:
        n_promoted = len({sid for _, ids in to_write for sid in ids})
        return len(real), len(to_write), n_promoted

    cur.execute(
        "DELETE FROM route_lines WHERE source = %s AND centre_id = %s "
        "AND test_class = %s",
        (SOURCE, centre_id, test_class),
    )
    # Every submission in this scope is reset to pending first -- a
    # submission promoted by a previous run that no longer clusters must
    # fall back, not keep a stale promoted_route_line_id pointing at a
    # row this DELETE just removed.
    cur.execute(
        "UPDATE user_submissions SET status = 'pending', "
        "promoted_route_line_id = NULL WHERE id = ANY(%s)",
        ([t["_submission_id"] for t in traces],),
    )

    promoted_ids = set()
    for i, (feat, sub_ids) in enumerate(to_write):
        p = feat["properties"]
        cur.execute(
            """
            INSERT INTO route_lines
                (centre_id, family, run, trace_count, authors,
                 distance_m, geometry, test_class, mixed_classes,
                 below_threshold, predicted, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                centre_id, p["family"], i, p["traces"], p["authors"],
                p["distance_m"], json.dumps(feat["geometry"]["coordinates"]),
                test_class, False, False, False, SOURCE,
            ),
        )
        route_line_id = cur.fetchone()["id"]
        cg.insert_steps(cur, route_line_id, feat.get("_steps", []))

        cur.execute(
            "UPDATE user_submissions SET status = 'promoted', "
            "promoted_route_line_id = %s WHERE id = ANY(%s)",
            (route_line_id, sub_ids),
        )
        promoted_ids.update(sub_ids)

    return len(real), len(to_write), len(promoted_ids)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "osm.db"))
    ap.add_argument("--threshold", type=float, default=0.5,
                     help="must match consensus_geometry.py's own 'confirmed' "
                          "line -- a submission-only route is held to the "
                          "same bar as ground-truth data, not a lower one")
    ap.add_argument("--min-authors", type=int, default=2,
                     help="distinct users required before a family is "
                          "even considered -- one person's submission, "
                          "however detailed, is never auto-promoted alone")
    ap.add_argument("--min-family", type=int, default=2,
                     help="passed straight to families(); a cluster of 1 "
                          "is not corroboration")
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    args.dry_run = not args.apply

    if not DATABASE_URL:
        sys.exit("Set DATABASE_PUBLIC_URL (or DATABASE_URL) before running this "
                  "(read-only access is required even for --dry-run, to read "
                  "real submissions).")

    cg.load_known(args.db)
    g = cg.Graph(args.db)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    by_scope, unhomed = load_submission_traces(cur)

    if unhomed:
        print(f"skipped {len(unhomed)} submission(s) for a centre not yet "
              f"in `centres` (no home for a route_lines row): {unhomed}")

    total_routes, total_promoted = 0, 0
    for (centre_id, test_class), traces in sorted(by_scope.items()):
        n_clusters, n_routes, n_promoted = promote_scope(
            cur, g, centre_id, test_class, traces,
            args.threshold, args.min_authors, args.min_family, args)
        total_routes += n_routes
        total_promoted += n_promoted
        print(f"{centre_id} {test_class}: {len(traces)} submission(s), "
              f"{n_clusters} cluster(s), {n_routes} route(s) promoted "
              f"({n_promoted} submission(s) involved)")

    if args.dry_run:
        print(f"\n[dry run, nothing written] {total_routes} route(s) would "
              f"be promoted across {len(by_scope)} scope(s)")
    else:
        conn.commit()
        print(f"\nPromoted {total_routes} route(s) across {len(by_scope)} scope(s).")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
