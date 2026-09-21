"""
main.py — OntarioDriveTestMap API.

Centre listings are public (a landing page needs something to show before
anyone signs in). Actual route geometry -- the thing this whole project
exists to produce -- requires a signed-in Google account, checked via the
`session` cookie on every request to /centres/{id}/map and beyond.
"""

import os
import re

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from auth import (
    get_or_create_user,
    get_user_by_id,
    make_session_token,
    require_user,
    verify_google_token,
)
from db import execute, query
from submissions import junction_point, street_exists, validate_pair, validate_streets
from forum import (
    COMMENT_DAILY_LIMIT,
    POST_DAILY_LIMIT,
    REPORT_HIDE_THRESHOLD,
    SCORE_HIDE_THRESHOLD,
    contains_blocked_word,
    over_comment_rate_limit,
    over_post_rate_limit,
    pseudonym,
)
import discussions
from notifications import notify_comment, notify_mentions

FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")

app = FastAPI(title="OntarioDriveTestMap API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,  # required for the httpOnly session cookie
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    """SEC-003, 2026-09-21 security audit: nothing set these before.
    CSP/HSTS belong at the static-hosting/edge layer for the frontend
    (this is a JSON API, not an HTML-serving app), so only the headers
    that make sense for an API response live here."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    response.headers["X-Frame-Options"] = "DENY"
    return response

# Rate limiting (2026-09-21) -- in-memory, per-IP. No Redis: this is a
# single-instance Railway deployment, and an in-memory limiter resets on
# deploy, which is an acceptable tradeoff for a project this size (the
# goal is blunting abuse/scraping, not a hard security boundary).
# `default_limits` + SlowAPIMiddleware apply a generous ceiling to every
# endpoint automatically, including ones with no explicit decorator
# below (the read/list endpoints) -- only endpoints that need a
# *tighter* limit than the default get their own @limiter.limit(...)
# and therefore need `request: Request` added to their signature
# (slowapi reads the client IP off that).
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    # Shaped like every other error in this API (a plain {"detail": ...}
    # from HTTPException) rather than slowapi's default bare-text body.
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests -- please slow down and try again shortly."},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)


class GoogleLogin(BaseModel):
    credential: str  # the ID token Google Identity Services hands the frontend


class ReportError(BaseModel):
    route_line_id: int
    step_order: int
    note: str | None = Field(default=None, max_length=2000)


class SubmittedTurn(BaseModel):
    street: str = Field(max_length=200)
    junction_type: str | None = Field(default=None, max_length=100)  # the user's own recollection (stop sign, traffic light, ...); not fact-checked


class SubmitRoute(BaseModel):
    centre_id: str | None = None  # matched to a real centres.id, or None for a centre not yet in the app
    centre_name: str = Field(max_length=200)
    test_class: str = Field(max_length=20)
    turns: list[SubmittedTurn] = Field(max_length=100)  # a real route has a handful of turns, not thousands


class BookingReminder(BaseModel):
    centre_id: str | None = None
    remind_at: str  # ISO date, e.g. "2026-10-15"
    note: str | None = Field(default=None, max_length=500)


class ValidateStreet(BaseModel):
    street: str = Field(max_length=200)


class ValidatePair(BaseModel):
    street_a: str = Field(max_length=200)
    street_b: str = Field(max_length=200)


class ForumPost(BaseModel):
    centre_id: str | None = None
    centre_name: str = Field(max_length=200)
    test_class: str = Field(max_length=20)
    street_a: str = Field(max_length=200)
    street_b: str = Field(max_length=200)
    maneuver_type: str = Field(max_length=100)
    outcome: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=5000)


class ForumVote(BaseModel):
    value: int  # -1, 0 (remove vote), or 1


class ForumComment(BaseModel):
    body: str = Field(max_length=3000)


class SetUsername(BaseModel):
    username: str = Field(max_length=20)  # USERNAME_RE below already enforces 3-20, this just bounds the raw input before regex runs


# SEC-001, 2026-09-21 security audit: this endpoint is public and
# unauthenticated by design, so its input is fully attacker-controlled.
# Restricting the character set at the source (real URL paths only, no
# angle brackets/quotes) is defense-in-depth on top of escaping the
# value everywhere it's later rendered (see digest.py) -- never a
# substitute for escaping at the output sink.
PAGE_VIEW_PATH_RE = re.compile(r"^[A-Za-z0-9\-_/.?=&%~]*$")


class PageView(BaseModel):
    path: str = Field(max_length=500, pattern=PAGE_VIEW_PATH_RE.pattern)


class DigestOptIn(BaseModel):
    opt_in: bool


class DiscussionPost(BaseModel):
    centre_id: str | None = None
    centre_name: str | None = Field(default=None, max_length=200)  # unlike the forum, a discussion post can be fully general -- no centre at all
    title: str = Field(max_length=300)
    body: str = Field(max_length=10000)
    test_type: str | None = None  # 'G' | 'G2' | None (general, not test-type-specific)
    post_type: str | None = None  # 'question' | 'experience' | 'tip'
    route_line_id: int | None = None  # real route_lines.id, never arbitrary text


class DiscussionVote(BaseModel):
    value: int  # -1, 0 (remove vote), or 1


class DiscussionComment(BaseModel):
    body: str = Field(max_length=3000)
    parent_comment_id: int | None = None


class DiscussionCommentVote(BaseModel):
    value: int  # -1, 0 (remove vote), or 1


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def display_name(row: dict) -> str:
    """Real chosen username once set; a deterministic pseudonym before
    that, so a post is never attributed to a blank name."""
    return row["username"] or pseudonym(row["user_id"])


def compute_difficulty(steps: list[dict], distance_m: int | None) -> dict:
    """Heuristic 0-100 score from real per-step data already extracted
    (traffic_control/speed_limit tags, step count) -- no data beyond
    what's already in route_line_steps. Not a claim about real-world
    pass difficulty, just a quantified stand-in for drgo.ca's
    unverifiable "Ottawa is easier than Toronto" prose."""
    n = len(steps)
    if not n:
        return {"score": None, "label": None}
    km = (distance_m or 0) / 1000 or 1
    turns_per_km = n / km
    controls = sum(1 for s in steps if s["traffic_control"])
    control_density = controls / n
    speed_zones = len({s["speed_limit"] for s in steps if s["speed_limit"]})

    score = min(100, round(turns_per_km * 6 + control_density * 50 + speed_zones * 4))
    label = "Easy" if score <= 30 else "Moderate" if score <= 60 else "Hard"
    return {"score": score, "label": label}


# ---------------------------------------------------------------- auth ---

@app.post("/auth/google")
@limiter.limit("10/minute")
def login(request: Request, body: GoogleLogin, response: Response):
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
    # Same shape as /auth/me -- Login.jsx passes this straight into
    # App.jsx's user state, so a partial shape here (previously just
    # email/name) left username/is_admin undefined right after signing
    # in, showing the username prompt and hiding the ADMIN badge for an
    # already-admin, already-named account until a full page reload
    # re-fetched /auth/me and corrected it.
    return {
        "email": user["email"],
        "name": user["name"],
        "username": user["username"],
        "is_admin": user["is_admin"],
        "weekly_digest_opt_in": user["weekly_digest_opt_in"],
    }


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.post("/account/delete")
@limiter.limit("5/minute")
def delete_account(request: Request, response: Response, user=Depends(require_user)):
    """Anonymizes the account rather than deleting the row -- see the
    comment on users.deleted_at in users_schema.sql for why. Forum/
    discussion posts, comments and votes stay in place (attributed to
    the same deterministic pseudonym any username-less user already
    gets), but every piece of real identity -- email, name, username,
    and the Google account link itself -- is scrubbed. The tombstone
    google_sub value can never match a real Google sub, so this account
    can never sign back in."""
    uid = user["user_id"]
    execute(
        """
        UPDATE users
        SET email = %s, name = NULL, username = NULL,
            google_sub = %s, deleted_at = now()
        WHERE id = %s
        """,
        (f"deleted-{uid}@deleted.invalid", f"deleted:{uid}", uid),
    )
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/auth/me")
def me(user=Depends(require_user)):
    row = get_user_by_id(user["user_id"])
    return {
        "email": row["email"],
        "username": row["username"],
        "is_admin": row["is_admin"],
        "weekly_digest_opt_in": row["weekly_digest_opt_in"],
    }


@app.patch("/account/digest-opt-in")
@limiter.limit("5/minute")
def set_digest_opt_in(request: Request, body: DigestOptIn, user=Depends(require_user)):
    """Admin-only weekly summary email opt-in (2026-09-21). Silently a
    no-op for non-admin accounts rather than a 403 -- the toggle isn't
    shown to them in the UI at all, so reaching this with opt_in=true
    on a non-admin account would only happen via a direct API call, and
    there's nothing sensitive about the flag itself; it just never gets
    read by send_weekly_digest.py for anyone who isn't is_admin."""
    me_row = get_user_by_id(user["user_id"])
    if not me_row["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only.")
    execute(
        "UPDATE users SET weekly_digest_opt_in = %s WHERE id = %s",
        (body.opt_in, user["user_id"]),
    )
    return {"ok": True, "weekly_digest_opt_in": body.opt_in}


@app.post("/analytics/pageview")
@limiter.limit("30/minute")
def record_pageview(request: Request, body: PageView):
    """First-party, cookie-free page-view counter -- see page_views in
    schema.sql. No auth required (most page views happen before/without
    sign-in), no IP/user-agent/session recorded. Never raises past a
    bad path -- a page view failing to log must never break navigation."""
    path = body.path.strip()[:500]
    if not path:
        return {"ok": False}
    execute("INSERT INTO page_views (path) VALUES (%s)", (path,))
    return {"ok": True}


@app.post("/auth/username")
def set_username(body: SetUsername, user=Depends(require_user)):
    """Chosen once after Google sign-in, not supplied by Google -- this
    is what @mentions and forum posts are attributed to. A user without
    one yet still shows up under a deterministic pseudonym (see
    forum.py), so nothing is ever blank, but posting requires a real
    one (checked in create_forum_post/add_forum_comment)."""
    name = body.username.strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-20 characters: letters, numbers, underscore only.",
        )
    taken = query(
        "SELECT id FROM users WHERE username = %s AND id != %s",
        (name, user["user_id"]),
        one=True,
    )
    if taken:
        raise HTTPException(status_code=409, detail="That username is already taken.")
    execute("UPDATE users SET username = %s WHERE id = %s", (name, user["user_id"]))
    return {"ok": True, "username": name}


