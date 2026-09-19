# OntarioDriveTestMap — Project Handoff

Repo: `~/Documents/GitHub/OntarioDriveTestMap`. This is a portfolio project:
crowdsourced Ontario G/G2 driving-test route reconstruction from Reddit
posts and YouTube dashcam footage, now being turned into a live web app.
Solo project, no deadline. I (the user) code through chat, not Claude
Code, for anything touching the data pipeline — treat Claude Code as a
separate tool I use myself for scoped tasks, not the primary collaborator.

Working style, read this first: verify before trusting. Check file
timestamps before assuming a file is current. Check `--help` output
before guessing a flag. Check actual table/column names before writing
SQL. Check git log/status before assuming a push succeeded. This project
has hit real bugs tonight from skipping exactly these checks — don't
repeat them. When something fails, find the root cause, don't paper over
it. State assumptions explicitly rather than silently picking one.

This file was missing for three Claude Code sessions in a row before this
version — each flagged it and proceeded anyway. It's real now. Note on
provenance: Parts 1–3 and 5 below are the original handoff, written from
firsthand knowledge of the data pipeline and deployment work. Part 4 has
been updated by a later Claude Code session that actually ran and debugged
the frontend (see the note at the top of Part 4). Part 6 is new work from
that same later session, appended rather than merged in, so it doesn't
dilute the original account.

---

## Part 1 — Data pipeline (this part is DONE, do not redo)

Four launch centres, each an Ontario DriveTest location: Walkley
(Ottawa), Canotek (Ottawa), Smiths Falls, Winchester.

**Walkley** — fully done, the reference standard. 30 traces, 3 route
families, F1 0.86 per-family / 0.74 pooled (current, post-corpus-growth
numbers — an older "0.86 pooled" figure floating around earlier
conversations is stale, don't trust it).

**Canotek** — done. 23 traces (Reddit + video), F1 0.78 at threshold
0.30, half-life 5yr. The 53-thread Reddit backlog was fully,
exhaustively read (not just triaged) — nothing left to mine there
without genuinely new source material. Weighted-vs-frequency consensus
checked directly (not just via aggregate F1) and confirmed to be doing
real, sensible work (a deliberate precision/recall tradeoff), not
degenerate convergence.

**Smiths Falls** — done, but thinner. 7 traces, F1 0.78-0.89 depending
on exact threshold/min-segments config (small corpus, less stable than
the others). The 58-thread Reddit backlog was fully, exhaustively read.
G2 core is fully double-corroborated (two independent sources — a Reddit
account and a YouTube video's burned-in captions — agree on every
segment of the loop). G class is thin. A Google Maps link one user
shared resolved a real bridge between the residential G2 core and the
Jasper Ave highway loop, cross-confirmed against an independent Reddit
account at the exact same junction node.

**Winchester** — explicitly SKIPPED, out of scope, at my direction. Its
Reddit batch (12 threads) yielded zero usable street names. Its entire
YouTube video batch (16 videos) turned out to be about Winchester,
*England*, not Ontario — a real bug in the collection tool's centre-
attribution logic was found and permanently fixed (`disqualifiers` list
added to `acquisition/count_sources.py`'s Winchester config), but the
centre itself stays parked unless I explicitly ask to resume it. Its
database rows are correctly all zero.

**Known, accepted limitations, not open bugs:** a handful of individual
traces have single unresolvable segments (a street pair that doesn't
share a real junction in the OSM extract) — these are known, logged,
and correctly excluded/dropped by the pipeline's own "no junction on
graph" handling, not something to chase further.

**Codebase cleanup (done):** structural cleanup, comment-quality pass,
and two real correctness bugs found and fixed this session:
- `consensus_geometry.py`'s route-line assembly was non-deterministic
  (Python hash-seed randomization leaking into a set iteration) — fixed,
  verified across 5 runs × 3 centres, byte-identical every time.
- `consensus_geometry.py`'s `age_weight()` was silently still on the
  old pre-tuning half-life (3yr) while `build_consensus.py` and
  `validate_consensus.py` were correctly on 5yr — fixed. Real impact
  was zero (no segment's publish/drop status ever changed because of
  it), but it was a real inconsistency worth catching.
- `build_consensus.py --report` means "print the table, write nothing"
  — I made this mistake twice tonight trying to regenerate
  `consensus.geojson` for Canotek/Smiths Falls with `--out` and
  `--report` together, silently producing zero file writes both times.
  Caught via file timestamps, not trusted output. Both files are now
  genuinely regenerated and current as of tonight.

---

## Part 2 — Database (Railway Postgres) — DONE

Railway project has two services: Postgres + backend (see Part 3).

Connection: `DATABASE_PUBLIC_URL` in the repo root `.env` (the public
proxy address, works from a laptop). `DATABASE_URL` in `.env` is the
*internal* `postgres.railway.internal` address — only resolves from
inside Railway's own network, useless locally. The backend code
(`backend/db.py`) checks `DATABASE_PUBLIC_URL` first, falls back to
`DATABASE_URL` — this makes it work both locally and once deployed.

PostGIS is NOT available on Railway's standard Postgres image (checked
directly, `CREATE EXTENSION postgis` fails). Deliberately not pursuing
a custom Docker image for it — the data's real scale (hundreds of
points, not millions) doesn't need it. Schema uses plain
`DOUBLE PRECISION` lat/lon columns.

