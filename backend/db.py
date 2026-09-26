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

# On Railway, use the PRIVATE DATABASE_URL (postgres.railway.internal):
# same data centre, no TLS hop through the public TCP proxy. Measured
# 2026-09-25: the public proxy cost ~0.3-0.5 s per query, and every API
# request paid it. Railway sets RAILWAY_ENVIRONMENT_NAME on deployed
# services. Off Railway (a laptop), the internal host doesn't resolve, so
# prefer DATABASE_PUBLIC_URL there.
_ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT_NAME") or os.environ.get("RAILWAY_ENVIRONMENT"))
if _ON_RAILWAY:
    DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
else:
    DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL (or DATABASE_PUBLIC_URL) in the environment.")

# Threaded, not Simple: FastAPI runs sync endpoints on a thread pool, and
# SimpleConnectionPool isn't safe to share across threads.
_pool = pool.ThreadedConnectionPool(1, 10, DATABASE_URL)


def get_conn():
    return _pool.getconn()


def put_conn(conn):
    # A connection the server dropped (idle timeout, restart) must not go
    # back into the pool to fail the next request.
    _pool.putconn(conn, close=bool(conn.closed))


def query(sql, params=None, one=False):
    """Run a read query, return list of dicts (or one dict / None)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            result = (rows[0] if rows else None) if one else rows
        # End the transaction cleanly. psycopg2 opens one implicitly on the
        # first execute; without this the pooled connection goes back
        # idle-in-transaction, and on ANY error below it would go back with
        # an *aborted* transaction and poison every later request that
        # reuses it ("current transaction is aborted...") until a restart.
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def execute(sql, params=None):
    """Run a write query and commit."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            result = cur.fetchall() if cur.description else None
        conn.commit()
        return result
    except Exception:
        # Roll back so a failed write doesn't return an aborted-transaction
        # connection to the pool (see the note in query()).
        conn.rollback()
        raise
    finally:
        put_conn(conn)