@app.get("/users/search")
def search_users(q: str = "", user=Depends(require_user)):
    """Backs @mention autocomplete -- usernames only, nothing else
    about the matched users is exposed."""
    q = q.strip()
    if not q:
        return []
    return query(
        "SELECT username FROM users WHERE username ILIKE %s ORDER BY username LIMIT 10",
        (f"{q}%",),
    )


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


@app.get("/centres/compare")
def compare_centres(user=Depends(require_user)):
    """Real distance/duration/maneuver-count stats per centre, confirmed
    routes only (predicted/below_threshold excluded -- a comparison view
    built partly on unsourced guesses would mislead). No pass-rate column:
    this app has no pass/fail data anywhere, so it's omitted rather than
    invented. Registered before /centres/{centre_id} -- FastAPI matches
    path routes in registration order, so this must come first or every
    call here would 404 as centre_id="compare" instead."""
    return query(
        """
        SELECT c.id, c.name,
               COUNT(DISTINCT rl.id) AS confirmed_route_count,
               ROUND(AVG(rl.distance_m)) AS avg_distance_m,
               ROUND(AVG(step_counts.n)) AS avg_maneuver_count
        FROM centres c
        LEFT JOIN route_lines rl
               ON rl.centre_id = c.id AND NOT rl.predicted AND NOT rl.below_threshold
        LEFT JOIN (
            SELECT route_line_id, COUNT(*) AS n
            FROM route_line_steps
            GROUP BY route_line_id
        ) step_counts ON step_counts.route_line_id = rl.id
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
          (SELECT COALESCE(json_agg(row_to_json(rl) ORDER BY rl.family, rl.run), '[]') FROM (
              SELECT id, family, run, trace_count, authors, distance_m,
                     geometry, test_class, mixed_classes, below_threshold,
                     predicted, source
              FROM route_lines WHERE centre_id = %(cid)s
          ) rl) AS lines,
          (SELECT COALESCE(json_agg(row_to_json(cs)), '[]') FROM (
              SELECT street_a, street_b, lat, lon, authors, weight,
                     video_count, text_count, last_seen
              FROM consensus_segments WHERE centre_id = %(cid)s
          ) cs) AS points,
          (SELECT COALESCE(json_agg(row_to_json(st) ORDER BY st.route_line_id, st.step_order), '[]') FROM (
              SELECT s.route_line_id, s.step_order, s.instruction, s.distance_m,
                     s.duration_s, s.traffic_control, s.speed_limit
              FROM route_line_steps s
              JOIN route_lines rl ON rl.id = s.route_line_id
              WHERE rl.centre_id = %(cid)s
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
                "step_order": r["step_order"],
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
        difficulty = compute_difficulty(steps, line["distance_m"])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": line["geometry"]},
                "properties": {
                    "kind": "route_line",
                    "route_line_id": line["id"],
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
                    "difficulty_score": difficulty["score"],
                    "difficulty_label": difficulty["label"],
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


@app.post("/report-error")
def report_error(body: ReportError, user=Depends(require_user)):
    exists = query(
        "SELECT id FROM route_lines WHERE id = %s", (body.route_line_id,), one=True
    )
    if not exists:
        raise HTTPException(status_code=404, detail="No such route.")
    execute(
        """
        INSERT INTO instruction_reports (route_line_id, step_order, user_id, note)
        VALUES (%s, %s, %s, %s)
        """,
        (body.route_line_id, body.step_order, user["user_id"], body.note),
    )
    return {"ok": True}


@app.post("/submissions/validate-street")
@limiter.limit("30/minute")
def validate_street_endpoint(request: Request, body: ValidateStreet, user=Depends(require_user)):
    """Checked when a user adds the FIRST street of a route -- there's no
    second street yet to pair-validate against, so this catches a typo'd
    or made-up street name before anything else is built on top of it."""
    name = body.street.strip()
    if not name:
        return {"ok": False, "error": "Street name is required."}
    if not street_exists(name):
        return {"ok": False, "error": f'"{name}" doesn\'t match any real street.'}
    return {"ok": True, "error": None}


@app.post("/submissions/validate-pair")
@limiter.limit("30/minute")
def validate_pair_endpoint(request: Request, body: ValidatePair, user=Depends(require_user)):
    """Checked every time a user adds another street to the route
    they're building, one addition at a time -- so a bad street is
    caught and fixed on the spot instead of surfacing only after the
    whole list is submitted."""
    a, b = body.street_a.strip(), body.street_b.strip()
    if not a or not b:
        return {"ok": False, "error": "Street name is required."}
    error = validate_pair(a, b)
    return {"ok": error is None, "error": error}


@app.post("/submissions")
@limiter.limit("10/minute")
def submit_route(request: Request, body: SubmitRoute, user=Depends(require_user)):
    """Community route submission -- for any Ontario DriveTest centre,
    including one not yet in this app's `centres` table (osm.db's
    street/junction data is Ontario-wide, not scoped to the handful of
    centres already tracked here, so validation works regardless).
    Validated against osm.db (same Graph.junction() adjacency check the
    ground-truth pipeline uses) before it's stored, so a bogus street
    list is rejected with a real reason instead of silently landing as
    data. Stored in user_submissions/user_submission_turns, a separate
    low-trust tier -- not merged into route_lines directly. Turning
    corroborated submissions into an actual route is
    scripts/promote_submissions.py (Notion backlog, Medium-Hard tier) --
    a separate, explicitly-run pipeline job, not this endpoint."""
    centre_id = body.centre_id
    if centre_id:
        centre = query("SELECT id FROM centres WHERE id = %s", (centre_id,), one=True)
        if not centre:
            raise HTTPException(status_code=404, detail="No such centre.")

    centre_name = body.centre_name.strip()
    if not centre_name:
        raise HTTPException(status_code=400, detail="Centre name is required.")

    streets = [t.street for t in body.turns]
    error = validate_streets(streets)
    if error:
        raise HTTPException(status_code=400, detail=error)

    rows = execute(
        """
        INSERT INTO user_submissions (centre_id, centre_name, test_class, user_id)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (centre_id, centre_name, body.test_class, user["user_id"]),
    )
    submission_id = rows[0]["id"]
    execute(
        """
        INSERT INTO user_submission_turns (submission_id, turn_order, street, junction_type)
        SELECT %(sid)s, ord - 1, street, junction_type
        FROM unnest(%(streets)s::text[], %(junction_types)s::text[])
            WITH ORDINALITY AS t(street, junction_type, ord)
        """,
        {
            "sid": submission_id,
            "streets": streets,
            "junction_types": [t.junction_type for t in body.turns],
        },
    )
    return {"ok": True, "submission_id": submission_id}


