# OntarioDriveTestMap

A crowdsourced map of real Ontario G/G2 DriveTest routes — built from actual GPS traces, hand-transcribed test-drive videos, and community submissions, not schematic guesses or paywalled placeholders.

Every competitor we audited either shows a disclaimed fake "sample route" and gates the real one behind a purchase, or ships an explicitly "not to scale" map synthesized from anecdotes. This project exists to be the opposite: real GPS-derived route data, openly shown, with every section honestly labeled by how well-supported it actually is.

**Live at:** [ontariodrivetestmap.fyi](https://ontariodrivetestmap.fyi)

Not affiliated with, endorsed by, or operated on behalf of DriveTest, Serco Canada Inc., or the Ontario Ministry of Transportation.

## What it does

- **Route maps** for each DriveTest centre, split by G and G2, with turn-by-turn instructions, real street names, speed limits, and junction types where the underlying data supports them.
- **Confidence labeling on every route** — confirmed, predicted, or insufficient evidence — computed from how many independent sources (GPS traces, video transcriptions, community reports) actually agree on a given road segment. This distinction is never blurred for visual cleanliness; a driving-test candidate could physically execute a turn no one ever confirmed.
- **Community route submission**, validated live against real street-graph data, with an auto-clustering pipeline that promotes corroborated submissions into official routes with zero manual review.
- **Live GPS drive-along practice mode**, with map-matching and off-route detection, entirely client-side.
- **Active-recall quiz mode**, built from the same verified route data.
- **A forum** ("where I messed up") anchored to real map junctions, and a separate general **Discussion** board — both with fully automated moderation, no AI moderation API, no volunteer mods.
- **Centre comparison, difficulty scoring, GPX/PDF export.**
- **Accounts** via Google Sign-In only — no passwords ever collected or stored.
- English/French throughout.

## Design principles

The interface is built as a civic utility, not a product being sold — calm and legible for someone checking a route the night before a test, or a passenger reading turns aloud in a moving car. Trust tiers are a first-class visual signal everywhere a route appears, never a footnote. See `PRODUCT.md` and `DESIGN.md` for the full design system and the reasoning behind it.

## How the data comes together

Real GPS traces and hand-transcribed test-drive videos are turned into a weighted graph of road segments (`analysis/`), where edge weight reflects how many independent sources traversed that stretch of road. Routes are reconstructed by walking that graph, and confidence tiers fall directly out of how much corroborating evidence a given segment has — never a manual judgment call.

## Structure

| Path | What's there |
|---|---|
| `frontend/` | React + Vite app — the map UI, forum, discussion, account flows |
| `backend/` | FastAPI service — auth, forum/discussion, route submissions, moderation, the weekly admin digest |
| `analysis/` | Route reconstruction and confidence-clustering pipeline |
| `acquisition/`, `extraction/`, `corrections/` | Turning raw GPS traces and video transcriptions into structured route data |
| `geometry/` | Road-network graph data |
| `scripts/` | Operational scripts (SEO file generation, submission promotion, digest sending) |
| `docs/` | Supporting documentation |

## Legal

- [Privacy Policy](https://ontariodrivetestmap.fyi/privacy-policy.html)
- [Terms of Service](https://ontariodrivetestmap.fyi/terms-of-service.html)
- [Cookie Policy](https://ontariodrivetestmap.fyi/cookie-policy.html)
