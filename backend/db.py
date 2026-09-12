"""
db.py — Postgres connection pool.

A pool, not a connection-per-request, because this runs on Railway where
a cold connection to a public proxy costs real latency. Small pool sizes
are fine here: this serves a handful of read-heavy endpoints, not a
high-traffic API.
"""

from dotenv import load_dotenv
load_dotenv()  # reads the repo root .env automatically

import os

from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# Public URL first: it works from a laptop AND from Railway. The internal
# .railway.internal host only resolves from inside Railway's own network,
# so it's useless here and would only be needed if this env var came
# pre-set by Railway itself on a deployed service, which is checked second.
DATABASE_URL = (
    os.environ.get("DATABASE_PUBLIC_URL")
    or os.environ.get("DATABASE_URL")
)
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL (or DATABASE_PUBLIC_URL) in the environment.")

_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL)


def get_conn():
    return _pool.getconn()


def put_conn(conn):
    _pool.putconn(conn)


def query(sql, params=None, one=False):
    """Run a read query, return list of dicts (or one dict / None)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
    finally:
        put_conn(conn)


def execute(sql, params=None):
    """Run a write query and commit."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            conn.commit()
            if cur.description:
                return cur.fetchall()
            return None
    finally:
        put_conn(conn)
