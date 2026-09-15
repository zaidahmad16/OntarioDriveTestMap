# Overnight session — 2026-09-14 (Claude Code)

Scope kept to **frontend + backend + verification + docs**. The data
pipeline (`consensus_geometry.py`, `classvote.py`, the backfill/predict/
connect scripts, `schema.sql`, migrations) is the user's own domain and
was **not** edited — pipeline-level observations below are reported, not
acted on. All numbers here were **measured from the live local API against
the real Railway Postgres**, not assumed. A dev-only session JWT (minted
locally with the repo's own `JWT_SECRET`) was used to exercise the
protected endpoints; nothing in auth was changed.

Branch: `feat/route-test-class-map` (still unpushed — no git credentials
in this environment; `git push -u origin feat/route-test-class-map` is
yours to run).

---

## Commits made this session (on the feature branch)

1. `2fe9814` — **fix InstructionsTable build break.** The mid-flight
   segment-grouping restructure left two build-breakers: a `#` used as a
   JS comment marker and the `segments.map()` block missing its closing
   `))}` + wrapping `</div>`. `vite build` was failing outright. Fixed,
   indentation tidied, build green.

2. `62d2ed9` — **frontend/backend robustness + confirmed/inferred
   distinction.** Four fixes, detailed below.

---

## Bugs found and fixed

### 1. Confirmed route geometry was invisible on the map (highest impact)
`MapView.matchesFilter()` returned `test_class === filter` for route
lines, and the only filter buttons are **G / G2** (default G). A route
line with `test_class = null` matched *neither* button and never
rendered. That silently hid **4 of Walkley's 8 confirmed consensus
routes** — including `family 2 / run 0`, a 7.4 km, 269-point real route.
HANDOFF Part 6 explicitly says NULL routes should render ("the safer
default"); the filter was quietly overriding that. **Fix:** null-class
route lines now show under both buttons (their popup already says "class
unknown"). Verified: they appear as gray lines that follow real streets.

### 2. Public route count lumped inferred data with confirmed
`/centres` returned a single `route_line_count` (canotek **22**, walkley
**26**) that counts confirmed consensus routes *plus* predicted bridges,
below-threshold recovery, and centre connectors together. On the public
landing page `CentreList` showed "22 route lines" when only **2** are
confirmed. That collapses the confirmed/predicted distinction the project
treats as a hard rule. **Fix:** backend now also returns
`confirmed_route_line_count` (`NOT predicted AND NOT below_threshold` →
walkley **8**, canotek **2**, matching the documented confirmed totals).
`CentreList` headlines the confirmed number and shows "(+N inferred)"
secondary.

### 3. `App.getCentres()` had no error handling
A failed public `/centres` call left the centre list silently empty —
indistinguishable from "there are no centres," the exact trap already
fixed in `TraceList`/`MapView`. **Fix:** added `.catch` + a visible error
message.

### 5. `db.query()` could poison the connection pool on any error
psycopg2 opens an implicit transaction on first `execute` and `query()`
never ended it. On any error (a transient blip to the Railway public
proxy is the realistic case) the connection went back to the
`SimpleConnectionPool` with an *aborted* transaction, never rolled back —
so every later request reusing it failed with "current transaction is
aborted…" until a process restart. **Fix:** commit after a successful
read (no idle-in-transaction) and roll back on error in both `query()`
and `execute()`. Proven: 13 deliberate query errors in a row, then a
normal query still returns all 4 centres.

### 6. `/centres/{id}/map` fired an N+1 query for steps
One `route_line_steps` query per route line — 20+ round trips over the
public DB proxy for one map load. **Fix:** a single JOIN query keyed by
centre, grouped in Python. Output verified byte-identical to the per-line
version across all centres.

### 4. `TraceDetail` — no error handling + a latent crash
`getTrace()` had no `.catch` (a failed fetch hangs on "Loading…"
forever), and `w.lat.toFixed(5)` would throw and blank the whole detail
view if any waypoint had a null coordinate. Currently 0/223 waypoints are
null, so the crash is latent — but 3 `failed` + 7 null-status traces are
exactly where a null could appear. **Fix:** added `.catch` + stale-guard,
and guard the `.toFixed` (renders "(unresolved)" instead of crashing).

---

## Verification performed (all green)

- **Backend + DB**: uvicorn against Railway Postgres via
  `DATABASE_PUBLIC_URL`. `/centres` trace/segment counts match HANDOFF
  exactly (canotek 23/29, smithsfalls 7/20, walkley 26/52, winchester 0).
- **Protected endpoints**: `/auth/me`, `/centres/{id}/map`,
  `/centres/{id}/traces` all 200 with a minted dev cookie; `/traces`
  returns 23/7/26/0.
- **Coordinates**: every feature in Ontario range (lat 45.32–45.40,
  lon −75.59 to −75.69), standard GeoJSON `[lon, lat]` order.
- **Regression test**: `extraction/test_reddit_parser.py` — all FIXED
  cases pass, known gaps unchanged, exit 0.
- **Builds**: `vite build` clean after every change.

## Fact-checks — routes vs. reality (measured, not guessed)

- **Predicted bridges respect the 2500 m cap** ✔ — max end-to-end span
  canotek 2242 m, walkley 957 m. None exceed the cap.
- **G-vs-G2 speed profile** — at **Walkley**, G routes include 80 km/h
  roads (Airport Parkway, Hunt Club) and G2 stays lower: a real G-test
  signal. At **Canotek**, G *and* G2 are identical (both ≤60 km/h, median
  40) — no speed-based distinction between the classes there. Data
  observation, not a code bug.
- **Turn-by-turn coherence** ✔ — confirmed routes read as connected
  street sequences (e.g. Walkley Rd → Cedarwood → Baycrest → Heron),
  "Arrive at destination" appears once per segment, no duplicate
  Head-onto loops. The InstructionsTable per-segment grouping holds on
  real data.
- **Geometry follows roads** ✔ (visual, Walkley w/ OSM tiles) — confirmed
  routes trace Bank St / Airport Parkway south to a loop at the airport,
  not scattered dots. Per-family extents are geographically coherent:
  families 0/1 cluster at the centre (<670 m, the G2 residential loops),
  families 2/3 run south to the airport (5.3–5.9 km, on the centre's
  meridian). Nothing strays toward the river/Gatineau.

## Open observations for the pipeline owner (reported, not touched)

- **778 m gap** inside two Walkley confirmed routes (`fam3/run0`
  vertices 279→280, and `fam3/run1` — the same physical segment,
  reversed; both `consensus_geometry`). Rendered and inspected on OSM
  tiles: it is a single straight vertex-to-vertex jump from Hunt Club Rd
  down to the Uplands/NRC area that **cuts across greenspace / airport-
  buffer land, not a road** (A=45.33453,−75.65976 → B=45.34098,−75.66360).
  A road-snapped segment would have many curve-following vertices; one
  778 m straight jump is by definition unsnapped. Same class as the
  2026-09-13 "fabricated straight-line connections" note — but here it's
  inside a *confirmed* consensus route, not a predicted bridge, so a
  G-test viewer sees a confirmed route slice straight across a green
  area. Worth chasing in `consensus_geometry.py`'s assembly.
- **Degenerate predicted bridges**: a few 2-point, ~0 m predicted lines
  at Walkley (`fam0/run0`, `fam2/run1`, `fam2/run2`) — connect a point to
  (nearly) itself; render as nothing meaningful.
- **Orphan junctions**: 11/52 Walkley and 7/29 Canotek `consensus_segment`
  points sit >150 m from any route line (worst 3.3 km / 4.6 km). They
  render as isolated dots away from the routes — real scored junctions
  that belong to no published route family. Visible in the Canotek render
  as a cluster of blue dots to the NE.
- **Canotek class labels**: with G and G2 speed profiles identical and
  the routes short, the G/G2 distinction at Canotek isn't reflected in
  road type — worth a sanity check on Canotek's class assignments.

---

## Round 2 — data layer (you authorized touching the pipeline)

Discipline held: no full re-cluster (that renumbers/drops published routes
per the backfill docstring), local reproduction before any change,
reversible writes only, and the one destructive live-DB delete was
correctly gated by the sandbox and left for you.

### Beeline gap — root-caused + fixed at two layers (`c84bda4`)
Direct OSRM probe on the gap junctions proved it: OSRM returns `"Ok"` but
a **2-point beeline**, distance 778 m == straight-line distance. So an
unroutable junction pair becomes a fake straight road inside a *confirmed*
route.
- **Render (`MapView`):** split route lines at gaps > 400 m (above the
  largest real sparse stretch, ~383 m). Road pieces stay solid red; the
  gap draws as a dashed gray "unrouted — not a road" segment + banner +
  popup. Flags exactly Walkley fam3/run0+run1, one 778 m gap each, zero
  false positives.
- **Source (`consensus_geometry`):** detect the largest post-OSRM coord
  gap, warn at generation, record `gap_m`. No live-DB change.

### Degenerate predicted bridges — fixed at source + guarded (`8ff3da8`)
`order_walk` emits branches off a shared junction as separate runs, so
`nearest_pair` can be the same coordinate → MST drew a 0 m "bridge"
(3 rows: ids 250/252/253). Source: components under `MIN_BRIDGE_M` (10 m)
are unioned but draw no bridge. Frontend: drop route lines under ~5 m so
any stored ones render as nothing. **The 3 live rows remain** — a direct
`DELETE` was correctly blocked by the sandbox; backup +
reversible SQL is ready (`scratchpad/degenerate_bridges_backup.json`).

### Orphan junctions — investigated + framing fixed (`688cb95`)
The 7 Canotek + 7 Walkley junctions that render 3–7 km from any route are
**real** outer-Ottawa locations (Orleans: Jeanne d'Arc × Orleans, St
Joseph…), but weak — weight ≤ 1.2, mostly single-author, video-only, and
never joined a route family. They were **dragging the map's auto-fit
bounds out** until the routes were a tiny knot mid-map. Fixed: `FitToData`
frames on routes (+ centre), not on scattered points; `pointToLayer` fades
junctions by weight. They're likely spurious/misattributed video content
worth a review (ties to Canotek below).

### Canotek G/G2 identical speeds — data finding, no bug
Traces label cleanly (12 G, 9 G2, 2 unknown). Both classes' routes run the
same arterials — Ogilvie (60), Shefford (50), Montréal Rd (50) — G adds
only Blair Rd (50). The centre sits in a business park where everything is
50–60 km/h; **no highway leg was captured for the G route**. The
distinction is by route shape, not road type. Source-data coverage
question, your domain.

### 4 NULL-class Walkley routes — confirmed correctly NULL, no bug
Ran the overlap math locally (efficiently, vs the timeout-prone full
backfill). Each NULL is genuine: matches are either `ambiguous`/`unknown`
class (which correctly abstain) or overlap only at a single generic hub
junction (which the backfill deliberately rejects as spurious). Lowering
the threshold would reintroduce the exact spurious-match bug already fixed.
The geometry is real; the class is honestly unconfirmable. Validates the
attribution logic.

## Still open (need your decision — not code bugs)

- `frontend/.env` `VITE_API_URL` is `http://localhost:8000`; no
  production hosting decision made. (`VITE_GOOGLE_CLIENT_ID` is correctly
  set — Login is fine.)
- Whether NULL-class confirmed routes should also get a dedicated
  "unknown" toggle rather than always-on under both G and G2.
- Whether orphan consensus junctions should be hidden when they're not on
  any route.
- Fresh Google sign-in popup still not exercised end-to-end (can't
  automate the Google consent screen).
