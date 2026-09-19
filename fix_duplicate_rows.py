#!/usr/bin/env python3
"""One-off: manual_routes.py --apply's DELETE only ever targeted
source='rebuild_routes', so re-running --apply after the first
successful run (which already flipped source to 'manual_youtube') left
the old manual_youtube rows in place and just inserted a second set on
top -- 26 rows instead of 13. This removes the older, superseded half
(ids 280-292, the pre-Jasper-Road-fix smithsfalls G among them) and
their steps, backing up first, same as manual_routes.py does."""
import datetime as dt
import json
import os

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
STALE_IDS = list(range(280, 293))

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor(cursor_factory=RealDictCursor)

backup = {}
for t in ("route_lines", "route_line_steps"):
    cur.execute(f"SELECT * FROM {t}")
    backup[t] = [dict(r) for r in cur.fetchall()]
ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
bpath = f"route_data_backup_dedup_{ts}.json"
json.dump(backup, open(bpath, "w"), default=str, indent=1)
print(f"backup written: {bpath}")

cur.execute("DELETE FROM route_line_steps WHERE route_line_id = ANY(%s)", (STALE_IDS,))
print(f"deleted {cur.rowcount} stale steps")
cur.execute("DELETE FROM route_lines WHERE id = ANY(%s)", (STALE_IDS,))
print(f"deleted {cur.rowcount} stale route_lines")

conn.commit()
cur.close()
conn.close()
print("committed.")
