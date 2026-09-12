#!/usr/bin/env python3
"""
migrate_to_postgres.py — load the local JSON pipeline output into Railway
Postgres.

DESTINATION: migrate_to_postgres.py (repo root)

Reads each centre's traces (reddit_traces.json + ocr_traces.json where
present), snapped.json, consensus.geojson, and consensus_routes.geojson,
and inserts them into the schema created by schema.sql. Idempotent: safe
to re-run — existing rows are deleted per centre before reinserting, so
this always reflects the current state of the JSON files, not an
accumulating history.

Requires: pip install psycopg2-binary --break-system-packages
"""

import json
import os
import sys

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("Set DATABASE_PUBLIC_URL (or DATABASE_URL) before running this.")

CENTRES = {
    "walkley": "Ottawa Walkley",
    "canotek": "Ottawa Canotek",
    "smithsfalls": "Smiths Falls",
    "winchester": "Winchester",
}

DATA_DIR = "data/out"


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def migrate_centre(cur, centre_id):
    base = os.path.join(DATA_DIR, centre_id)
    if not os.path.isdir(base):
        print(f"  {centre_id}: no output directory, skipping")
        return

    # Clear this centre's rows first so a rerun reflects the current
    # JSON files exactly, not an accumulating history of every past run.
    cur.execute("DELETE FROM route_lines WHERE centre_id = %s", (centre_id,))
    cur.execute("DELETE FROM consensus_segments WHERE centre_id = %s", (centre_id,))
    cur.execute("DELETE FROM traces WHERE centre_id = %s", (centre_id,))

    # --- traces (reddit + ocr, snapped waypoints where available) ---
    all_traces = []
    for fname in ("reddit_traces.json", "ocr_traces.json", "all_traces.json"):
        data = load_json(os.path.join(base, fname))
        if data:
            all_traces.extend(data)

    snapped_by_source = {}
    snapped = load_json(os.path.join(base, "snapped.json"))
    if snapped:
        for t in snapped:
            snapped_by_source[t["source_id"]] = t

    # all_traces.json duplicates reddit+ocr for some centres; dedupe by
    # source_id so migrate_centre() doesn't insert the same trace twice.
    seen = set()
    n_traces, n_turns, n_waypoints = 0, 0, 0
    for t in all_traces:
        sid = t["source_id"]
        if sid in seen:
            continue
        seen.add(sid)

        snap = snapped_by_source.get(sid, {})
        cur.execute(
            """
            INSERT INTO traces
                (source_id, centre_id, test_class, reliability,
                 observed_at, author_hash, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id, centre_id) DO UPDATE SET
                test_class = EXCLUDED.test_class,
                reliability = EXCLUDED.reliability,
                observed_at = EXCLUDED.observed_at,
                author_hash = EXCLUDED.author_hash,
                status = EXCLUDED.status
            RETURNING id
            """,
            (
                sid,
                centre_id,
                t.get("test_class"),
                t.get("reliability"),
                parse_date(t.get("observed_at")),
                t.get("author_hash"),
                snap.get("status"),
            ),
        )
        trace_id = cur.fetchone()[0]
        n_traces += 1

        for i, turn in enumerate(t.get("turns", [])):
            cur.execute(
                """
                INSERT INTO trace_turns
                    (trace_id, turn_order, direction, street)
                VALUES (%s, %s, %s, %s)
                """,
                (trace_id, i, turn["direction"], turn["street"]),
            )
            n_turns += 1

        for i, wp in enumerate(snap.get("waypoints", [])):
            pair = wp.get("pair", [None, None])
            cur.execute(
                """
                INSERT INTO trace_waypoints
                    (trace_id, waypoint_order, node_id, lat, lon,
                     pair_street_a, pair_street_b, candidates)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trace_id,
                    i,
                    str(wp["node_id"]),
                    wp["lat"],
                    wp["lon"],
                    pair[0] if len(pair) > 0 else None,
                    pair[1] if len(pair) > 1 else None,
                    wp.get("candidates"),
                ),
            )
            n_waypoints += 1

    # --- consensus segments (junction confidence points) ---
    n_segments = 0
    consensus = load_json(os.path.join(base, "consensus.geojson"))
    if consensus:
        for feat in consensus.get("features", []):
            props = feat["properties"]
            lon, lat = feat["geometry"]["coordinates"]
            streets = props.get("streets", [None, None])
            cur.execute(
                """
                INSERT INTO consensus_segments
                    (centre_id, street_a, street_b, lat, lon, authors,
                     weight, video_count, text_count, last_seen,
                     junction_type, node_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (centre_id, street_a, street_b) DO UPDATE SET
                    lat = EXCLUDED.lat, lon = EXCLUDED.lon,
                    authors = EXCLUDED.authors, weight = EXCLUDED.weight,
                    video_count = EXCLUDED.video_count,
                    text_count = EXCLUDED.text_count,
                    last_seen = EXCLUDED.last_seen,
                    junction_type = EXCLUDED.junction_type,
                    node_id = EXCLUDED.node_id
                """,
                (
                    centre_id,
                    streets[0] if len(streets) > 0 else None,
                    streets[1] if len(streets) > 1 else None,
                    lat,
                    lon,
                    props.get("authors"),
                    props.get("weight"),
                    props.get("video"),
                    props.get("text"),
                    parse_date(props.get("last_seen")),
                    props.get("junction"),
                    str(props.get("node")) if props.get("node") else None,
                ),
            )
            n_segments += 1

    # --- published route lines (drawable paths) ---
    n_routes, n_route_segments = 0, 0
    routes = load_json(os.path.join(base, "consensus_routes.geojson"))
    if routes:
        for feat in routes.get("features", []):
            props = feat["properties"]
            cur.execute(
                """
                INSERT INTO route_lines
                    (centre_id, family, run, trace_count, authors,
                     distance_m, geometry)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    centre_id,
                    props.get("family"),
                    props.get("run"),
                    props.get("traces"),
                    props.get("authors"),
                    props.get("distance_m"),
                    json.dumps(feat["geometry"]["coordinates"]),
                ),
            )
            route_id = cur.fetchone()[0]
            n_routes += 1

            for i, seg in enumerate(props.get("segments", [])):
                streets = seg.get("streets", [None, None])
                cur.execute(
                    """
                    INSERT INTO route_line_segments
                        (route_line_id, segment_order, street_a, street_b,
                         authors, video_count, text_count, weight,
                         junction_type, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        route_id,
                        i,
                        streets[0] if len(streets) > 0 else None,
                        streets[1] if len(streets) > 1 else None,
                        seg.get("authors"),
                        seg.get("video"),
                        seg.get("text"),
                        seg.get("weight"),
                        seg.get("junction"),
                        parse_date(seg.get("last_seen")),
                    ),
                )
                n_route_segments += 1

    print(
        f"  {centre_id}: {n_traces} traces, {n_turns} turns, "
        f"{n_waypoints} waypoints, {n_segments} consensus segments, "
        f"{n_routes} route lines, {n_route_segments} route segments"
    )


def parse_date(s):
    """Postgres's DATE type needs a full day, but approximate dates in
    the source data are only ever precise to the month ('~2023-09').
    Pads those to the 1st -- the day is a placeholder, not a claim of
    precision, same as the '~' already signals in the source JSON. A
    truly unparseable value is stored as NULL rather than crashing the
    whole migration over one bad row."""
    if not s:
        return None
    try:
        s = s.lstrip("~")
        if len(s) == 7:       # "YYYY-MM", no day given
            s += "-01"
        return s
    except Exception:
        return None


def main():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    for centre_id, name in CENTRES.items():
        cur.execute(
            """
            INSERT INTO centres (id, name) VALUES (%s, %s)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """,
            (centre_id, name),
        )

    print("Migrating:")
    for centre_id in CENTRES:
        migrate_centre(cur, centre_id)

    conn.commit()
    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
