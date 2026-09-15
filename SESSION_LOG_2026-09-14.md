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

- **778 m gaps** inside two Walkley confirmed routes (`fam3/run0`,
  `fam3/run1`, both `consensus_geometry`) — a straight ~0.8 km jump
  between consecutive vertices that may draw across non-road space.
  Same class of issue as the 2026-09-13 "fabricated straight-line
  connections" note.
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
