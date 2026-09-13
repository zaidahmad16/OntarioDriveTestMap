#!/usr/bin/env python3
"""
extract_traffic_data.py — real stop signs, traffic lights, and speed
limits from the OSM extract, into osm.db.

DESTINATION: geometry/extract_traffic_data.py

build_intersection_index.py never captured this -- it was built purely
for street-name junction lookup (node_id/lat/lon/kind), not driving
detail. This is a separate, additive pass over the same underlying OSM
data, real and sourced the same way street names already are: every
row here is a tag some OSM contributor recorded on an actual node or
way, not inferred or guessed. Coverage is genuinely uneven -- checked
directly against the Ottawa extract before building this: only ~4.7%
of ways carry a maxspeed tag at all, so most segments will show no
speed limit rather than a wrong one. That's an honest gap in the
source data, not something to paper over with a guessed default.

Usage:
    python3 extract_traffic_data.py ../data/raw/ontario-latest.osm.pbf --db ../data/osm.db
"""
import argparse
import sqlite3

import osmium


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("--db", default="../data/osm.db")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS traffic_control (
            node_id INTEGER PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            kind TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS way_maxspeed (
            way_id INTEGER PRIMARY KEY,
            maxspeed TEXT NOT NULL
        )
    """)
    con.commit()

    n_signals = n_stops = n_maxspeed = 0
    n_processed = 0

    for obj in osmium.FileProcessor(args.pbf):
        if obj.is_node():
            hw = obj.tags.get("highway")
            if hw in ("traffic_signals", "stop") and obj.location.valid():
                cur.execute(
                    "INSERT OR REPLACE INTO traffic_control (node_id, lat, lon, kind) "
                    "VALUES (?, ?, ?, ?)",
                    (obj.id, obj.location.lat, obj.location.lon, hw),
                )
                if hw == "traffic_signals":
                    n_signals += 1
                else:
                    n_stops += 1
        elif obj.is_way():
            ms = obj.tags.get("maxspeed")
            if ms:
                cur.execute(
                    "INSERT OR REPLACE INTO way_maxspeed (way_id, maxspeed) VALUES (?, ?)",
                    (obj.id, ms),
                )
                n_maxspeed += 1

        n_processed += 1
        if n_processed % 2_000_000 == 0:
            con.commit()
            print(f"  ...{n_processed:,} objects processed "
                  f"(signals={n_signals} stops={n_stops} maxspeed={n_maxspeed})")

    con.commit()
    con.close()
    print(f"\ndone: {n_signals} traffic signals, {n_stops} stop signs, "
          f"{n_maxspeed} ways with a maxspeed tag")


if __name__ == "__main__":
    main()
