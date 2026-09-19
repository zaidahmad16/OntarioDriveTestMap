#!/usr/bin/env python3
"""
backfill_maxspeed_location.py — real coordinates for maxspeed-tagged
ways, so speed limits can be looked up by LOCATION, not just street name.

DESTINATION: backfill_maxspeed_location.py (repo root), needs venv/bin/python3

Why this exists: `Graph.street_maxspeed(name)` (analysis/consensus_geometry.py)
picks whichever tagged way SQLite happens to return first for a street
name, with no location awareness. A long arterial like Walkley Road
genuinely has BOTH a 50 km/h residential stretch (near the DriveTest
centre) and an 80 km/h stretch (toward the airport) -- a name-only
lookup can attach the wrong one to any given real leg. Confirmed by the
owner watching the live app: a maneuver near the centre showed 80 km/h,
Walkley Road's arterial speed, not the real local speed there.

way_maxspeed (built by geometry/extract_traffic_data.py) has no
geometry, only (way_id, maxspeed). This adds each way's real midpoint
node coordinate so a location-aware lookup becomes possible. Scoped to
only the ~2,400 way_ids that both carry a maxspeed tag AND match a
street name actually used in manual_routes.py's ROUTES, not a full
province-wide backfill -- keeps the pbf pass fast and targeted.

Usage:
    venv/bin/python3 backfill_maxspeed_location.py
"""
import sqlite3
import sys

import osmium

sys.path.insert(0, "analysis")
import consensus_geometry as cg  # noqa: E402

DB_PATH = "data/osm.db"
PBF_PATH = "data/raw/ontario-latest.osm.pbf"


def target_way_ids(con):
    cg.load_known(DB_PATH)
    import manual_routes as mr

    names = set()
    for streets in mr.ROUTES.values():
        names.update(streets)

    way_ids = set()
    for n in names:
        for v in cg.name_variants(n):
            rows = con.execute(
                "SELECT DISTINCT wm.way_id FROM streets s "
                "JOIN way_maxspeed wm ON wm.way_id = s.way_id "
                "WHERE s.base = ? OR s.full = ?", (v, v),
            ).fetchall()
            way_ids.update(r[0] for r in rows)
    return way_ids


def main():
    con = sqlite3.connect(DB_PATH)
    want = target_way_ids(con)
    print(f"{len(want):,} target way_ids")

    con.execute("""
        CREATE TABLE IF NOT EXISTS way_maxspeed_loc (
            way_id INTEGER PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL
        )
    """)
    con.commit()

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.n = 0

        def way(self, w):
            if w.id not in want:
                return
            pts = [(n.lat, n.lon) for n in w.nodes if n.location.valid()]
            if not pts:
                return
            lat, lon = pts[len(pts) // 2]
            con.execute(
                "INSERT OR REPLACE INTO way_maxspeed_loc (way_id, lat, lon) "
                "VALUES (?, ?, ?)", (w.id, lat, lon),
            )
            self.n += 1

    print("scanning ontario-latest.osm.pbf ...")
    h = H()
    h.apply_file(PBF_PATH, locations=True)
    con.commit()
    print(f"located {h.n:,} / {len(want):,} target ways")
    con.close()


if __name__ == "__main__":
    main()
