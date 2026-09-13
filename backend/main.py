"""
main.py — OntarioDriveTestMap API.

Centre listings are public (a landing page needs something to show before
anyone signs in). Actual route geometry -- the thing this whole project
exists to produce -- requires a signed-in Google account, checked via the
`session` cookie on every request to /centres/{id}/map and beyond.
"""

import os
from collections import defaultdict

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from auth import get_or_create_user, make_session_token, require_user, verify_google_token
from db import query

FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")

app = FastAPI(title="OntarioDriveTestMap API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,  # required for the httpOnly session cookie
    allow_methods=["*"],
    allow_headers=["*"],
)


class GoogleLogin(BaseModel):
    credential: str  # the ID token Google Identity Services hands the frontend


# ---------------------------------------------------------------- auth ---

@app.post("/auth/google")
def login(body: GoogleLogin, response: Response):
    try:
        payload = verify_google_token(body.credential)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google token.")

    user = get_or_create_user(payload)
    token = make_session_token(user)

    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=True,       # HTTPS only -- Railway serves everything over TLS
        samesite="lax",
        max_age=7 * 86400,
    )
    return {"email": user["email"], "name": user["name"]}


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/auth/me")
def me(user=Depends(require_user)):
    return {"email": user["email"]}


# ------------------------------------------------------------- centres ---

@app.get("/centres")
def list_centres():
    return query(
        """
        SELECT c.id, c.name,
               COUNT(DISTINCT t.id)  AS trace_count,
               COUNT(DISTINCT cs.id) AS segment_count,
               COUNT(DISTINCT rl.id) AS route_line_count
        FROM centres c
        LEFT JOIN traces t             ON t.centre_id = c.id
        LEFT JOIN consensus_segments cs ON cs.centre_id = c.id
        LEFT JOIN route_lines rl        ON rl.centre_id = c.id
        GROUP BY c.id, c.name
        ORDER BY c.id
        """
    )


@app.get("/centres/{centre_id}")
def get_centre(centre_id: str):
    row = query(
        """
        SELECT c.id, c.name,
               COUNT(DISTINCT t.id)  AS trace_count,
               COUNT(DISTINCT cs.id) AS segment_count,
               COUNT(DISTINCT rl.id) AS route_line_count
        FROM centres c
        LEFT JOIN traces t             ON t.centre_id = c.id
        LEFT JOIN consensus_segments cs ON cs.centre_id = c.id
        LEFT JOIN route_lines rl        ON rl.centre_id = c.id
        WHERE c.id = %s
        GROUP BY c.id, c.name
        """,
        (centre_id,),
        one=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No such centre.")
    return row


def _connect_segments(points):
    """Chain consensus_segments into walkable paths by shared street name.

    Each point here is already real, scored evidence -- it passed
    build_consensus.py's own threshold to exist in this table at all.
    Most of them just never got promoted into an official route_line,
    because that requires a full trace-level family cluster with enough
    corroborating traces (see analysis/consensus_geometry.py). This adds
    ordering only, using the same street-adjacency approach that
    pipeline's own order_walk() uses for its route_lines: two points end
    up adjacent in a drawn line only because a real published segment
    says those two streets meet, never by proximity or guesswork. No
    point moves and nothing is snapped to a road -- these are straight
    lines between real junction coordinates, not OSRM-routed geometry
    like the official route_lines get, which is why they're a visually
    distinct, less-precise layer, not a replacement for the real thing.
    """
    edges = {i: p for i, p in enumerate(points) if p["street_a"] and p["street_b"]}
    adj = defaultdict(list)
    for i, p in edges.items():
        adj[p["street_a"]].append((p["street_b"], i))
        adj[p["street_b"]].append((p["street_a"], i))

    unused = set(edges)
    runs = []
    while unused:
        streets = {s for i in unused for s in (edges[i]["street_a"], edges[i]["street_b"])}
        start = max(streets, key=lambda s: len([1 for _, i in adj[s] if i in unused]))
        run, cur = [], start
        while True:
            nxt = next(((other, i) for other, i in adj[cur] if i in unused), None)
            if not nxt:
                break
            other, i = nxt
            unused.discard(i)
            run.append(i)
            cur = other
        if run:
            runs.append(run)
    return runs


@app.get("/centres/{centre_id}/map")
def get_map(centre_id: str, user=Depends(require_user)):
    """One GeoJSON FeatureCollection: official route lines (if any exist
    for this centre -- Smiths Falls currently has none, that's real, not
    a bug), a "connected_segments" layer chaining every scored junction
    into walkable paths by shared street name (see _connect_segments),
    and every scored junction as its own point feature."""
    lines = query(
        "SELECT family, run, trace_count, authors, distance_m, geometry, "
        "test_class, mixed_classes "
        "FROM route_lines WHERE centre_id = %s",
        (centre_id,),
    )
    points = query(
        "SELECT street_a, street_b, lat, lon, authors, weight, "
        "video_count, text_count, last_seen "
        "FROM consensus_segments WHERE centre_id = %s",
        (centre_id,),
    )

    features = []
    for line in lines:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": line["geometry"]},
                "properties": {
                    "kind": "route_line",
                    "family": line["family"],
                    "run": line["run"],
                    "trace_count": line["trace_count"],
                    "authors": line["authors"],
                    "distance_m": line["distance_m"],
                    "test_class": line["test_class"],
                    "mixed_classes": line["mixed_classes"],
                },
            }
        )
    for run in _connect_segments(points):
        if len(run) < 2:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[points[i]["lon"], points[i]["lat"]] for i in run],
                },
                "properties": {
                    "kind": "connected_segments",
                    "segment_count": len(run),
                },
            }
        )

    for pt in points:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [pt["lon"], pt["lat"]]},
                "properties": {
                    "kind": "segment",
                    "streets": [pt["street_a"], pt["street_b"]],
                    "authors": pt["authors"],
                    "weight": float(pt["weight"]),
                    "video_count": pt["video_count"],
                    "text_count": pt["text_count"],
                    "last_seen": str(pt["last_seen"]) if pt["last_seen"] else None,
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


@app.get("/centres/{centre_id}/traces")
def list_traces(centre_id: str, user=Depends(require_user)):
    return query(
        """
        SELECT id, source_id, test_class, reliability, observed_at,
               author_hash, status
        FROM traces
        WHERE centre_id = %s
        ORDER BY id
        """,
        (centre_id,),
    )


@app.get("/traces/{trace_id}")
def get_trace(trace_id: int, user=Depends(require_user)):
    trace = query("SELECT * FROM traces WHERE id = %s", (trace_id,), one=True)
    if not trace:
        raise HTTPException(status_code=404, detail="No such trace.")

    trace["turns"] = query(
        "SELECT direction, street FROM trace_turns "
        "WHERE trace_id = %s ORDER BY turn_order",
        (trace_id,),
    )
    trace["waypoints"] = query(
        "SELECT node_id, lat, lon, pair_street_a, pair_street_b, candidates "
        "FROM trace_waypoints WHERE trace_id = %s ORDER BY waypoint_order",
        (trace_id,),
    )
    return trace
