---
name: OntarioDriveTestMap
description: Evidence-labelled Ontario G/G2 road-test routes
updated: 2026-09-25
source: OntarioDriveTestMap-UI-UX-Redesign-Spec.md (25 Sept 2026)
colors:
  ink: "#102A43"
  ink-soft: "#536675"
  canvas: "#F6F8F7"
  surface: "#FFFFFF"
  surface-muted: "#E8EEED"
  border: "#D5E2DF"
  border-strong: "#A9BDB8"
  brand: "#0B756B"
  brand-deep: "#096C64"
  brand-wash: "#E4F3EF"
  inferred: "#97531B"
  inferred-wash: "#FFF1DB"
  neutral-line: "#6A7A86"
  danger: "#A33B32"
  danger-wash: "#FBECEB"
  focus: "#175CD3"
  position: "#2467B6"  # user GPS puck only, never route status
typography:
  family: "Manrope (Google Fonts, existing approved origin), system sans fallback. One family only."
  hero: "800, 44/50 desktop, 32/38 mobile, -0.035em"
  page-title: "700, 30/36"
  section: "700, 22/28"
  card-title: "700, 18/24"
  body: "400, 16/24"
  metadata: "14/20 minimum; never below 14px, wrap French instead of shrinking"
  data: "same family, tabular figures, weight 600"
rounded:
  control: "10px"
  card: "12px"
  dialog: "16px"
  pill: "999px (small badges and segmented choices only)"
spacing: "4px grid: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 / 64"
layout:
  page-max: "1440px (route workspace)"
  content-max: "1200px (home, discussion, static pages)"
  rail: "384px route rail at >= 1100px"
  gutters: "16px mobile, 24px tablet, 32-48px desktop"
motion: "120-220ms, ease-out, everything collapses under prefers-reduced-motion"
---

# Design System: OntarioDriveTestMap

Implemented in `frontend/src/App.css` (tokens on `:root`, component layer at the
end of the file) and mirrored for the static pages in
`frontend/public/static-site.css`. Keep the two token blocks in sync.

## 1. Direction

**Road atlas + trustworthy field notes.** A calm, credible study tool for
people preparing for an Ontario G or G2 road test. It should read as a
well-made independent civic map product. It should not read as a generic SaaS
dashboard, a government service, or a driving game.

The visual signature is the **route itself**: real geometry on a quiet map,
and one confidence notation repeated everywhere. There's no hero
illustration, gradient, glassmorphism, emoji road sign, crest, seal or
"verified" ribbon. Nothing may look like a DriveTest or provincial mark.

Screenshot test: if you can swap the logo for a travel or fitness app and the
screen still makes sense, it's too generic. If someone can't tell confirmed
from inferred without opening a panel, the map treatment is wrong.

## 2. Colour

UI chrome is ink on pale stone/green surfaces. Teal (`brand`) is both the
active-control colour and the "confirmed" route colour on purpose: the
product's identity *is* confirmed evidence. Amber and grey carry route
meaning only and are never decoration. `danger` is for errors and
destructive confirmation only and is **never a route colour**.

Primary buttons are solid `ink`. Selected segmented controls use `brand`.
Focus is always a 3px `focus` blue outline, offset 3px.

Use dark text on the pale washes, never white on a wash.

## 3. The confidence notation (The Never-Blur Rule)

Confirmed and inferred data are never visually indistinguishable, even under
pressure to simplify. Colour is never the only carrier: every status is a
**line pattern + a word**, repeated identically in the map, map legend,
route summary, turn rows, centre index, home trust note and About.

| Status | Line | Word (EN / FR) | Backend mapping |
| --- | --- | --- | --- |
| Confirmed | solid teal, round caps | Confirmed / Confirmé | `route_lines.predicted = false AND below_threshold = false` |
| Inferred | amber dash `10 9` | Inferred / Déduit | `predicted = true` (road-snapped bridge) **or** `below_threshold = true` (weaker than the publish threshold) |
| Unrouted gap | grey dotted `2 8` | Unrouted gap / Écart non tracé | client-split beeline > 400 m (`splitAtGaps`) |
| Unknown | thin neutral line | Status unavailable / Statut indisponible | anything else. Never styled as confirmed |

The mapping lives in one place: `lineStatus()` in
`frontend/src/components/MapView.jsx`. The shared UI pieces are
`LineSample`, `StatusBadge` and `RouteLegend` in `frontend/src/RouteNotation.jsx`,
which take a semantic `status` enum, never a colour. A route with both
confirmed and inferred sections is labelled "Partly inferred" and its
section counts are listed separately; a single confirmed leg never
upgrades the whole route.

Counts are never merged: centre rows show confirmed and inferred
separately, and evidence is labelled with its real scope ("Evidence for
this centre" means the `/traces` records for the whole centre, never proof
for a particular route or turn).

