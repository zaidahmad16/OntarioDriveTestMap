# HANDOFF

This file did not exist for three sessions in a row (three separate debugging/
verification passes each flagged it missing before proceeding anyway). Writing
it for real now, as of 2026-09-13.

## Deployed / running state

**Backend** is deployed on Railway: `https://backend-production-0115.up.railway.app`
(FastAPI, `backend/main.py`, Python 3.12 — pinned because `psycopg2-binary` has
no 3.13 wheel yet). Auth is Google Identity Services: the frontend gets an ID
token from Google, the backend verifies it server-side and issues its own
short-lived JWT in an httpOnly `session` cookie (7-day expiry). `/centres` and
`/centres/{id}` are public; everything else (`/centres/{id}/map`,
`/centres/{id}/traces`, `/traces/{id}`, `/auth/me`) requires that cookie.

**Frontend** has **no production deployment yet** — dev-only. Run locally:

```
cd frontend
npm install
npm run dev          # Vite, http://localhost:5173
```

`frontend/.env` points `VITE_API_URL` at `http://localhost:8000` (a local
backend), not the Railway URL — for local dev, also run the backend locally
(`cd backend && uvicorn main:app --port 8000`), pointed at the same Postgres
via `DATABASE_PUBLIC_URL` in the repo-root `.env` (the `.railway.internal` host
in `DATABASE_URL` only resolves from inside Railway itself; `db.py` already
prefers `DATABASE_PUBLIC_URL` when both are set, so this works unmodified from
a laptop). To point the local frontend at the real Railway backend instead,
change `VITE_API_URL` and add `http://localhost:5173` to the Railway
service's `FRONTEND_ORIGIN` env var (CORS `allow_origins` is a single value,
read from that env var at startup).

Database: Railway Postgres. Public proxy connection string is
`DATABASE_PUBLIC_URL` in the repo-root `.env` — use that for any direct
`psql`/script access from outside Railway.

## Schema, as it actually stands now

`schema.sql` (repo root) plus `backend/users_schema.sql` (run separately, not
part of the main file). As of this session:

- `centres(id, name)`
- `traces(id, source_id, centre_id, test_class, reliability, observed_at, author_hash, status, created_at)`
- `trace_turns(id, trace_id, turn_order, direction, street)`
- `trace_waypoints(id, trace_id, waypoint_order, node_id, lat, lon, pair_street_a, pair_street_b, candidates)`
- `consensus_segments(id, centre_id, street_a, street_b, lat, lon, authors, weight, video_count, text_count, last_seen, junction_type, node_id)`
- `route_lines(id, centre_id, family, run, trace_count, authors, distance_m, geometry, test_class, mixed_classes)`
  — **`test_class`/`mixed_classes` are new this session.** Added via `ALTER
  TABLE` against the live DB (also reflected in `schema.sql` for anyone doing
  a fresh install). Backfilled for all 10 currently-published route_lines
  using `backfill_route_test_class.py` (repo root) — see Known-open below for
  what "backfilled" actually got populated.
- `route_line_segments(id, route_line_id, segment_order, street_a, street_b, authors, video_count, text_count, weight, junction_type, last_seen)`
- `users(id, google_sub, email, name, created_at, last_login)` — separate file, `backend/users_schema.sql`

Indexes: `idx_traces_centre`, `idx_segments_centre`, `idx_routes_centre` (all
on `centre_id`).

`GET /centres/{id}/map` returns one `FeatureCollection`: `route_line`
LineString features (now carrying `test_class`/`mixed_classes` in
`properties`) and `segment` Point features (individual scored junctions —
these are NOT connected into lines; most centres have far more of these than
they have route_lines, which is why the map can look mostly like disconnected
dots rather than drawn routes for centres with few published route_lines —
that's the data shape, not a bug).

## How test_class/mixed_classes actually gets computed

`common/classvote.py` — `class_vote(test_classes)` — is the single
implementation, used by both:
- `analysis/consensus_geometry.py`, at original-clustering time (in-memory,
  has the true trace→family membership), and
- `backfill_route_test_class.py`, for already-published route_lines where
  that in-memory membership isn't available — it reconstructs "which traces
  fed this route_line" by matching each route_line's own street-pair segments
  against each trace's consecutive-turn segments, crediting a trace only if
  it covers at least half the route_line's own segments (a looser "any
  overlap counts" version was tried first and found to produce false
  attributions via generic shared hub junctions — see Build Log).

Only confirmed `"G"`/`"G2"` trace labels count as votes. `"ambiguous"`
(a trace's text matched both G and G2 keyword patterns in
`extraction/process_reddit_v2.py`'s `guess_class()`) and `"unknown"`
(matched neither) abstain — they're uncertainty about the trace, not a third
confirmed class, and were previously being counted as peer votes, which
produced several false "mixed" results (see Build Log for the specific
before/after).

## Known-open items

- **4 of the 10 currently-published route_lines have `test_class = NULL`**
  after the corrected backfill: Walkley family 2 run 0, and family 3's runs 1,
  3, and 4. This means: no trace currently in the `traces` table can be
  confidently tied (≥half-segment overlap) to that specific route_line with a
  confirmed G/G2 label. The geometry itself is still real, validated route
  data — it's just unlabeled. `MapView.jsx` renders these as a solid gray
  line with a "class unknown" popup, visually distinct from both G/G2
  (colored) and a genuinely mixed family (gray **dashed** — that style exists
  in the code but has zero live examples right now; all 3 previously-flagged
  "mixed" families turned out to be single-class once ambiguous/unknown
  stopped voting).
- **Smiths Falls route-line clustering** is a separate, still-open
  investigation (whether re-clustering the current 7-trace corpus finds real
  route families at all — Smiths Falls currently has 0 published route_lines,
  which the backend comment already notes is expected, not a bug, given how
  little data exists there). Not touched by any of the test_class work above;
  don't conflate the two.
- **Frontend has no production deployment.** Whoever picks this up next needs
  to decide hosting (the domain `ontariodrivetestmap.fyi` is referenced in
  Notion — see below) and wire `VITE_API_URL`/`FRONTEND_ORIGIN` for prod.
- **Feature branch `feat/route-test-class-map`** (this session's frontend
  hardening + test_class schema/API/backfill work, 3 commits) exists locally
  but has **not been pushed** — this environment had no GitHub credentials
  configured (`git push` failed with no stored auth, no `gh` CLI installed).
  Push it yourself once you have credentials available.

## Where to look for more detail

Project tracking lives in Notion, under "Ontario Drive Test Routes — Project
Idea":
- **Build Log** — running log of what actually got built, when:
  `https://app.notion.com/p/3cb6d8d941d7818b986efdcd84fa2fa6`
- **Report Material** — raw material for the end-of-project write-up,
  distinct from the Build Log:
  `https://app.notion.com/p/3cb6d8d941d781c6b987d8b1c4abf991`
- **Route Consensus — Design Doc**:
  `https://app.notion.com/p/3ca6d8d941d78173a6cacf818cd4ce28`
- Parent page: **Ontario Drive Test Routes — Project Idea**:
  `https://app.notion.com/p/3ca6d8d941d78142b066e4a9263fa9ee`

The mixed-class heuristic's before/after numbers (spurious v1 backfill vs.
tightened-threshold-plus-classvote-fix v2) are exactly the kind of thing that
belongs in the Build Log — "the plan was wrong" is treated as material worth
recording on this project, not something to bury. Worth adding an entry there
if one doesn't already exist.