**Schema** (`backend/schema.sql`, already run against Railway):
- `centres` (id, name)
- `traces` (source_id, centre_id, test_class, reliability, observed_at,
  author_hash, status) — one row per collected Reddit/video account
- `trace_turns` (trace_id, turn_order, direction, street) — ordered
  turns within a trace
- `trace_waypoints` (trace_id, waypoint_order, node_id, lat, lon,
  pair_street_a, pair_street_b, candidates) — resolved coordinates once
  snapped
- `consensus_segments` (centre_id, street_a, street_b, lat, lon,
  authors, weight, video_count, text_count, last_seen, junction_type,
  node_id) — the real, scored junction-confidence points
- `route_lines` (centre_id, family, run, trace_count, authors,
  distance_m, geometry JSONB) — actual drawable route paths as
  `[lon, lat]` coordinate arrays. **Updated in Part 6**: now also has
  `test_class`/`mixed_classes` columns.
- `route_line_segments` — the segments making up one route line, in
  order
- `users` (google_sub, email, name, created_at, last_login) — added
  separately via `backend/users_schema.sql`, no passwords anywhere

**Migration script**: `scripts/migrate_to_postgres.py`. Idempotent
— deletes each centre's rows before reinserting, so reruns always
reflect current JSON state. Dedupes `all_traces.json` against
`reddit_traces.json`/`ocr_traces.json` (Canotek's `all_traces.json` is
those two files concatenated). Approximate dates like `~2023-09` get
the `~` stripped and padded to `-01` since Postgres `DATE` needs a full
day.

**Current real row counts, verified directly via SQL** (not just
trusted from script output):
| centre | traces | consensus_segments | route_lines |
|---|---|---|---|
| canotek | 23 | 29 | 2 |
| smithsfalls | 7 | 20 | 0 |
| walkley | 26 | 52 | 8 |
| winchester | 0 | 0 | 0 |

Smiths Falls' 0 route_lines is real and correct — its corpus is too
thin for HDBSCAN to find real route-family structure, not a bug.

---

## Part 3 — Backend (FastAPI on Railway) — DONE AND DEPLOYED

Live at: **https://backend-production-0115.up.railway.app**

Code lives in `backend/` (separate from the data pipeline code — never
mix these). Files: `main.py` (routes), `auth.py` (Google verification +
session JWT), `db.py` (connection pool), `requirements.txt`, `Procfile`,
`.python-version` (pinned to 3.12 — see deployment gotchas below),
`users_schema.sql`.

