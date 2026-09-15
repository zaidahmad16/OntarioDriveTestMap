#!/usr/bin/env python3
"""
cleanup_degenerate_bridges.py — remove zero-length predicted "bridges"
from route_lines.

DESTINATION: cleanup_degenerate_bridges.py (repo root)

Background: predict_family_bridges.py used to draw a bridge between any
two disconnected components of a family, using their nearest pair of
points. order_walk() emits a branch off a shared junction as its own
separate run, so two of a family's components can have their nearest
points at the SAME coordinate -- and the MST then "bridged" a point to
itself: a 2-point LineString of ~0 m. It renders as nothing but still
counts as a predicted route line and clutters the inferred-route total.

predict_family_bridges.py has since been fixed (components closer than
MIN_BRIDGE_M are unioned without drawing a bridge), so no NEW ones are
created. This script clears the ones already written to Postgres before
that fix. As of 2026-09-14 that is exactly 3 rows: ids 250, 252, 253
(Walkley family 0 run 0, family 2 runs 1 and 2).

Safety:
  * Dry-run by default. Prints exactly what it would delete and writes
    nothing. Pass --apply to actually delete.
  * Only ever touches rows that are predicted=true AND
    source='predict_family_bridges' AND whose geometry is a 2-point line
    whose endpoints are within --epsilon-m (default 10 m). It will not
    touch a real predicted bridge, a confirmed route, or anything with
    real length.
  * Always writes a JSON backup of every row it deletes (and that row's
    route_line_steps, if any) BEFORE deleting, so the delete is fully
    reversible. Re-running predict_family_bridges.py would also
    regenerate the (now non-degenerate) bridge set from scratch.
  * Runs the whole delete in one transaction and refuses to commit if the
    number of rows deleted doesn't match the number identified.

Usage:
    python3 cleanup_degenerate_bridges.py                  # dry run
    python3 cleanup_degenerate_bridges.py --apply          # delete
    python3 cleanup_degenerate_bridges.py --apply --epsilon-m 5
"""
import argparse
import datetime as dt
import json
import math
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()  # so DATABASE_PUBLIC_URL in the repo-root .env is picked up
except ImportError:
    pass

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")


def haversine(a, b):
    """a, b are [lon, lat]."""
    la1, lo1, la2, lo2 = map(math.radians, [a[1], a[0], b[1], b[0]])
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def coords_of(geometry):
    """route_lines.geometry stores a bare [[lon,lat], ...] array (JSONB).
    Accept it as parsed JSON or as a string, and be defensive about a
    full GeoJSON geometry dict just in case."""
    g = geometry
    if isinstance(g, str):
        g = json.loads(g)
    if isinstance(g, dict):
        g = g.get("coordinates", [])
    return g or []


def is_degenerate(row, epsilon_m):
    if not (row["predicted"] and row["source"] == "predict_family_bridges"):
        return False
    c = coords_of(row["geometry"])
    if len(c) != 2:
        return False
    return haversine(c[0], c[1]) < epsilon_m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without this it is a dry run")
    ap.add_argument("--epsilon-m", type=float, default=10.0,
                    help="a 2-point predicted line whose endpoints are "
                         "within this distance is treated as zero-length")
    ap.add_argument("--backup-dir", default=os.path.dirname(os.path.abspath(__file__)),
                    help="where to write the reversible backup JSON")
    args = ap.parse_args()

    if not DATABASE_URL:
        sys.exit("Set DATABASE_PUBLIC_URL (or DATABASE_URL) before running this.")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        "SELECT id, centre_id, family, run, geometry, source, predicted "
        "FROM route_lines "
        "WHERE predicted = true AND source = 'predict_family_bridges' "
        "ORDER BY centre_id, family, run"
    )
    degen = [r for r in cur.fetchall() if is_degenerate(r, args.epsilon_m)]

    if not degen:
        print("No degenerate predicted bridges found. Nothing to do.")
        cur.close()
        conn.close()
        return 0

    print(f"{len(degen)} degenerate predicted bridge(s) "
          f"(2-point, endpoints < {args.epsilon_m:.0f} m apart):")
    ids = [r["id"] for r in degen]
    for r in degen:
        print(f"  id={r['id']}  {r['centre_id']} family {r['family']} run {r['run']}")

    # pull the steps too, so the backup captures everything needed to undo
    cur.execute(
        "SELECT * FROM route_line_steps WHERE route_line_id = ANY(%s) "
        "ORDER BY route_line_id, step_order",
        (ids,),
    )
    steps = cur.fetchall()

    backup = {
        "written": dt.datetime.now().isoformat(timespec="seconds"),
        "epsilon_m": args.epsilon_m,
        "route_lines": [dict(r) for r in degen],
        "route_line_steps": [dict(s) for s in steps],
    }
    bpath = os.path.join(
        args.backup_dir,
        f"degenerate_bridges_backup_{dt.date.today().isoformat()}.json",
    )
    with open(bpath, "w") as f:
        json.dump(backup, f, default=str, indent=1)
    print(f"\nBackup written: {bpath}  "
          f"({len(degen)} route_lines, {len(steps)} steps)")

    if not args.apply:
        print("\n[dry run] nothing deleted. Re-run with --apply to delete.")
        cur.close()
        conn.close()
        return 0

    cur.execute("DELETE FROM route_line_steps WHERE route_line_id = ANY(%s)", (ids,))
    steps_deleted = cur.rowcount
    cur.execute(
        "DELETE FROM route_lines WHERE id = ANY(%s) "
        "AND predicted = true AND source = 'predict_family_bridges'",
        (ids,),
    )
    rows_deleted = cur.rowcount

    if rows_deleted != len(degen):
        conn.rollback()
        sys.exit(f"ABORT: expected to delete {len(degen)} route_lines but "
                 f"matched {rows_deleted}. Rolled back, nothing changed.")

    conn.commit()
    print(f"\nDeleted {rows_deleted} route_lines and {steps_deleted} steps. "
          f"Committed. Undo from the backup above if needed.")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
