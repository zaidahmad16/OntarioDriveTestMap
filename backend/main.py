"""
main.py — OntarioDriveTestMap API.

Centre listings are public (a landing page needs something to show before
anyone signs in). Actual route geometry -- the thing this whole project
exists to produce -- requires a signed-in Google account, checked via the
`session` cookie on every request to /centres/{id}/map and beyond.
"""

import os

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
               COUNT(DISTINCT rl.id) AS route_line_count,
               COUNT(DISTINCT rl.id) FILTER (
                   WHERE NOT rl.predicted AND NOT rl.below_threshold
               ) AS confirmed_route_line_count
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
               COUNT(DISTINCT rl.id) AS route_line_count,
               COUNT(DISTINCT rl.id) FILTER (
                   WHERE NOT rl.predicted AND NOT rl.below_threshold
               ) AS confirmed_route_line_count
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


@app.get("/centres/{centre_id}/map")
def get_map(centre_id: str, user=Depends(require_user)):
    """One GeoJSON FeatureCollection: route lines (if any exist for this
    centre -- Smiths Falls currently has none, that's real, not a bug)
    plus every scored junction as its own point feature.

    All three source queries (lines, points, steps) run as ONE round
    trip via json_agg subqueries instead of three separate ones -- this
    runs over the public Railway proxy where each round trip measured
    ~300-460ms of pure connection/network overhead even for a handful of
    rows (confirmed directly, not assumed), so three sequential queries
    cost ~1.1s before any data even starts rendering. One round trip
    cuts that to ~1 query's worth of latency."""
    row = query(
        """
        SELECT
          (SELECT COALESCE(json_agg(row_to_json(rl)), '[]') FROM (
              SELECT id, family, run, trace_count, authors, distance_m,
                     geometry, test_class, mixed_classes, below_threshold,
                     predicted, source
              FROM route_lines WHERE centre_id = %(cid)s
              ORDER BY family, run
          ) rl) AS lines,
          (SELECT COALESCE(json_agg(row_to_json(cs)), '[]') FROM (
              SELECT street_a, street_b, lat, lon, authors, weight,
                     video_count, text_count, last_seen
              FROM consensus_segments WHERE centre_id = %(cid)s
          ) cs) AS points,
          (SELECT COALESCE(json_agg(row_to_json(st)), '[]') FROM (
              SELECT s.route_line_id, s.instruction, s.distance_m,
                     s.duration_s, s.traffic_control, s.speed_limit
              FROM route_line_steps s
              JOIN route_lines rl ON rl.id = s.route_line_id
              WHERE rl.centre_id = %(cid)s
              ORDER BY s.route_line_id, s.step_order
          ) st) AS steps
        """,
        {"cid": centre_id},
        one=True,
    )
    lines, points, step_rows = row["lines"], row["points"], row["steps"]

    steps_by_line = {}
    for r in step_rows:
        steps_by_line.setdefault(r["route_line_id"], []).append(
            {
                "instruction": r["instruction"],
                "distance_m": r["distance_m"],
                "duration_s": r["duration_s"],
                "traffic_control": r["traffic_control"],
                "speed_limit": r["speed_limit"],
            }
        )

    features = []
    for line in lines:
        steps = steps_by_line.get(line["id"], [])
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
                    "below_threshold": line["below_threshold"],
                    "predicted": line["predicted"],
                    "source": line["source"],
                    "steps": steps,
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