@app.get("/submissions")
def list_submissions(user=Depends(require_user)):
    """Most recent community submissions across every centre -- the form
    itself is global (header-triggered, not scoped to a selected
    centre), so this is one shared list rather than a per-centre one."""
    return query(
        """
        SELECT id, centre_id, centre_name, test_class, created_at,
               status, promoted_route_line_id
        FROM user_submissions
        ORDER BY created_at DESC
        LIMIT 20
        """
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


# --------------------------------------------------------------- forum ---

@app.get("/forum")
def list_forum_posts(centre_id: str | None = None, user=Depends(require_user)):
    """Real net score (SUM of +1/-1 votes) and report count, computed
    live from the actual vote/report rows every request -- never a
    stored counter that could drift out of sync. Auto-hidden posts
    (score at or below SCORE_HIDE_THRESHOLD, or report count at/above
    REPORT_HIDE_THRESHOLD) are excluded for everyone except admins, who
    see them flagged with `hidden: true` instead -- the crowd's votes
    and reports are still the primary gate, admin visibility is a
    deliberate override on top, not a replacement for it."""
    me = get_user_by_id(user["user_id"])
    rows = query(
        """
        SELECT p.id, p.centre_id, p.centre_name, p.test_class, p.street_a, p.street_b,
               p.maneuver_type, p.outcome, p.note, p.created_at, p.user_id,
               u.username,
               COALESCE(v.score, 0) AS score,
               COALESCE(c.comment_count, 0) AS comment_count,
               myv.value AS my_vote,
               img.id AS image_id,
               (COALESCE(v.score, 0) <= %(score_floor)s
                OR COALESCE(r.report_count, 0) >= %(report_ceiling)s) AS hidden
        FROM forum_posts p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN (SELECT post_id, SUM(value) AS score FROM forum_votes GROUP BY post_id)
            v ON v.post_id = p.id
        LEFT JOIN (SELECT post_id, COUNT(*) AS comment_count FROM forum_comments GROUP BY post_id)
            c ON c.post_id = p.id
        LEFT JOIN (SELECT post_id, COUNT(*) AS report_count FROM forum_reports GROUP BY post_id)
            r ON r.post_id = p.id
        LEFT JOIN forum_votes myv ON myv.post_id = p.id AND myv.user_id = %(uid)s
        LEFT JOIN LATERAL (
            SELECT id FROM forum_images WHERE post_id = p.id ORDER BY id LIMIT 1
        ) img ON true
        WHERE (%(cid)s IS NULL OR p.centre_id = %(cid)s)
          AND (
            %(is_admin)s
            OR (COALESCE(v.score, 0) > %(score_floor)s AND COALESCE(r.report_count, 0) < %(report_ceiling)s)
          )
        ORDER BY COALESCE(v.score, 0) DESC, p.created_at DESC
        """,
        {
            "cid": centre_id,
            "uid": user["user_id"],
            "is_admin": me["is_admin"],
            "score_floor": SCORE_HIDE_THRESHOLD,
            "report_ceiling": REPORT_HIDE_THRESHOLD,
        },
    )
    for row in rows:
        row["author"] = display_name(row)
        row["can_delete"] = row["user_id"] == user["user_id"] or me["is_admin"]
    return rows


@app.post("/forum")
def create_forum_post(body: ForumPost, user=Depends(require_user)):
    """Every post resolves to a real junction (same Graph.junction()
    adjacency check as route submissions) -- that's where its lat/lon
    comes from, so it's placeable on the map like any other real point
    feature, not a free-floating text post. Rate-limited and
    wordlist-filtered before it's stored; score/report auto-hide is
    query-time (see list_forum_posts), not enforced here. Requires a
    real username -- @mentions only mean something if the poster is
    addressable by one."""
    me = get_user_by_id(user["user_id"])
    if not me["username"]:
        raise HTTPException(status_code=400, detail="Set a username before posting.")

    centre_id = body.centre_id
    if centre_id:
        centre = query("SELECT id FROM centres WHERE id = %s", (centre_id,), one=True)
        if not centre:
            raise HTTPException(status_code=404, detail="No such centre.")

    centre_name = body.centre_name.strip()
    if not centre_name:
        raise HTTPException(status_code=400, detail="Centre name is required.")

    if over_post_rate_limit(user["user_id"]):
        raise HTTPException(
            status_code=429,
            detail=f"Daily post limit reached ({POST_DAILY_LIMIT}/day). Try again tomorrow.",
        )
    if contains_blocked_word(body.note) or contains_blocked_word(body.maneuver_type):
        raise HTTPException(status_code=400, detail="Post contains blocked language.")

    point = junction_point(body.street_a, body.street_b)
    if not point:
        raise HTTPException(
            status_code=400,
            detail=f'No real junction found between "{body.street_a}" and "{body.street_b}".',
        )

    rows = execute(
        """
        INSERT INTO forum_posts
            (centre_id, centre_name, test_class, street_a, street_b, lat, lon,
             maneuver_type, outcome, note, user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            centre_id, centre_name, body.test_class, body.street_a, body.street_b,
            point["lat"], point["lon"], body.maneuver_type, body.outcome, body.note,
            user["user_id"],
        ),
    )
    new_post_id = rows[0]["id"]
    notify_mentions(body.note, "forum", new_post_id, None, user["user_id"])
    return {"ok": True, "post_id": new_post_id}


@app.delete("/forum/{post_id}")
@limiter.limit("20/minute")
def delete_forum_post(request: Request, post_id: int, user=Depends(require_user)):
    """The post's own author can always delete it; an admin can delete
    anyone's -- the manual-override layer on top of the automated
    score/report moderation (see list_forum_posts)."""
    post = query("SELECT user_id FROM forum_posts WHERE id = %s", (post_id,), one=True)
    if not post:
        raise HTTPException(status_code=404, detail="No such post.")
    me = get_user_by_id(user["user_id"])
    if post["user_id"] != user["user_id"] and not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed.")
    execute("DELETE FROM forum_posts WHERE id = %s", (post_id,))
    return {"ok": True}


@app.post("/forum/{post_id}/vote")
@limiter.limit("30/minute")
def vote_forum_post(request: Request, post_id: int, body: ForumVote, user=Depends(require_user)):
    if body.value not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="Vote value must be -1, 0, or 1.")
    if not query("SELECT 1 FROM forum_posts WHERE id = %s", (post_id,), one=True):
        raise HTTPException(status_code=404, detail="No such post.")
    if body.value == 0:
        execute(
            "DELETE FROM forum_votes WHERE post_id = %s AND user_id = %s",
            (post_id, user["user_id"]),
        )
    else:
        execute(
            """
            INSERT INTO forum_votes (post_id, user_id, value) VALUES (%s, %s, %s)
            ON CONFLICT (post_id, user_id) DO UPDATE SET value = EXCLUDED.value
            """,
            (post_id, user["user_id"], body.value),
        )
    return {"ok": True}


@app.post("/forum/{post_id}/report")
@limiter.limit("30/minute")
def report_forum_post(request: Request, post_id: int, user=Depends(require_user)):
    """One report per user per post (ON CONFLICT DO NOTHING) -- can't
    be gamed by one account reporting the same post repeatedly."""
    if not query("SELECT 1 FROM forum_posts WHERE id = %s", (post_id,), one=True):
        raise HTTPException(status_code=404, detail="No such post.")
    execute(
        "INSERT INTO forum_reports (post_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (post_id, user["user_id"]),
    )
    return {"ok": True}


@app.post("/forum/{post_id}/image")
@limiter.limit("10/minute")
async def upload_forum_image(request: Request, post_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    """Image types only, checked against the file's real content-type
    (never trusted from a filename), capped at MAX_IMAGE_BYTES. Only
    the post's own author or an admin can attach one."""
    post = query("SELECT user_id FROM forum_posts WHERE id = %s", (post_id,), one=True)
    if not post:
        raise HTTPException(status_code=404, detail="No such post.")
    me = get_user_by_id(user["user_id"])
    if post["user_id"] != user["user_id"] and not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed.")
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only image files are allowed (jpeg, png, webp, gif).")

    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 5MB).")

    rows = execute(
        "INSERT INTO forum_images (post_id, content_type, data) VALUES (%s, %s, %s) RETURNING id",
        (post_id, file.content_type, data),
    )
    return {"ok": True, "image_id": rows[0]["id"]}


@app.get("/forum/image/{image_id}")
def get_forum_image(image_id: int, user=Depends(require_user)):
    row = query("SELECT content_type, data FROM forum_images WHERE id = %s", (image_id,), one=True)
    if not row:
        raise HTTPException(status_code=404, detail="No such image.")
    return Response(content=bytes(row["data"]), media_type=row["content_type"])


@app.get("/forum/{post_id}/comments")
def list_forum_comments(post_id: int, user=Depends(require_user)):
    me = get_user_by_id(user["user_id"])
    rows = query(
        """
        SELECT c.id, c.body, c.created_at, c.user_id, u.username, img.id AS image_id
        FROM forum_comments c
        JOIN users u ON u.id = c.user_id
        LEFT JOIN LATERAL (
            SELECT id FROM forum_images WHERE comment_id = c.id ORDER BY id LIMIT 1
        ) img ON true
        WHERE c.post_id = %s
        ORDER BY c.created_at
        """,
        (post_id,),
    )
    for row in rows:
        row["author"] = display_name(row)
        row["can_delete"] = row["user_id"] == user["user_id"] or me["is_admin"]
    return rows


@app.post("/forum/{post_id}/comments")
def add_forum_comment(post_id: int, body: ForumComment, user=Depends(require_user)):
    me = get_user_by_id(user["user_id"])
    if not me["username"]:
        raise HTTPException(status_code=400, detail="Set a username before commenting.")

    post = query("SELECT user_id FROM forum_posts WHERE id = %s", (post_id,), one=True)
    if not post:
        raise HTTPException(status_code=404, detail="No such post.")

    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Comment cannot be empty.")
    if over_comment_rate_limit(user["user_id"]):
        raise HTTPException(
            status_code=429,
            detail=f"Daily comment limit reached ({COMMENT_DAILY_LIMIT}/day). Try again tomorrow.",
        )
    if contains_blocked_word(text):
        raise HTTPException(status_code=400, detail="Comment contains blocked language.")

    rows = execute(
        "INSERT INTO forum_comments (post_id, user_id, body) VALUES (%s, %s, %s) RETURNING id",
        (post_id, user["user_id"], text),
    )
    new_comment_id = rows[0]["id"]
    notify_comment(post["user_id"], "forum", post_id, new_comment_id, user["user_id"], text)
    notify_mentions(text, "forum", post_id, new_comment_id, user["user_id"])
    return {"ok": True, "comment_id": new_comment_id}


@app.post("/forum/comments/{comment_id}/image")
@limiter.limit("10/minute")
async def upload_forum_comment_image(request: Request, comment_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    """Same rules as post images: real content-type checked against
    ALLOWED_IMAGE_TYPES, capped at MAX_IMAGE_BYTES, only the comment's
    own author or an admin can attach one. Served back through the same
    /forum/image/{id} endpoint as post images -- one image row is one
    image regardless of what it's attached to."""
    c = query("SELECT user_id FROM forum_comments WHERE id = %s", (comment_id,), one=True)
    if not c:
        raise HTTPException(status_code=404, detail="No such comment.")
    me = get_user_by_id(user["user_id"])
    if c["user_id"] != user["user_id"] and not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed.")
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only image files are allowed (jpeg, png, webp, gif).")

    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 5MB).")

    rows = execute(
        "INSERT INTO forum_images (comment_id, content_type, data) VALUES (%s, %s, %s) RETURNING id",
        (comment_id, file.content_type, data),
    )
    return {"ok": True, "image_id": rows[0]["id"]}


@app.delete("/forum/comments/{comment_id}")
@limiter.limit("20/minute")
def delete_forum_comment(request: Request, comment_id: int, user=Depends(require_user)):
    c = query("SELECT user_id FROM forum_comments WHERE id = %s", (comment_id,), one=True)
    if not c:
        raise HTTPException(status_code=404, detail="No such comment.")
    me = get_user_by_id(user["user_id"])
    if c["user_id"] != user["user_id"] and not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed.")
    execute("DELETE FROM forum_comments WHERE id = %s", (comment_id,))
    return {"ok": True}


# --------------------------------------------------------- discussions ---
# General Q&A / comment board -- separate feature from the forum above,
# not a generalization of it. No junction requirement: a discussion
# post isn't anchored to a real location the way a forum post is.

@app.get("/discussions")
def list_discussions(
    centre_id: str | None = None,
    test_type: str | None = None,
    post_type: str | None = None,
    sort: str = "newest",
    user=Depends(require_user),
):
    me = get_user_by_id(user["user_id"])
    has_tags = discussions.has_tag_columns()
    tag_select = "p.test_type, p.post_type, p.route_line_id," if has_tags else "NULL AS test_type, NULL AS post_type, NULL AS route_line_id,"
    tag_filter = "AND (%(tt)s IS NULL OR p.test_type = %(tt)s) AND (%(pt)s IS NULL OR p.post_type = %(pt)s)" if has_tags else ""
    order = {
        "top": "COALESCE(v.score, 0) DESC, p.created_at DESC",
        "discussed": "COALESCE(c.comment_count, 0) DESC, p.created_at DESC",
    }.get(sort, "p.created_at DESC")
    rows = query(
        f"""
        SELECT p.id, p.centre_id, p.centre_name, p.title, p.body, p.created_at, p.user_id,
               {tag_select}
               u.username,
               COALESCE(v.score, 0) AS score,
               COALESCE(c.comment_count, 0) AS comment_count,
               myv.value AS my_vote,
               img.id AS image_id,
               (COALESCE(v.score, 0) <= %(score_floor)s
                OR COALESCE(r.report_count, 0) >= %(report_ceiling)s) AS hidden
        FROM discussion_posts p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN (SELECT post_id, SUM(value) AS score FROM discussion_votes GROUP BY post_id)
            v ON v.post_id = p.id
        LEFT JOIN (SELECT post_id, COUNT(*) AS comment_count FROM discussion_comments GROUP BY post_id)
            c ON c.post_id = p.id
        LEFT JOIN (SELECT post_id, COUNT(*) AS report_count FROM discussion_reports GROUP BY post_id)
            r ON r.post_id = p.id
        LEFT JOIN discussion_votes myv ON myv.post_id = p.id AND myv.user_id = %(uid)s
        LEFT JOIN LATERAL (
            SELECT id FROM discussion_images WHERE post_id = p.id ORDER BY id LIMIT 1
        ) img ON true
        WHERE (%(cid)s IS NULL OR p.centre_id = %(cid)s)
          {tag_filter}
          AND (
            %(is_admin)s
            OR (COALESCE(v.score, 0) > %(score_floor)s AND COALESCE(r.report_count, 0) < %(report_ceiling)s)
          )
        ORDER BY {order}
        """,
        {
            "cid": centre_id,
            "tt": test_type,
            "pt": post_type,
            "uid": user["user_id"],
            "is_admin": me["is_admin"],
            "score_floor": discussions.SCORE_HIDE_THRESHOLD,
            "report_ceiling": discussions.REPORT_HIDE_THRESHOLD,
        },
    )
    for row in rows:
        row["author"] = display_name(row)
        row["can_delete"] = row["user_id"] == user["user_id"] or me["is_admin"]
    return rows


@app.post("/discussions")
def create_discussion(body: DiscussionPost, user=Depends(require_user)):
    me = get_user_by_id(user["user_id"])
    if not me["username"]:
        raise HTTPException(status_code=400, detail="Set a username before posting.")

    centre_id = body.centre_id
    if centre_id:
        centre = query("SELECT id FROM centres WHERE id = %s", (centre_id,), one=True)
        if not centre:
            raise HTTPException(status_code=404, detail="No such centre.")

    title = body.title.strip()
    text = body.body.strip()
    if not title or not text:
        raise HTTPException(status_code=400, detail="Title and body are required.")

    if discussions.over_post_rate_limit(user["user_id"]):
        raise HTTPException(
            status_code=429,
            detail=f"Daily post limit reached ({discussions.POST_DAILY_LIMIT}/day). Try again tomorrow.",
        )
    if contains_blocked_word(title) or contains_blocked_word(text):
        raise HTTPException(status_code=400, detail="Post contains blocked language.")

    if body.test_type and body.test_type not in ("G", "G2"):
        raise HTTPException(status_code=400, detail="test_type must be 'G' or 'G2'.")
    if body.post_type and body.post_type not in ("question", "experience", "tip"):
        raise HTTPException(status_code=400, detail="Invalid post_type.")
    route_line_id = body.route_line_id
    if route_line_id:
        route = query("SELECT id FROM route_lines WHERE id = %s", (route_line_id,), one=True)
        if not route:
            raise HTTPException(status_code=404, detail="No such route.")

    if discussions.has_tag_columns():
        rows = execute(
            """
            INSERT INTO discussion_posts (centre_id, centre_name, title, body, user_id, test_type, post_type, route_line_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (centre_id, body.centre_name, title, text, user["user_id"], body.test_type, body.post_type, route_line_id),
        )
    else:
        rows = execute(
            """
            INSERT INTO discussion_posts (centre_id, centre_name, title, body, user_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (centre_id, body.centre_name, title, text, user["user_id"]),
        )
    new_post_id = rows[0]["id"]
    notify_mentions(text, "discussion", new_post_id, None, user["user_id"])
    return {"ok": True, "post_id": new_post_id}


@app.delete("/discussions/{post_id}")
@limiter.limit("20/minute")
def delete_discussion(request: Request, post_id: int, user=Depends(require_user)):
    post = query("SELECT user_id FROM discussion_posts WHERE id = %s", (post_id,), one=True)
    if not post:
        raise HTTPException(status_code=404, detail="No such post.")
    me = get_user_by_id(user["user_id"])
    if post["user_id"] != user["user_id"] and not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed.")
    execute("DELETE FROM discussion_posts WHERE id = %s", (post_id,))
    return {"ok": True}


@app.post("/discussions/{post_id}/image")
@limiter.limit("10/minute")
async def upload_discussion_image(request: Request, post_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    """Same rules as the forum's image upload: real content-type checked
    against ALLOWED_IMAGE_TYPES (never trusted from a filename), capped
    at MAX_IMAGE_BYTES, only the post's own author or an admin can
    attach one."""
    post = query("SELECT user_id FROM discussion_posts WHERE id = %s", (post_id,), one=True)
    if not post:
        raise HTTPException(status_code=404, detail="No such post.")
    me = get_user_by_id(user["user_id"])
    if post["user_id"] != user["user_id"] and not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed.")
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only image files are allowed (jpeg, png, webp, gif).")

    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 5MB).")

    rows = execute(
        "INSERT INTO discussion_images (post_id, content_type, data) VALUES (%s, %s, %s) RETURNING id",
        (post_id, file.content_type, data),
    )
    return {"ok": True, "image_id": rows[0]["id"]}


@app.get("/discussions/image/{image_id}")
def get_discussion_image(image_id: int, user=Depends(require_user)):
    row = query("SELECT content_type, data FROM discussion_images WHERE id = %s", (image_id,), one=True)
    if not row:
        raise HTTPException(status_code=404, detail="No such image.")
    return Response(content=bytes(row["data"]), media_type=row["content_type"])


@app.post("/discussions/{post_id}/vote")
@limiter.limit("30/minute")
def vote_discussion(request: Request, post_id: int, body: DiscussionVote, user=Depends(require_user)):
    if body.value not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="Vote value must be -1, 0, or 1.")
    if not query("SELECT 1 FROM discussion_posts WHERE id = %s", (post_id,), one=True):
        raise HTTPException(status_code=404, detail="No such post.")
    if body.value == 0:
        execute(
            "DELETE FROM discussion_votes WHERE post_id = %s AND user_id = %s",
            (post_id, user["user_id"]),
        )
    else:
        execute(
            """
            INSERT INTO discussion_votes (post_id, user_id, value) VALUES (%s, %s, %s)
            ON CONFLICT (post_id, user_id) DO UPDATE SET value = EXCLUDED.value
            """,
            (post_id, user["user_id"], body.value),
        )
    return {"ok": True}


@app.post("/discussions/{post_id}/report")
@limiter.limit("30/minute")
def report_discussion(request: Request, post_id: int, user=Depends(require_user)):
    if not query("SELECT 1 FROM discussion_posts WHERE id = %s", (post_id,), one=True):
        raise HTTPException(status_code=404, detail="No such post.")
    execute(
        "INSERT INTO discussion_reports (post_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (post_id, user["user_id"]),
    )
    return {"ok": True}


@app.get("/discussions/{post_id}/comments")
def list_discussion_comments(post_id: int, sort: str = "newest", user=Depends(require_user)):
    """Returns a flat list -- each row carries parent_comment_id so the
    frontend builds the reply tree client-side, same division of labour
    as the rest of this app (query computes truth, React shapes it).
    `sort` only orders top-level comments; replies under a parent always
    stay oldest-first underneath it, matching how Reddit itself nests."""
    me = get_user_by_id(user["user_id"])
    order_col = "score" if sort == "score" else "c.created_at"
    order_dir = "DESC" if sort == "score" else "ASC"
    rows = query(
        f"""
        SELECT c.id, c.body, c.created_at, c.user_id, c.parent_comment_id, u.username,
               img.id AS image_id, COALESCE(v.score, 0) AS score, myv.value AS my_vote
        FROM discussion_comments c
        JOIN users u ON u.id = c.user_id
        LEFT JOIN LATERAL (
            SELECT id FROM discussion_images WHERE comment_id = c.id ORDER BY id LIMIT 1
        ) img ON true
        LEFT JOIN (SELECT comment_id, SUM(value) AS score FROM discussion_comment_votes GROUP BY comment_id)
            v ON v.comment_id = c.id
        LEFT JOIN discussion_comment_votes myv ON myv.comment_id = c.id AND myv.user_id = %(uid)s
        WHERE c.post_id = %(post_id)s
        ORDER BY (c.parent_comment_id IS NOT NULL), {order_col} {order_dir}
        """,
        {"uid": user["user_id"], "post_id": post_id},
    )
    for row in rows:
        row["author"] = display_name(row)
        row["can_delete"] = row["user_id"] == user["user_id"] or me["is_admin"]
    return rows


@app.post("/discussions/{post_id}/comments")
def add_discussion_comment(post_id: int, body: DiscussionComment, user=Depends(require_user)):
    me = get_user_by_id(user["user_id"])
    if not me["username"]:
        raise HTTPException(status_code=400, detail="Set a username before commenting.")

    post = query("SELECT user_id FROM discussion_posts WHERE id = %s", (post_id,), one=True)
    if not post:
        raise HTTPException(status_code=404, detail="No such post.")

    parent_id = body.parent_comment_id
    if parent_id is not None:
        parent = query(
            "SELECT id FROM discussion_comments WHERE id = %s AND post_id = %s",
            (parent_id, post_id),
            one=True,
        )
        if not parent:
            raise HTTPException(status_code=404, detail="No such comment to reply to.")

    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Comment cannot be empty.")
    if discussions.over_comment_rate_limit(user["user_id"]):
        raise HTTPException(
            status_code=429,
            detail=f"Daily comment limit reached ({discussions.COMMENT_DAILY_LIMIT}/day). Try again tomorrow.",
        )
    if contains_blocked_word(text):
        raise HTTPException(status_code=400, detail="Comment contains blocked language.")

    rows = execute(
        "INSERT INTO discussion_comments (post_id, parent_comment_id, user_id, body) VALUES (%s, %s, %s, %s) RETURNING id",
        (post_id, parent_id, user["user_id"], text),
    )
    new_comment_id = rows[0]["id"]
    notify_comment(post["user_id"], "discussion", post_id, new_comment_id, user["user_id"], text)
    notify_mentions(text, "discussion", post_id, new_comment_id, user["user_id"])
    return {"ok": True, "comment_id": new_comment_id}


@app.post("/discussions/comments/{comment_id}/vote")
@limiter.limit("30/minute")
def vote_discussion_comment(request: Request, comment_id: int, body: DiscussionCommentVote, user=Depends(require_user)):
    if body.value not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="Vote value must be -1, 0, or 1.")
    if not query("SELECT 1 FROM discussion_comments WHERE id = %s", (comment_id,), one=True):
        raise HTTPException(status_code=404, detail="No such comment.")
    if body.value == 0:
        execute(
            "DELETE FROM discussion_comment_votes WHERE comment_id = %s AND user_id = %s",
            (comment_id, user["user_id"]),
        )
    else:
        execute(
            """
            INSERT INTO discussion_comment_votes (comment_id, user_id, value) VALUES (%s, %s, %s)
            ON CONFLICT (comment_id, user_id) DO UPDATE SET value = EXCLUDED.value
            """,
            (comment_id, user["user_id"], body.value),
        )
    return {"ok": True}