## 4. Map

- Base OSM tiles are lightly muted (`.ontario-tiles` filter); overlays and
  attribution are untouched.
- Route sections draw over a pale casing so they stay legible on busy streets.
- Evidence points (scored junctions) are **off by default**, live in their own
  Leaflet pane below the route, and are small neutral circles when shown
  ("Colour by support" is an opt-in sub-option).
- One named centre pin, visually distinct from evidence points.
- Auto-fit only on centre/route change. Never refit on a turn click, layer
  toggle or rerender. "Fit route" resets the view.
- Turn ↔ map linking only exists when a route has more than one section;
  otherwise there's no honest correspondence to draw.
- If tiles fail, a retry banner appears. Text, status and turns always remain.

## 5. Layout

- **Header:** one 68px row. Brand · Find a centre / Discussion / About ·
  Submit a route (primary) · EN/FR · notifications · Account menu. Below
  1100px the nav collapses into a labelled Menu; below 600px notifications
  and account move into the Menu too. Account holds the role badge, email
  preference, sign out and delete account (two-step).
- **Home:** search-first hero, centre index rows with aligned counts, an
  editorial trust note using the line samples, compare as a quiet disclosure.
- **Route (≥1100px):** breadcrumb + title + Change centre on one band, then a
  384px rail (picker, summary, turns) beside a sticky map
  `min(70vh, 760px)`. Tablet: picker and summary side by side, map, turns.
  Phone: picker → map (route title and status in its toolbar) → summary →
  turns, with section jump links.
- **Discussion:** 960px column, one filter toolbar (a Filters disclosure on
  phones), editorial feed with rules instead of cards, separate empty,
  filtered-empty and error states.
- **About / legal:** readable article column (~68–76ch), numbered source rows,
  illustrative legend figure explicitly captioned as not a real route.
- **Submission wizard:** 720px dialog on desktop, full screen on phones,
  named four-step stepper, one-line motivation with the long note behind
  "Why submissions help", discard confirmation, modal focus handling.

## 6. Components and states

Cards are 12px radius with a hairline border and no shadow at rest (the
Flat-Ground Rule: only content that leaves the flow, such as popovers,
drawers, dialogs and the submission nudge, gets a shadow). Don't wrap every
sentence in a card; the centre index, turn feed, discussion feed and About
all use rules and spacing instead.

Every async surface has a loading, empty and error state: skeletons (not a
blank area), a `state-panel` for empty, and `state-panel--error` with a Retry
that keeps the user's selections. Failures never render as "no posts" or "no
centres".

## 7. Copy and i18n

Use real product language: "Ottawa Walkley · G Route 1", "Evidence for this
centre", "Inferred section". No marketing slogans. Every message is a
complete string per key in `frontend/src/i18n.jsx`; plurals use
`tn(key, n)` with `_one` / `_other` variants (Intl.PluralRules). French uses
"itinéraire" for route throughout. Language persists across entry points via
the `lang` localStorage key. The static About/legal pages are English-only.

## 8. Guest boundary and practice drive (2026-09-25)

- **Reading is public, contributing needs an account.** Guests see the
  landing (real route preview + centres), route maps, turns and evidence
  counts. Raw source records, centre tips, discussion, submissions,
  reports and reminders ask for sign-in *at the action*, through
  `useSignIn().requireSignIn(reason, action)` (`frontend/src/SignIn.jsx`).
  Each reason has its own honest title and body, and the action resumes after
  sign-in. Never justify a read gate with "accountability".
- **Practice drive** (`components/PracticeDrive.jsx`, mobile only) is a
  Phase A visual follower: one reducer state machine (preview → acquiring
  → joining → following ↔ lowAccuracy ↔ offRoute, plus away / paused /
  interrupted / denied / noFix / unavailable). **Position blue `#2467B6`** is
  only ever the user's puck. GPS quality uses a bar-chip with its own words,
  so it can't be read as route confidence. No turn countdown or voice until
  turn steps carry geometry anchors. Hidden page = interrupted; resuming
  needs a fresh fix. Location never leaves the device.

## 9. Don'ts

- Don't soften the confirmed/inferred/gap distinction for visual cleanliness,
  under any circumstance, including a request to simplify.
- Don't invent data to fill a design: no fake posts, counts, confidence
  percentages, route previews or maneuver icons that aren't in the
  instruction text.
- Don't imply a route will be assigned on test day, or that web GPS tracks in
  the background.
- Don't use a serif display face (removed at the owner's request, 2026-09-25),
  gradient text, glass, or decorative blur beyond the header's backdrop.
- Don't give secondary features (compare, reminders, digest, discussion) the
  same visual weight as route selection and the map.
