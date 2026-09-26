# OntarioDriveTestMap

An independent map of reported Ontario G and G2 road-test routes, reconstructed from real evidence (video-transcribed test drives, community reports and GPS traces where contributors have them), with every section labelled by how well it's actually supported.

**Live at:** [ontariodrivetestmap.fyi](https://ontariodrivetestmap.fyi)

DriveTest doesn't publish its routes. Every competitor we audited either shows a disclaimed "sample route" and sells the real one, or draws an explicitly "not to scale" map from anecdotes. This project does the opposite: evidence-backed routes, openly shown and honestly labelled. Routes are a study aid; an examiner can always choose a different one.

Not affiliated with, endorsed by, or operated on behalf of DriveTest, Serco Canada Inc., or the Ontario Ministry of Transportation.

---

## What it does

**Study a route (no account needed)**
- **Route maps** for each centre, split by G and G2, with a route picker and shareable deep links (`/?centre=walkley&class=G&route=1`).
- **Confidence on every section**: confirmed (solid teal), inferred (dashed amber) or unrouted gap (dotted grey), with the same line and word used on the map, legend, route summary and turn list. The distinction is never blurred for visual cleanliness; a candidate could physically execute a turn no one ever confirmed.
- **Turn-by-turn list** with distance, time, traffic control and posted speed where the data has them.
- **Numbered turn points** on the map for turns matched, by their own street names, to a real junction on the route. Selecting a turn highlights its point and vice versa. Unmatched turns stay text-only and are never guessed.
- **Route playback**: a dot drives the route at 0.5×, 1×, 2× or 4×, with a slider to jump anywhere.
- **Evidence** counts per centre (video transcriptions and community reports), an optional evidence-points map layer, **centre comparison**, **GPX export** and printable turn lists.
- **Practice drive** on phones: a full-screen map that follows your live position along the chosen route. Foreground only (it pauses when the page is hidden), built for a passenger or a parked car, and your location never leaves the device.
- **English and French** throughout.

**Contribute (free Google sign-in)**
- **Submit a route** street by street; each street is validated against the real road network as it's added.
- **Report an issue** on a specific turn.
- **Tips and corrections** per centre, and a site-wide **Discussion** board, both with fully automated moderation (no AI moderation API, no volunteer mods).
- **Test reminders** and in-app **notifications**, plus self-service account deletion.

Sign-in is only ever asked for at the moment of a contributing action, with the reason for that action, and the action resumes afterwards.

---

## How the data becomes a route

Every source, old or new, goes through the same pipeline; nobody hand-picks what appears on the map.

1. **Acquire** (`acquisition/`): collect source material: YouTube test-drive transcripts and frames, Reddit route descriptions.
2. **Extract** (`extraction/`): turn each source into an ordered list of streets and turns (a "trace"). Manually transcribed routes live in `scripts/manual_routes.py`.
3. **Snap** (`geometry/`): match every street and junction to OpenStreetMap data for Ontario (`backend/data/osm.db`). Streets that don't exist, or don't actually meet, are rejected. Hand corrections live in `corrections/`.
4. **Cluster and weight** (`analysis/consensus_geometry.py`): group traces that describe the same drive (HDBSCAN, with a hierarchical fallback), weight each stretch of road by independent support (one account counts once, and older evidence decays), and label sections against a single confirmation threshold. Gaps where the map data doesn't connect become unrouted gaps, never solid road.
5. **Load** (`scripts/migrate_to_postgres.py`): write centres, traces, route lines and turn steps to Postgres.
6. **Serve** (`backend/`): the API adds per-turn junction anchors at request time (`backend/turn_anchors.py`).

**New data:** community submissions stay *pending* until at least two different accounts describe the same route and it passes the same threshold. `scripts/promote_submissions.py` then promotes it; it's idempotent and its rows can be rolled back by `source`. Turn reports are stored for review and never change the map on their own. If official records arrive (an MTO Freedom of Information request is pending), they'll be added as their own labelled source and weighed in the same pipeline.

---

## Access model

| Public (no account) | Signed-in only |
| --- | --- |
| Centre list, route maps and turn text (`/centres`, `/centres/{id}/map`) | Raw source records (`/centres/{id}/traces`, `/traces/{id}`) |
| Evidence counts (`/centres/{id}/evidence-summary`), centre comparison | Centre tips, discussion posts and comments (read and write) |
| About and legal pages | Route submissions, turn reports, reminders, notifications, account |

Public reads are cached for 5 minutes server-side and sent with `Cache-Control`. Every endpoint is rate-limited per IP (60/min by default, tighter on auth and writes).

---

## Tech stack

| Layer | Tech |
| --- | --- |
| Frontend | React 18, Vite 5 (multi-page: `index.html`, `discussion.html`), React-Leaflet on OpenStreetMap tiles, hand-rolled EN/FR i18n, served in production by `serve` (`frontend/public/serve.json`) |
| Backend | FastAPI + Uvicorn, psycopg2 (threaded pool), SlowAPI rate limiting, GZip, Google Identity Services, JWT session cookie (`httpOnly`, `Secure`, `SameSite=Lax`) |
| Data | PostgreSQL (app data), SQLite `osm.db` (road network and junctions), Python pipeline (numpy, scipy, scikit-learn) |
| Hosting | Railway (Frontend, Backend and Postgres services), Cloudflare DNS, Resend for the admin weekly digest |

**Domains:** `ontariodrivetestmap.fyi` and `www.` → Frontend service (`www` redirects to the apex in the page head); `api.ontariodrivetestmap.fyi` → Backend service. Keeping the API on a subdomain of the same site is what lets the `SameSite=Lax` session cookie work. Cloudflare records for Railway must be **DNS only** (grey cloud) so Railway can issue certificates.

---

## Local development

**Prerequisites:** Node 18+, Python 3.11+, access to the Postgres database.

**1. Environment**

Repo-root `.env` (read by the backend; never committed):

| Variable | Purpose |
| --- | --- |
| `DATABASE_PUBLIC_URL` | Postgres URL reachable from your machine (used locally) |
| `DATABASE_URL` | Private Railway URL; preferred automatically when running on Railway |
| `JWT_SECRET` | Signs session cookies |
| `GOOGLE_CLIENT_ID` | Google Sign-In client ID (token verification) |
| `FRONTEND_ORIGIN` | Allowed CORS origin(s), comma-separated. Default `http://localhost:5173` |
| `RESEND_API_KEY`, `RESEND_FROM` | Optional: weekly admin digest email |

`frontend/.env`:

| Variable | Purpose |
| --- | --- |
| `VITE_API_URL` | Backend base URL, e.g. `http://localhost:8000` |
| `VITE_GOOGLE_CLIENT_ID` | Same Google client ID as above |
| `VITE_SITE_URL` | Optional canonical site URL; production defaults to `https://ontariodrivetestmap.fyi` |

**2. Run it**

```bash
# backend (from the repo root)
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev            # http://localhost:5173
```

`npm run build` produces `frontend/dist/`; `npm start` serves it the same way production does.

The data pipeline (`analysis/`, `geometry/`, `extraction/`) additionally needs `pip install numpy scipy scikit-learn`.

**3. Dev-only helpers** (stripped from production builds)

- `?devGuest=1`: this tab calls the API without your session cookie, to check signed-out views while signed in.
- `?simulateDrive=1` (or `=2`…`10` for speed): fakes GPS driving the selected route in Practice drive, including a weak-GPS stretch and an off-route detour. A yellow "Simulated location" chip shows while it's active.

**Testing on a phone** (practice drive needs HTTPS for location): run a second dev server that proxies the API, then open it through a Cloudflare quick tunnel.

```bash
cd frontend && VITE_API_URL=/api npx vite --port 5174 --strictPort
cloudflared tunnel --url http://localhost:5174   # prints an https://…trycloudflare.com link
```

The `/api` proxy and the `*.trycloudflare.com` host allowance live in `vite.config.js` (dev server only). Google sign-in won't work on the tunnel address, but routes and practice drive don't need it.

---

## Operational scripts

| Command | When |
| --- | --- |
| `SITE_URL=https://ontariodrivetestmap.fyi python scripts/generate_seo_files.py` | After data changes (new centre, new routes). Regenerates `sitemap.xml`, `robots.txt` and `llms.txt` in `frontend/public/`. **Always set `SITE_URL`**; it defaults to localhost. |
| `python scripts/promote_submissions.py --dry-run` / `--apply` | Promote corroborated community submissions into route lines. Review the dry run first. |
| `python scripts/send_weekly_digest.py` | Weekly admin summary email; run from an external weekly cron. |
| `python scripts/migrate_to_postgres.py` | Load pipeline output into Postgres (per-centre delete-then-reinsert). |

---

## Deploying

Railway deploys automatically from `main`: the Backend service builds `backend/` only (so everything it imports, including `osm.db`, must live inside `backend/`), and the Frontend service builds `frontend/` and runs `npm start`. Push to `main` and watch the two deploys in the Railway dashboard.

---

## Project structure

| Path | What's there |
| --- | --- |
| `frontend/` | React + Vite app: map workspace, practice drive, discussion, account flows. Static pages (About, legal, 404), `serve.json` and SEO files are in `frontend/public/` |
| `backend/` | FastAPI API: public route data, turn anchors, auth, forum/discussion, submissions, moderation, reminders, notifications, digest. Vendors the pipeline modules and `data/osm.db` it needs at runtime |
| `acquisition/`, `extraction/` | Collecting sources and turning them into traces |
| `geometry/`, `corrections/` | Snapping traces to the OSM road network; hand corrections |
| `analysis/` | Clustering, weighting and route reconstruction |
| `common/` | Street-name normalisation and class voting shared by the pipeline |
| `scripts/` | Operational scripts (see above) |
| `docs/` | Handoff notes, security audit, session logs |
| `DESIGN.md`, `PRODUCT.md` | Design system (the confidence notation, layout rules, guest/practice-drive rules) and product principles |

---

## Legal

- [About](https://ontariodrivetestmap.fyi/about.html)
- [Privacy Policy](https://ontariodrivetestmap.fyi/privacy-policy.html)
- [Terms of Service](https://ontariodrivetestmap.fyi/terms-of-service.html)
- [Cookie Policy](https://ontariodrivetestmap.fyi/cookie-policy.html)