@app.post("/discussions/comments/{comment_id}/image")
@limiter.limit("10/minute")
async def upload_discussion_comment_image(request: Request, comment_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    c = query("SELECT user_id FROM discussion_comments WHERE id = %s", (comment_id,), one=True)
    if not c:
        raise HTTPException(status_code=404, detail="No such comment.")
    me = get_user_by_id(user["user_id"])
    if c["user_id"] != user["user_id"] and not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed.")
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only image files are allowed (jpeg, png, webp, gif).")

    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 5MB).")

    rows = execute(
        "INSERT INTO discussion_images (comment_id, content_type, data) VALUES (%s, %s, %s) RETURNING id",
        (comment_id, file.content_type, data),
    )
    return {"ok": True, "image_id": rows[0]["id"]}


@app.delete("/discussions/comments/{comment_id}")
@limiter.limit("20/minute")
def delete_discussion_comment(request: Request, comment_id: int, user=Depends(require_user)):
    c = query("SELECT user_id FROM discussion_comments WHERE id = %s", (comment_id,), one=True)
    if not c:
        raise HTTPException(status_code=404, detail="No such comment.")
    me = get_user_by_id(user["user_id"])
    if c["user_id"] != user["user_id"] and not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed.")
    execute("DELETE FROM discussion_comments WHERE id = %s", (comment_id,))
    return {"ok": True}