**Auth design, explicit decision:** Google Identity Services in the
browser gets an ID token directly from Google → frontend POSTs it to
`POST /auth/google` → backend verifies it against Google's own public
keys via the `google-auth` library (never trusts the frontend's claim)
→ backend issues its own short-lived JWT in an `httpOnly` cookie (7-day
expiry) → every protected route checks that cookie via the
`require_user` dependency. No passwords anywhere in the system.

**Endpoints:**
- `POST /auth/google` — body `{"credential": "<google id token>"}`,
  sets session cookie, returns `{email, name}`
- `POST /auth/logout` — clears cookie
- `GET /auth/me` — protected, returns current user
- `GET /centres` — PUBLIC (no auth needed) — list with counts, meant
  for a landing page before sign-in
- `GET /centres/{id}` — PUBLIC — single centre detail
- `GET /centres/{id}/map` — PROTECTED — one combined GeoJSON
  FeatureCollection: route_lines as LineString features + consensus_
  segments as Point features, `properties.kind` distinguishes them.
  **Updated in Part 6**: route_line features now also carry
  `test_class`/`mixed_classes`.
- `GET /centres/{id}/traces` — PROTECTED — raw trace list (the
  "transparency" / sources view)
- `GET /traces/{id}` — PROTECTED — one trace's full turns + waypoints

**Explicit design decision, stated not assumed:** "users need to sign
in with Google to view the routes" was interpreted as: centre metadata
(names, counts) is public for a landing page, but actual route
geometry (`/map`, `/traces`, individual trace detail) requires
sign-in. Revisit this if that reading is wrong.

**Environment variables set on the Railway backend service** (Variables
tab — this was empty at first and caused a crash, now populated):
`DATABASE_URL` (linked via Railway's internal reference to the Postgres
service, not the public proxy), `GOOGLE_CLIENT_ID`, `JWT_SECRET`,
`FRONTEND_ORIGIN` (currently `http://localhost:5173` — MUST be updated
to the real deployed frontend URL once that exists, or CORS will
reject it).

**Google OAuth credentials** (from Google Cloud Console, project
"OntarioDriveTestMap"):
- Client ID: `259265107986-oue1v1u2lmitln92jaaao4ff9ht3iog9.apps.googleusercontent.com`
  (safe to reference, not secret)
- Client Secret: in `.env` locally, not reproduced here
- Authorized JavaScript origins / redirect URIs currently only include
  `http://localhost:5173` — needs the real frontend URL added once
  deployed, or sign-in will fail in production

**JWT_SECRET** (generated fresh this session, reused across local +
Railway): value is in `.env` and in Railway's Variables tab already —
don't regenerate, reuse the existing one everywhere.

**Real deployment bugs hit and fixed, in order — useful if the frontend
deploy hits similar issues:**
1. `psycopg2-binary` has no pre-built wheel for Python 3.13 (Railway's
   default), source-compile fails on a Python 3.13 C-API change. Fixed
   by adding `backend/.python-version` containing `3.12`. First attempt
   at this fix silently failed because the command was run from inside
   `backend/` while still using the path `backend/.python-version`,
   creating nothing — caught by checking `git log`, not assumed.
2. `google-auth`'s transport layer needs the `requests` library
   directly; it was missing from `requirements.txt` (worked locally
   only because it was already present transitively in the existing
   venv). Added explicitly.
3. The Railway service had zero environment variables configured —
   Variables tab was completely empty. Had to add all four manually.

**`python-dotenv` gotcha:** `backend/db.py` and `backend/auth.py` both
call `load_dotenv()` so the repo-root `.env` loads automatically — no
manual `export` needed when running the backend itself. But raw `psql`
commands against the database still need manual export:
`export $(grep DATABASE_PUBLIC_URL .env | xargs)` before any direct
`psql "$DATABASE_PUBLIC_URL" ...` command, every new terminal session.

**Verified working, not just deployed:** hit `/centres` on the live
Railway URL directly, confirmed it returns real data matching the
independent SQL check exactly.

**NOT yet verified (as of the original writing of this section):** the
actual Google sign-in flow end-to-end. **Update, Part 6:** this has
since been exercised — see Part 6's note on what was and wasn't
personally driven through the browser.

---

## Part 4 — Frontend

**This section was rewritten by a later Claude Code session** (the same
one that added Part 6) because the original text below described the
frontend as scaffolded but never run — that's no longer true. The
original next-steps list is kept below with each item annotated, rather
than deleted, since the reasoning in it is still valid for anyone
picking this up fresh.

Explicit instruction from the user: don't care about UI/UX yet — get
main features plus side features functionally working first, discuss
polish later. Framework: React + Vite.

**Feature list, as scoped:**
Main: (1) Google Sign-In, (2) centre picker showing the 4 towns with
real counts, (3) map view of the selected centre's actual route lines
and confidence points.
Side: (4) trace list per centre (the raw sources), (5) trace detail
(one trace's turns + resolved waypoints).

**Current real status, as of this session:** the frontend has been run
(`npm install && npm run dev`, Vite dev server on `localhost:5173`)
and driven through a real browser (Chrome, via the claude-in-chrome
automation tools) across three separate debugging/verification
sessions, against a real local backend (`uvicorn main:app --port 8000`)
talking to the live Railway Postgres via `DATABASE_PUBLIC_URL`. Two
real, confirmed frontend bugs were found and fixed in that process (see
Part 6 for the fix details, since they surfaced while working on the
test_class feature, not as a separate audit):
- `TraceList.jsx` had no error handling on its fetch — any failure
  (network blip, momentary 401, etc.) left the list silently empty with
  zero indication, indistinguishable from "this centre has no traces."
- Neither `TraceList.jsx` nor `MapView.jsx` guarded against stale
  responses — switching centres quickly could let an old centre's
  response resolve after a newer selection and silently overwrite it.
  Reproduced concretely (temporarily reverted the fix, confirmed
  Smiths Falls showed Canotek's stale trace count under a fast
  switch, then restored the fix and confirmed it no longer happens).

Both are fixed as of this session (`frontend/src/components/
TraceList.jsx` and `MapView.jsx`).

**`frontend/.env`** currently has `VITE_API_URL=http://localhost:8000`
— this was the correct choice for the debugging sessions (a local
backend was run specifically to test against, per the original note
below about "unless the plan is to run the backend locally too during
frontend dev" — that's what happened). This has **not** been changed to
point at the live Railway backend, and no decision has been made yet
about the production value. Still an open decision, not resolved by
this note.

**Original "Immediate next steps," annotated:**
1. ~~`cd frontend && npm install && npm run dev`~~ — done.
2. Decide and fix `VITE_API_URL` (local backend vs. deployed Railway
   backend) — **still open**, see above.
3. Confirm the Google Sign-In button renders at all — **not directly
   observed this session**; the browser session used for testing was
   already signed in (a valid 7-day session cookie from an earlier
   sign-in), so the initial unauthenticated screen with the Sign-In
   button itself was never actually looked at fresh.
4. Confirm the full auth flow works (Google token → `/auth/google` →
   cookie → `/auth/me`) — **confirmed indirectly**: the app correctly
   showed "Signed in as zaidahmad8060@gmail.com" on load, meaning
   `api.me()` succeeded against the real backend with a real cookie,
   and every protected endpoint (`/map`, `/traces`) worked correctly
   across all four centres throughout testing. What was **not**
   personally exercised: clicking through the actual Google popup /
   consent screen for a fresh sign-in.
5. Confirm the centre picker loads real data — **confirmed**, matches
   the SQL-verified row counts in Part 2 exactly.
6. Confirm the map renders route lines + points for a centre with real
   data, Walkley first — **confirmed**, and gone further: raw
   coordinates cross-checked against `psql` directly, and the actual
   rendered SVG in the DOM inspected to confirm Walkley's 8 route lines
   render as genuinely smooth, connected paths (not the "random
   scattered dots" a since-resolved bug report described — that report
   never reproduced; see Part 6 for what actually turned out to be
   broken instead).
7. Confirm the trace list and trace detail side features work — trace
   **list** confirmed working for all four centres (correct counts,
   correct content, error states now handled per the fixes above).
   Trace **detail** (`TraceDetail` in `TraceList.jsx`, opened by
   clicking a source) was **not** specifically tested this session —
   worth a quick check before considering side features fully done.
8. UI/UX polish — still not started, as originally scoped.

**Frontend has no production deployment.** All of the above was local
dev only. Whoever picks this up next needs to decide hosting and update
`VITE_API_URL` / the Railway backend's `FRONTEND_ORIGIN` / the Google
Cloud Console authorized origins together, consistently, once a real
frontend URL exists.

---

## Part 5 — Things to just know, not necessarily act on

- A recurring mistake this session: giving a `cd`-relative path while
  already inside that directory, creating a wrong nested path or
  silently failing. Always check `pwd` when a command's location
  matters.
- File uploads as `.txt`/`.md`/`.csv` attachments have repeatedly come
  through empty in this chat interface; screenshots and direct project-
  folder uploads have worked reliably. If a text upload fails, don't
  retry it — ask for a screenshot or the project-folder route instead.
- This whole handoff exists because the prior conversation got long
  enough that per-message cost (re-processing the entire history every
  turn) was burning usage fast for objectively light work. Keep new
  conversations scoped — don't let one thread cover data pipeline +
  backend + frontend + deployment all at once again if avoidable.

---

## Part 6 — Route test_class / mixed_classes (three-session Claude Code arc)

New work, not covered above, spanning three separate Claude Code
sessions (debug → verify → fix-and-ship). Summarized here rather than
narrated in full — the play-by-play (including the specific spurious
data that got caught) belongs in the Notion Build Log, see links below.

**What changed:**
- `route_lines` gained two columns: `test_class TEXT`,
  `mixed_classes BOOLEAN NOT NULL DEFAULT false` (`backend/schema.sql`, applied
  live via `ALTER TABLE`).
- `scripts/migrate_to_postgres.py` and `backend/main.py` carry these fields
  through end to end (insert on migration, expose in
  `GET /centres/{id}/map`'s route_line feature properties).
- `common/classvote.py` (new): one shared `class_vote()` function,
  same shared-module precedent as `streetnames.py`. Only confirmed
  `"G"`/`"G2"` trace labels count as votes toward a route family's
  dominant class; `"ambiguous"` (a trace's text matched both G and G2
  keyword patterns in `extraction/process_reddit_v2.py`'s
  `guess_class()`) and `"unknown"` (matched neither) abstain rather
  than casting a peer vote. This matters: the first backfill attempt
  let ambiguous/unknown outvote confirmed classifications, and every
  family that first attempt flagged "mixed" turned out to be spurious
  once that was fixed — zero of the 10 currently-published route_lines
  are actually mixed-class.
- `analysis/consensus_geometry.py` uses `class_vote()` instead of its
  own (previously uncommitted) inline computation.
- `backfill_route_test_class.py` (new, repo root): populates
  `test_class`/`mixed_classes` for already-published route_lines
  without re-deriving route families or geometry (re-running the full
  clustering pipeline was tried in a dry run and found to change which
  routes get published entirely — 8 Walkley lines became 6, renumbered
  — a data-layer clustering change that's explicitly out of scope
  here). Instead, it reconstructs "which traces fed this route_line" by
  matching each route_line's own segments against each trace's own
  turn-derived segments, requiring at least half-segment overlap
  (a looser "any overlap" version was tried first and found to produce
  false attributions through generic shared hub junctions, e.g. two
  unrelated routes both passing the same junction near the test
  centre).

**Current live values, verified via `psql` after running the backfill
for real** (not just trusted from the script's own output): of the 10
published route_lines, **6 confidently classed** (Canotek family 1 both
runs: G; Walkley family 1 both runs and family 3 run 2: G2), **0
mixed**, **4 NULL** — Walkley family 2 run 0, and family 3's runs 1, 3,
and 4. NULL means no trace currently in the `traces` table can be tied
(at the half-segment-overlap threshold) to that specific route_line
with a confirmed G/G2 label. The geometry is still real, validated
route data — just unlabeled.

**Frontend rendering** (`MapView.jsx`): route lines are colored by
class — G orange, G2 blue, NULL solid gray with a "class unknown"
popup label. A mixed-class style (gray, dashed) exists in the code but
has no live example right now; it's kept rather than removed for when
real data produces a genuine G/G2 conflict. Confirmed via the actual
rendered SVG in the browser DOM, not just by reading the code, that all
three currently-live states are visually distinct on Walkley.

**Not resolved, needs a decision:** whether NULL route_lines should
stay rendered as they are now (solid gray, "class unknown"), or be
hidden until more source data exists. Currently rendered, not hidden —
that was the safer default in the absence of a decision, not a final
call.

**Committed but not pushed:** branch `feat/route-test-class-map`, 4
commits (frontend hardening + first-ever frontend commit, schema/
migration/API, the classvote fix, and this file). `git push` failed in
that session's environment — no GitHub credentials configured, no `gh`
CLI installed. Push this yourself:
```
git push -u origin feat/route-test-class-map
```

**Separate, NOT touched by any of the above:** Smiths Falls route-line
clustering (whether re-clustering its thin 7-trace corpus finds real
route families at all) is its own open investigation, independent of
the test_class work. Don't conflate the two if picking either back up.

**Where to look for more detail** — project tracking lives in Notion,
under "Ontario Drive Test Routes — Project Idea":
- Build Log: `https://app.notion.com/p/3cb6d8d941d7818b986efdcd84fa2fa6`
- Report Material: `https://app.notion.com/p/3cb6d8d941d781c6b987d8b1c4abf991`
- Route Consensus — Design Doc: `https://app.notion.com/p/3ca6d8d941d78173a6cacf818cd4ce28`
- Parent page: `https://app.notion.com/p/3ca6d8d941d78142b066e4a9263fa9ee`

The mixed-class heuristic's before/after (spurious v1 vs. the
tightened-threshold-plus-classvote-fix v2) is exactly the kind of thing
that belongs in the Build Log — this project treats "the plan was
wrong" as material worth recording, not something to bury.

---

## Part 7 — route-quality overhaul, rebuild_routes.py, manual_routes.py (2026-09-14 through 2026-09-18, multiple sessions)

Everything below happened after Part 6 and is NOT reflected in Parts
1-6 above (this file went stale for several sessions again — see Part 5's
note about that being a recurring failure mode). Full narrative detail
is in Claude's own persistent memory and `SESSION_LOG_2026-09-14.md`;
this is the compressed version so a fresh session isn't starting blind.

**Branch:** still `feat/route-test-class-map`. It IS now pushed to
origin (the Part 6 push instruction is done). Latest commits build
`manual_routes.py` (uncommitted as of this writing — see below).

**2026-09-14 session:** fixed `InstructionsTable`'s build break,
fixed `MapView.matchesFilter()` hiding 4 of Walkley's 8 confirmed
routes (null-class routes matched neither G nor G2 filter button),
fixed a real `db.py` connection-pool poisoning bug (an aborted
transaction never rolled back, breaking every later request until
restart), fixed an N+1 query in `get_map`. Root-caused OSRM's
straight-beeline behavior for unroutable junction pairs (778m gaps in
Walkley's confirmed geometry) — frontend now splits at >400m gaps and
renders them dashed ("unrouted"), `consensus_geometry.py` records
`gap_m`. Confirmed Canotek's G/G2 same-speed-profile finding is a real
data-coverage gap (no trace ever covered the Hwy 174 leg), not a bug.

**2026-09-15 session — route-quality overhaul:** root-caused why
routes "didn't look like one route": `families()` clusters on shared
streets and is class-blind, merging G+G2 routes onto arterials they
both use. Fixed with `class_families()` (cluster within class only).
Built **`rebuild_routes.py`** (repo root): per (centre, class), take
the class's own confirmed traces, pick the most-complete real trace as
a "backbone" (its driven-order turns ARE the route), OSRM-route its
junctions in order. `ROUTE_COUNTS` dict held the assumed ground truth
route count per (centre, class) — **this assumption was wrong, see
below.** `rebuild_routes.py --apply` was run for real at some point
after the session ended (confirmed via `psql`: all 8 live `route_lines`
rows have `source='rebuild_routes'`) — Walkley G=1/G2=3, Canotek
G=1/G2=1, Smiths Falls G=1/G2=1.

**2026-09-18 session — the real ground truth arrived.** The user spent
days hand-transcribing real DriveTest YouTube videos, street-by-street,
into `~/Downloads/Manual Route trace youtube.md` — genuine ground
truth, not inference. **It overturns `rebuild_routes.py`'s
`ROUTE_COUNTS`**: Walkley G is actually **3** routes (not 1), Canotek G
is **2** (not 1), Canotek G2 is **3** (not 1). Only Walkley G2 (3) and
Smiths Falls (1 each) were already right.

Built **`manual_routes.py`** (repo root, dry-run only, no `--apply` — a
deliberate choice, see below) + **`build_spur_nodes.py`** (one-off
pyosmium scan of `data/raw/ontario-latest.osm.pbf`, needs
`venv/bin/python3`) to turn the transcript into real routed geometry
via `data/osm.db` + OSRM. Output: `data/out/manual/*.geojson`, viewable
via `data/out/manual/viewer.html` (a throwaway Leaflet page, `python3
-m http.server 8899` from that directory; supports `?focus=walkley` /
`canotek` / `smithsfalls` query param to jump straight to a centre).

**Two real bugs found and fixed while building this, both would have
silently produced wrong routes — worth knowing if extending
`Graph.junction()` (in `analysis/consensus_geometry.py`) to more
centres:**
1. `data/osm.db` is Ontario-wide, not per-centre. `Graph.junction()`
   picks the FIRST matching junction with no location filtering — for
   a common street name that recurs elsewhere in the province, it can
   silently grab the wrong one. Blew Smiths Falls G out to **170km**
   before catching it. `manual_routes.py`'s `best_junction()` picks the
   candidate nearest current position, capped at 15km.
2. A junction coordinate alone doesn't force OSRM to actually drive a
   named street — for a dead-end spur (a parking-maneuver detour:
   Elmridge Dr, Lerner Way, Grafton Crescent) or a street whose two
   flanking junctions are reachable without touching it, OSRM's
   shortest path can silently bypass it. Confirmed present only in the
   auto-generated turn-by-turn TEXT, verified this by checking actual
   route-geometry coordinates pass within 0m of a real node on every
   named street (they do, for all 13 routes, after the fix) — the
   text-only check is not reliable for this, the geometry check is.
   Fixed with `build_spur_nodes.py` sampling a real point from each
   problem street's own OSM geometry, forced in as an explicit
   waypoint.

**Verified this session (visual, in a real browser via
claude-in-chrome, on real OSM tiles):** all three centres' routes are
coherent loops, no teleports, Walkley G properly loops down Airport
Parkway to Macdonald-Cartier airport while G2 stays tight and
residential (matches the ORIGINAL ground truth description exactly),
Canotek G0 visibly follows Highway 174 northeast to Orleans and loops
back, Smiths Falls G loops cleanly through town via a County Road 29
substitution. Durations (OSRM free-flow, so real test time with stops
would run longer) all plausible: Walkley G2 2.4-3.9km/6-8min, Walkley G
14.5-22.1km/20-30min, Canotek G2 2.4-8.8km/6-17min, Canotek G
13.5-19.9km/21-22min, Smiths Falls G2 4.1km/8min, Smiths Falls G
12.6km/17min.

**Two genuinely uncertain substitutions, flagged in `manual_routes.py`
comments, need the user's own memory of the video (not resolvable from
OSM data alone) to fully settle:**
- Smiths Falls G: "Regional Rd" (appears twice) was resolved to County
  Road 29, the only street that touches both of its real transcript
  neighbors — but Jasper Avenue is ALSO a confirmed direct neighbor at
  the same point, and the transcript's alternating "Regional
  Rd"/"Jasper Ave" pattern reads just like an earlier
  "Brockville"/"County Rd" pattern that WAS confirmed to be one road
  under two names. Geometrically the resolved route is fine either way
  (visually confirmed, no bad detour) — it's specifically which real
  street the narrator meant that's unresolved.
- Canotek G route1: "27" (as transcribed) doesn't exist as a street
  anywhere near Canotek in this OSM extract; resolved to Highway 417.
  Confidence raised after checking the geometry: it now shows a clean
  Blair Rd -> Innes Rd -> "Merge onto Highway 417" -> Aviation Parkway
  loop, which matches the transcript's "turn onto highway ramp ->
  continue on Queensway" maneuver well.

**Also handled, lower-confidence-but-resolved:** "Ottawa 34" dropped
(almost certainly Montreal Rd's own route number, not a separate
street). "Queensway" in Canotek G route0's transcript was DROPPED
rather than used — in this OSM extract that name matches a rural road
near Smiths Falls, not Highway 417/the real Ottawa Queensway; using it
would have routed through the wrong region entirely. Walkley G route0's
transcript is truncated (cuts off before returning to centre) —
completed by continuing the last named street (Walkley Rd) back to the
centre, no streets invented.

**NOT done, deliberately:** `manual_routes.py` has no `--apply` path
yet by design — the live DB currently holds `rebuild_routes.py`'s data
with the WRONG route counts (8 rows: Walkley G=1/G2=3, Canotek
G=1/G2=1, Smiths Falls G=1/G2=1), and replacing it with the correct 13
should be a deliberate, reviewed action, not something done in the same
pass as building the resolver. Whoever picks this up should: (1) have
the user settle the two uncertain substitutions above if they still
have the video open, (2) decide whether `manual_routes.py` should grow
a `--apply` (mirroring `rebuild_routes.py`'s backup-then-DELETE/INSERT
pattern) or whether the geojson should be reviewed by hand first, (3)
if applying, note this REPLACES all `rebuild_routes`-sourced rows, it's
not an additive merge — route counts genuinely change from 8 to 13.
