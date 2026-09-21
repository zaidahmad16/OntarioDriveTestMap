"""
submissions.py — community route-submission validation.

Reuses the same osm.db junction lookup the ground-truth pipeline uses
(analysis/consensus_geometry.py's Graph.junction), so a user submission
is checked against the same real-road adjacency data as everything else
in this app, not a separate/weaker check. The full CLI module is safe to
import here: its OSRM/network calls only happen inside functions this
code never calls, not at import time.

validate_pair/street_exists are checked one at a time as the user builds
a route (see /submissions/validate-street and /submissions/validate-pair
in main.py), not just in bulk when the whole list is submitted -- so the
frontend never has to parse which of N streets was the bad one back out
of a single error message.
"""

import os
import sqlite3
import sys

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)
for _sub in ("analysis", "common"):
    _p = os.path.join(_REPO_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from streetnames import load_known, variants as name_variants  # noqa: E402
from consensus_geometry import Graph  # noqa: E402

OSM_DB_PATH = os.path.join(_REPO_ROOT, "data", "osm.db")
load_known(OSM_DB_PATH)


def street_exists(name: str) -> bool:
    """Whether `name` matches any real street in osm.db, under any of
    its known spellings/suffix forms. Used to check the very first
    street a user adds, before there's a second one to form a pair --
    that street would otherwise pass through unchecked."""
    variants = name_variants(name)
    if not variants:
        return False
    con = sqlite3.connect(OSM_DB_PATH)
    placeholders = ",".join("?" * len(variants))
    row = con.execute(
        f"SELECT 1 FROM junction_streets "
        f"WHERE base IN ({placeholders}) OR full IN ({placeholders}) LIMIT 1",
        (*variants, *variants),
    ).fetchone()
    con.close()
    return row is not None


def junction_point(a: str, b: str) -> dict | None:
    """The real junction dict (node_id/lat/lon/kind) where a and b meet,
    or None if they don't. Single source of truth for both "is this a
    real junction" (validate_pair) and "what are its coordinates" (the
    forum, which needs a real lat/lon to place a post) -- one Graph
    lookup, not two."""
    g = Graph(OSM_DB_PATH)
    return g.junction(a, b)


def validate_pair(a: str, b: str) -> str | None:
    """None if a and b meet at a real osm.db junction; otherwise a
    specific reason -- this is the single source of truth both the
    incremental per-street check and the final bulk check build on."""
    if not junction_point(a, b):
        return f'No real junction found between "{a}" and "{b}".'
    return None


def validate_streets(streets: list[str]) -> str | None:
    """None if every consecutive pair of streets resolves to a real
    osm.db junction; otherwise an error naming the first pair that
    doesn't. Kept as a defensive final check on submit -- the frontend
    is expected to have already validated each addition incrementally,
    so this should always pass in the normal flow."""
    if len(streets) < 2:
        return "Need at least 2 streets to form a route."
    for a, b in zip(streets, streets[1:]):
        error = validate_pair(a, b)
        if error:
            return error
    return None