# ------------------------------------------------------- notifications ---
# In-app only, see notifications.py -- no email, no push.

def _materialize_due_reminders(user_id: int) -> None:
    """Turns any of this user's booking_reminders whose remind_at has
    arrived into a real notifications row, then marks it notified so it's
    only ever materialized once. Done at read time (here) rather than by
    a background job -- this project has no scheduler process, and the
    forum's own moderation already uses the same "compute at query time,
    never drift out of sync" approach. Best-effort like the rest of
    notifications.py: never let a failure here block the notification
    list itself from loading."""
    try:
        due = query(
            "SELECT id, centre_id, note FROM booking_reminders "
            "WHERE user_id = %s AND remind_at <= CURRENT_DATE AND NOT notified",
            (user_id,),
        )
        for r in due:
            excerpt = r["note"] or (f"centre: {r['centre_id']}" if r["centre_id"] else None)
            execute(
                """
                INSERT INTO notifications (user_id, kind, source, excerpt)
                VALUES (%s, 'booking_reminder', 'booking_reminder', %s)
                """,
                (user_id, excerpt),
            )
            execute("UPDATE booking_reminders SET notified = true WHERE id = %s", (r["id"],))
    except Exception:
        pass


@app.get("/notifications")
def list_notifications(user=Depends(require_user)):
    _materialize_due_reminders(user["user_id"])
    return query(
        """
        SELECT n.id, n.kind, n.source, n.post_id, n.comment_id, n.excerpt, n.read, n.created_at,
               u.username AS actor_username, u.id AS actor_user_id
        FROM notifications n
        LEFT JOIN users u ON u.id = n.actor_user_id
        WHERE n.user_id = %s
        ORDER BY n.created_at DESC
        LIMIT 30
        """,
        (user["user_id"],),
    )


