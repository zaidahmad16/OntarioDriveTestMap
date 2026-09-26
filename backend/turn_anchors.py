"""
turn_anchors.py -- place a route's turn instructions on the map, honestly.

Route steps carry no coordinates. Placing them by cumulative step distance
was measured on 2026-09-25 and rejected: the step distances cover anywhere
from 3% to 94% of the drawn route length, so points would land far from
the real turns.

Instead each maneuver is resolved from its OWN street names against the
real OSM junction table (the same `junction_candidates` join the route
pipeline and forum already use):

  * the street you're on is tracked through the instruction text
    ("Continue on Walkley Rd", "Turn right onto Uplands Dr" ...);
  * a turn/exit onto street X from street Y looks up every real junction
    where Y and X meet;
  * a candidate is kept only if it lies within ANCHOR_MAX_M of the drawn
    route geometry AND comes after the previously anchored turn (so a
    street pair that meets twice, or a loop back past the centre, can't
    pull a later turn onto an earlier leg);
  * anything that doesn't pass stays in the turn list with no point.

"Continue on X" lines are not maneuvers and never get a point.
"""

import math
import re

from consensus_geometry import Graph

ANCHOR_MAX_M = 40
BACKTRACK_SLACK_M = 60

_ONTO = re.compile(r"\b(?:onto|into)\s+(?:the\s+)?(.+?)(?:\s+(?:on-ramp|off-ramp|ramp|exit))?\s*$", re.I)
_EXIT = re.compile(r"\b(?:exit|take the exit)\s+(?:at|to|for)\s+(?:the\s+)?(.+?)\s*$", re.I)
_ON = re.compile(r"\b(?:continue|keep\s+\w+|stay|head\s+\w+|drive)\b.*?\b(?:on|along)\s+(?:the\s+)?(.+?)\s*$", re.I)
_TURNISH = re.compile(r"\b(turn|exit|merge|bear|keep left|keep right|take)\b", re.I)


def _clean(name):
    name = re.sub(r"[.,;:!]+$", "", name.strip())
    return name if name and name.lower() not in {"median", "the median", "centre", "drivetest centre"} else None


def parse_step(instruction):
    """(kind, street): kind is 'turn' (a maneuver onto `street`),
    'follow' (you're travelling on `street`), or None."""
    s = (instruction or "").strip()
    m = _EXIT.search(s)
    if m:
        return "turn", _clean(m.group(1))
    m = _ONTO.search(s)
    if m and _TURNISH.search(s):
        return "turn", _clean(m.group(1))
    m = _ON.search(s)
    if m:
        return "follow", _clean(m.group(1))
    return None, None


def _hav(a, b):
    # a, b = (lon, lat)
    r = 6371000
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dla, dlo = la2 - la1, math.radians(b[0] - a[0])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _passes(coords, cum, p, max_m):
    """Every separate pass of the route within max_m of point p, as a
    list of along-route distances (one per contiguous run of nearby
    segments, at that run's closest point). A route that goes past the
    same junction on the way out and on the way back yields two passes --
    keeping only the single closest one pinned early turns onto the
    return leg (caught on Walkley G Route 1)."""
    passes = []
    run = None  # (dev, along) best in the current run
    for i in range(1, len(coords)):
        a, b = coords[i - 1], coords[i]
        kx = math.cos(math.radians((a[1] + b[1]) / 2)) or 1
        ax, ay, bx, by = a[0] * kx, a[1], b[0] * kx, b[1]
        px, py = p[0] * kx, p[1]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        t = 0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
        q = ((ax + t * dx) / kx, ay + t * dy)
        d = _hav(p, q)
        if d <= max_m:
            along = cum[i - 1] + t * (cum[i] - cum[i - 1])
            if run is None or d < run[0]:
                run = (d, along) if run is None or d < run[0] else run
        elif run is not None:
            passes.append(run[1])
            run = None
    if run is not None:
        passes.append(run[1])
    return passes


def anchor_steps(steps, coords, graph):
    """Returns a list parallel to `steps`: {lat, lon, along_m} or None."""
    if not coords or len(coords) < 2:
        return [None] * len(steps)
    cum = [0.0]
    for i in range(1, len(coords)):
        cum.append(cum[-1] + _hav(coords[i - 1], coords[i]))

    out = []
    current = None
    last_along = 0.0
    for s in steps:
        kind, street = parse_step(s.get("instruction"))
        anchor = None
        if kind == "turn" and street and current and street.lower() != current.lower():
            best = None
            for row in graph.junction_candidates(current, street):
                for along in _passes(coords, cum, (row["lon"], row["lat"]), ANCHOR_MAX_M):
                    if along < last_along - BACKTRACK_SLACK_M:
                        continue
                    # earliest valid pass wins: keeps later turns available
                    if best is None or along < best[1]:
                        best = (row, along)
            if best:
                row, along = best
                anchor = {"lat": row["lat"], "lon": row["lon"], "along_m": round(along)}
                last_along = along
        if kind in ("turn", "follow") and street:
            current = street
        out.append(anchor)
    return out


_cache = {}


def anchors_for_line(line_id, steps, coords, db_path):
    """Cached per route line (content-keyed, so a pipeline rebuild that
    changes steps or geometry recomputes). A fresh Graph per computation:
    sqlite connections can't be shared across FastAPI's worker threads."""
    key = (line_id, tuple(s.get("instruction") or "" for s in steps), len(coords),
           tuple(coords[0]) if coords else None, tuple(coords[-1]) if coords else None)
    if key not in _cache:
        _cache[key] = anchor_steps(steps, coords, Graph(db_path))
    return _cache[key]