# ---------------------------------------------------- booking reminders ---
# Appointment-opening notifier, scoped down from real scraping -- see
# schema.sql's booking_reminders comment for why. This is a plain
# self-set reminder, not a live check of drivetest.ca.

@app.post("/booking-reminders")
def create_booking_reminder(body: BookingReminder, user=Depends(require_user)):
    if body.centre_id:
        centre = query("SELECT id FROM centres WHERE id = %s", (body.centre_id,), one=True)
        if not centre:
            raise HTTPException(status_code=404, detail="No such centre.")
    rows = execute(
        """
        INSERT INTO booking_reminders (user_id, centre_id, remind_at, note)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (user["user_id"], body.centre_id, body.remind_at, body.note),
    )
    return {"ok": True, "id": rows[0]["id"]}


@app.get("/booking-reminders")
def list_booking_reminders(user=Depends(require_user)):
    return query(
        """
        SELECT id, centre_id, remind_at, note, notified, created_at
        FROM booking_reminders
        WHERE user_id = %s
        ORDER BY remind_at
        """,
        (user["user_id"],),
    )


@app.delete("/booking-reminders/{reminder_id}")
@limiter.limit("20/minute")
def delete_booking_reminder(request: Request, reminder_id: int, user=Depends(require_user)):
    execute(
        "DELETE FROM booking_reminders WHERE id = %s AND user_id = %s",
        (reminder_id, user["user_id"]),
    )
    return {"ok": True}


@app.get("/notifications/unread-count")
def unread_notification_count(user=Depends(require_user)):
    row = query(
        "SELECT COUNT(*) AS n FROM notifications WHERE user_id = %s AND read = false",
        (user["user_id"],),
        one=True,
    )
    return {"count": row["n"]}


@app.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user=Depends(require_user)):
    execute(
        "UPDATE notifications SET read = true WHERE id = %s AND user_id = %s",
        (notification_id, user["user_id"]),
    )
    return {"ok": True}


@app.post("/notifications/read-all")
def mark_all_notifications_read(user=Depends(require_user)):
    execute(
        "UPDATE notifications SET read = true WHERE user_id = %s AND read = false",
        (user["user_id"],),
    )
    return {"ok": True}
