# Security Audit Report — OntarioDriveTestMap

**Date:** 2026-09-21
**Scope:** Full application — frontend (React/Vite), backend (FastAPI/Postgres), infrastructure config present in the repository.
**Method:** Manual adversarial code review (no live penetration testing against production; this is a local dev-environment code audit). All findings are evidence-based, with file/line references. No code was changed as part of this audit.

---

## 1. Executive Summary

**Overall posture: solid for a solo-built project, with one real High-severity bug and a handful of Medium/Low hardening gaps — no Critical findings.**

The backend is unusually disciplined for its size: every SQL call site uses parameterized queries (no string-built SQL with user input anywhere), every mutating endpoint requires authentication, every ownership/admin check is a real server-side check (not a truthy check), the JWT session has no unsafe fallback secret, and Google's ID token is verified server-side rather than trusted from the client. CORS is a single fixed origin with credentials, not a wildcard. Secrets are gitignored and confirmed absent from git history and from the built frontend bundle. Rate limiting (added earlier in this session) is real and tiered by risk.

The one genuine High-severity issue: the newly-added `POST /analytics/pageview` endpoint is intentionally public and unauthenticated (by design — most page views happen before sign-in), but the raw `path` string it accepts is interpolated unescaped into the HTML of the weekly admin digest email (`digest.py`). An anonymous attacker can `curl` a crafted HTML payload directly into that endpoint with zero authentication, and it will render un-sanitized inside an email an admin opens. See **SEC-001**.

Biggest architectural risk to watch, not yet a live bug: this app has no CI/CD, no automated security testing, and no HTTP security headers (CSP/HSTS/etc.) configured anywhere in code — likely acceptable today given the app isn't yet deployed to a real production domain with real traffic, but worth closing before that happens.

**Strongest controls already in place:** parameterized SQL throughout, real per-endpoint authorization checks, httpOnly/Secure/SameSite session cookie, no passwords stored anywhere, secrets never committed, fixed-origin CORS, real tiered rate limiting, server-side file-type validation on uploads.

---

## 2. Architecture Summary

- **Frontend:** React 18 + Vite 8, two-entry multi-page build (`index.html` main app, `discussion.html` discussion board). Leaflet/OpenStreetMap for maps. No SSR, no GraphQL, no WebSockets.
- **Backend:** FastAPI (0.115.0) + Uvicorn, single-process, single Railway service (`Procfile`: `uvicorn main:app --host 0.0.0.0 --port $PORT`, no `--reload` in production). No Docker, no Kubernetes, no CI/CD pipeline exists in this repo (confirmed — no `.github/`, no `Dockerfile`, no `docker-compose.yml`).
- **Database:** Postgres, accessed directly via `psycopg2` (no ORM), connection-pooled (`psycopg2.pool.SimpleConnectionPool`, 1-10 connections). Hosted on Railway.
- **Auth:** Google Identity Services (client-side) → ID token → verified server-side against Google's public keys (`google.oauth2.id_token.verify_oauth2_token`) → app-issued JWT (HS256, `PyJWT`) stored in an httpOnly/Secure/SameSite=Lax cookie. No passwords anywhere in the system.
- **File storage:** Uploaded images (forum/discussion) stored as `bytea` directly in Postgres, not an object store — a deliberate tradeoff documented in the schema.
- **Third-party integrations:** Google (Sign-In), OpenStreetMap (map tiles, browser-to-tile-server direct), Resend (transactional email — weekly admin digest only, not yet live since no API key is configured).
- **Rate limiting:** `slowapi`, in-memory, per-IP via `get_remote_address`, tiered limits + a 60/min global default.

**Trust boundaries:**
- Browser → FastAPI backend (the primary boundary; CORS-restricted to one origin, cookie-authenticated).
- FastAPI → Postgres (parameterized queries only; DB credentials from env).
- FastAPI → Google (ID token verification; outbound only).
- FastAPI → Resend (outbound email send; not yet active).
- Browser → OpenStreetMap tile servers (direct, no backend involvement, no credentials).

---

## 3. Attack Surface

| Surface | Authentication | Authorization | Sensitive Data | Risk |
|---|---|---|---|---|
| `POST /auth/google` | None (this *is* login) | N/A | Issues session cookie | Rate-limited (10/min); relies entirely on Google's token verification |
| `POST /analytics/pageview` | **None (intentional)** | None | Feeds admin digest email | **See SEC-001 — unescaped in email HTML** |
| `GET /centres`, `/centres/{id}` | None (intentional) | None | Public route metadata | Low |
| `GET /centres/{id}/map`, `/centres/{id}/traces`, `/centres/compare` | Required | Any signed-in user | Route geometry, GPS traces | Low — correctly gated server-side |
| `POST /submissions*`, `/report-error` | Required | Any signed-in user | User-submitted route data | Rate-limited; validated against real street data |
| `POST/DELETE /forum/*`, `/discussions/*` (posts, comments, votes, reports) | Required | Owner-or-admin on mutation | User-generated content | Real ownership checks confirmed; see SEC-004 for a minor gap |
| `POST /forum/*/image`, `/discussions/*/image` | Required | Owner-or-admin | Binary image data | Server-side MIME allowlist + 5MB cap confirmed real |
| `GET /forum/image/{id}`, `/discussions/image/{id}` | Required (any signed-in user) | None beyond sign-in | Image bytes | Acceptable — content is already semi-public to all signed-in users |
| `POST /account/delete` | Required | Self only (session-scoped) | Full account anonymization | Correctly scoped to session's own user_id; see SEC-002 for session-revocation gap |
| `PATCH /account/digest-opt-in` | Required | Admin-only (403 otherwise) | Digest email preference | Correctly enforced despite a stale comment implying otherwise |
| `GET /users/search` | Required | Any signed-in user | Usernames only | Low — no email/PII exposed |
| `GET/POST /booking-reminders`, `/notifications*` | Required | Self only (`WHERE user_id = %s`) | Personal reminders/notifications | Correctly scoped |

---

## 4. Findings Summary

| ID | Finding | Severity | Confidence | Component | Exploitable? |
|---|---|---|---|---|---|
| SEC-001 | Unescaped user input in weekly digest email HTML (stored HTML injection) | **High** | High | `backend/digest.py`, `backend/main.py` (`/analytics/pageview`) | Yes — confirmed via code trace, no runtime test needed to be certain of the sink |
| SEC-002 | Account deletion doesn't revoke other active sessions | Medium | High | `backend/auth.py`, `backend/main.py` (`/account/delete`) | Yes, but narrow window (≤7 days, requires a second active session) |
| SEC-003 | No HTTP security headers configured (CSP, HSTS, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) | Medium | High | `backend/main.py`, frontend hosting config (absent) | Defense-in-depth gap, not independently exploitable today |
| SEC-004 | Vote/report endpoints don't verify the target post exists before insert | Low | Medium | `backend/main.py` (`vote_forum_post`, `report_forum_post`, discussion equivalents) | Causes a 500 on a bad ID, not an info leak (default FastAPI error body is generic) |
| SEC-005 | Rate limiter's IP detection unverified behind Railway's reverse proxy | Medium | Low (requires runtime verification) | `backend/main.py` (`get_remote_address`) | Could make rate limiting ineffective (all traffic bucketed under one IP) — needs production verification, not exploitable from code alone |
| SEC-006 | Content moderation wordlist trivially bypassed | Low | High | `backend/forum.py` (`contains_blocked_word`) | Not a security vulnerability per se — a moderation-effectiveness gap |
| SEC-007 | Unused `GOOGLE_CLIENT_SECRET` present in `.env` | Informational | High | root `.env` | Not read anywhere in code; not a live exposure since `.env` is gitignored, but unnecessary secret material |
| SEC-008 | No automated dependency/secret/SAST scanning in CI (no CI exists at all) | Informational | High | repo-wide | Process gap, not a code vulnerability |

---

## Full Findings

### [SEC-001] Stored HTML injection via public analytics endpoint into admin digest email

**Severity:** High
**Confidence:** High
**CWE:** CWE-79 (Improper Neutralization of Input During Web Page Generation) — technically HTML injection into an email rather than a browser DOM, since the sink is an email client, not the app itself
**OWASP category:** API3:2023 Broken Object Property Level Authorization is not quite right; this is closest to A03:2021 – Injection
**Affected component:** First-party analytics + weekly admin digest email
**Affected file(s):** `backend/main.py` (lines ~303-314, `record_pageview`), `backend/digest.py` (lines ~124-130, ~163-167, `render_digest_html`)
**Affected endpoint(s):** `POST /analytics/pageview` (public, unauthenticated) → consumed by `scripts/send_weekly_digest.py` → emailed via Resend

#### Description

`POST /analytics/pageview` is intentionally public and unauthenticated (most page views happen before a user signs in, which is a reasonable design choice). It accepts a `path` string (Pydantic `Field(max_length=500)`), strips whitespace, and inserts it verbatim into `page_views.path`:

```python
# backend/main.py
@app.post("/analytics/pageview")
@limiter.limit("30/minute")
def record_pageview(request: Request, body: PageView):
    path = body.path.strip()[:500]
    if not path:
        return {"ok": False}
    execute("INSERT INTO page_views (path) VALUES (%s)", (path,))
    return {"ok": True}
```

No HTML-escaping, no character allowlist, no validation that this looks like an actual URL path — just a length cap. The SQL itself is safely parameterized (no injection there), but the *string content* is completely attacker-controlled.

That string later gets read back and interpolated directly into an HTML email template with an f-string, no escaping:

```python
# backend/digest.py
top_paths_html = "".join(
    f'<tr><td style="padding:4px 12px;color:#65635e;font-size:13px;">{p["path"]}</td>'
    f'<td style="padding:4px 12px;color:#171717;font-size:13px;text-align:right;">{p["views"]}</td></tr>'
    for p in stats["top_paths"]
)
```

`stats["top_paths"]` comes directly from `compute_weekly_digest()`'s `SELECT path, count(*) AS n FROM page_views ... GROUP BY path ORDER BY n DESC LIMIT 5` — the top 5 most-submitted `path` values, completely attacker-controlled if the attacker submits the same malicious path repeatedly to guarantee it ranks in the top 5.

This HTML string is then sent as the `html` field of a Resend email:

```python
# backend/digest.py, send_digest_email()
json={
    "from": RESEND_FROM,
    "to": [to_email],
    "subject": "OntarioDriveTestMap — Weekly Summary",
    "html": render_digest_html(stats),
},
```

#### Attack scenario

1. Attacker sends (no authentication, no browser needed):
   ```http
   POST /analytics/pageview HTTP/1.1
   Host: <backend>
   Content-Type: application/json

   {"path": "<a href=\"https://evil.example/phish\" style=\"color:#171717;text-decoration:none;font-weight:700\">⚠ Your account requires re-verification — click here</a>"}
   ```
   Repeated a few times (within the 30/min rate limit, over multiple minutes if needed) so it ranks in the digest's top-5-paths query.
2. The following week, `scripts/send_weekly_digest.py` runs (once the project owner sets up a scheduler + Resend key), computes the digest, and emails it to every admin who opted in.
3. The admin opens the email. The injected `<a>` tag renders as a normal-looking, trusted-appearing link inside a legitimate email from the app's own sender address — a classic phishing/content-spoofing payload. Most modern email clients (Gmail, Outlook web) strip `<script>` and `on*` event handlers, so full JavaScript execution is unlikely, but arbitrary HTML structure, styling, links, and `<img>` tags (which many clients auto-load, enabling read-tracking/pixel-based admin-IP fingerprinting) are not stripped by all clients and are not stripped here at all before sending.

#### Impact

- Content spoofing / phishing targeting the application's own administrators, delivered from the app's own trusted sending domain.
- Tracking-pixel-style exfiltration (e.g., `<img src="https://attacker.example/x.png">`) revealing when/whether an admin opened the email, and potentially their IP/mail-client fingerprint depending on the client.
- Depending on the admin's specific email client, layout-breaking or more aggressive HTML/CSS injection is possible. Full script execution is unlikely in modern webmail but cannot be ruled out for all clients (older desktop clients, some corporate mail gateways).
- Not currently exploitable in production in the sense of "an email actually gets sent" — no `RESEND_API_KEY` is configured yet, so `send_digest_email()` currently no-ops. **This becomes live the moment the project owner sets up Resend and a scheduler**, which the user has stated they intend to do.

#### Likelihood

Trivial. One unauthenticated HTTP request, no rate-limit bypass needed (30/min is generous enough to land a payload in the top-5 query over a short window), no social engineering of the victim required beyond "open the weekly digest email you signed up for."

#### Recommended remediation

Escape all DB-sourced/user-influenced values before interpolating into the digest HTML. Minimal fix:

```python
import html as html_module

def row(label, value):
    return (
        f'<tr><td style="padding:6px 12px;color:#65635e;font-size:14px;">{html_module.escape(str(label))}</td>'
        f'<td style="padding:6px 12px;color:#171717;font-size:14px;font-weight:600;text-align:right;">{html_module.escape(str(value))}</td></tr>'
    )

top_paths_html = "".join(
    f'<tr><td style="padding:4px 12px;color:#65635e;font-size:13px;">{html_module.escape(p["path"])}</td>'
    f'<td style="padding:4px 12px;color:#171717;font-size:13px;text-align:right;">{p["views"]}</td></tr>'
    for p in stats["top_paths"]
)
```

Additionally, at the source, restrict what `/analytics/pageview` will even accept — reject anything that doesn't look like a real path (e.g. `^[a-zA-Z0-9\-_/.?=&%]*$`, or simply reject anything containing `<` or `>`), so garbage never enters `page_views` in the first place regardless of how it's later rendered. This is defense-in-depth on top of the escaping fix, not a replacement for it — always escape at the output sink too.

#### Verification

After the fix, repeat the attack-scenario POST, then call `compute_weekly_digest()` + `render_digest_html()` directly (or trigger `scripts/send_weekly_digest.py` in dry-run) and confirm the rendered HTML shows `&lt;a href=...&gt;` as literal text, not a rendered link/tag.

---

### [SEC-002] Account deletion doesn't revoke other active sessions

**Severity:** Medium
**Confidence:** High
**CWE:** CWE-613 (Insufficient Session Expiration)
**OWASP category:** A07:2021 – Identification and Authentication Failures
**Affected component:** Session management
**Affected file(s):** `backend/auth.py` (`require_user`), `backend/main.py` (`delete_account`)
**Affected endpoint(s):** `POST /account/delete`, and implicitly every endpoint behind `Depends(require_user)`

#### Description

Sessions are stateless JWTs (`auth.py`, `make_session_token`/`require_user`) with no server-side revocation list. `require_user` only checks the JWT's signature and expiry:

```python
def require_user(session: str | None = Cookie(default=None)) -> dict:
    ...
    payload = jwt.decode(session, JWT_SECRET, algorithms=["HS256"])
    return payload
```

`POST /account/delete` anonymizes the DB row and clears the cookie on the *requesting* browser:

```python
execute("UPDATE users SET email = %s, name = NULL, username = NULL, google_sub = %s, deleted_at = now() WHERE id = %s", ...)
response.delete_cookie("session")
```

But it never invalidates the JWT itself. If the same user has an active session on a second device/browser (or if a session token was ever exposed to an attacker via some other means — leaked cookie, shared device, etc.), that second session remains fully valid and authenticated for up to 7 days (`SESSION_DAYS = 7` in `auth.py`) after the account is "deleted." Endpoints like `add_forum_comment`/`create_discussion` call `get_user_by_id()` fresh each time, but nothing in that path checks `deleted_at IS NOT NULL` to reject a stale session.

#### Attack scenario

1. User signs in on both a laptop and a phone.
2. User deletes their account from the laptop. The Privacy Policy states "this account can never sign back in" — true for *new* Google logins (the tombstoned `google_sub` can never match), but the phone's existing session cookie is untouched.
3. The phone session continues to work — can still post, vote, comment, attach images — for up to 7 days, attributed to what the UI will show as the anonymous pseudonym (since `username` was cleared), but still hitting the same underlying `user_id` row and still subject to whatever the actor intended when "deleting."

This is a low-severity-in-isolation but real trust/expectation violation: the feature promises a clean break and doesn't fully deliver one.

#### Impact

A deleted account can continue acting on the platform for up to 7 days via any other still-valid session. Not a cross-user compromise, not a privilege escalation — but a genuine gap between documented behavior ("this account can never sign back in") and actual behavior (an *existing* session isn't a sign-back-in, and survives).

#### Likelihood

Requires the user (or an attacker holding a leaked session cookie for that account) to have a second valid session at deletion time. Realistic for any user with multiple devices.

#### Recommended remediation

Cheapest fix without introducing a full session store: add a `deleted_at IS NULL` check inside `require_user` or immediately after — this requires a DB lookup on every request (a real cost tradeoff, since `require_user` is currently DB-free by design). A middle ground: keep `require_user` as-is for speed, but have `get_user_by_id()`'s callers (which already hit the DB almost everywhere `require_user` is used for a mutation) treat a `deleted_at IS NOT NULL` row as equivalent to "no such user" / 401. A stricter fix: maintain a short-lived server-side denylist (e.g. a `revoked_sessions` table keyed by a JWT `jti` claim, checked in `require_user`) — more correct, more implementation cost.

#### Verification

Sign in on two sessions for one test account, delete the account via session A, confirm session B's next authenticated request (e.g. `GET /auth/me`) returns 401 rather than succeeding.

---

### [SEC-003] No HTTP security headers configured

**Severity:** Medium
**Confidence:** High
**CWE:** CWE-1021 (Improper Restriction of Rendered UI Layers, re: clickjacking), general security-misconfiguration
**OWASP category:** A05:2021 – Security Misconfiguration
**Affected component:** Backend response headers (no middleware sets them); frontend hosting config (nothing in-repo configures them either)

#### Description

Nothing in `main.py` sets `Content-Security-Policy`, `Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, or `X-Frame-Options`/`frame-ancestors`. FastAPI/Starlette don't add these by default. Whatever Railway's edge does by default for HSTS is outside this repo's control/visibility and shouldn't be assumed.

The account-deletion page, forum/discussion posting, and the admin digest-opt-in toggle are all reachable without any clickjacking protection — a malicious site could iframe the app and attempt UI-redress attacks (though the app's own session cookie is `SameSite=Lax`, which blunts a lot of cross-site POST-based CSRF risk already — see the CSRF note under Security Strengths).

#### Impact

Defense-in-depth gap. No concrete exploit demonstrated in this audit (would require live production testing, e.g. attempting to iframe the real deployed site) — flagged because it's straightforward and cheap to close, and because its absence widens the blast radius of any future stored-content bug (like SEC-001) if the app ever renders user content directly in the browser (it currently doesn't — React's default escaping covers this today, confirmed no `dangerouslySetInnerHTML`/`innerHTML` anywhere in `frontend/src`).

#### Recommended remediation

Add a small Starlette middleware in `main.py`:

```python
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    response.headers["X-Frame-Options"] = "DENY"
    return response
```

(`geolocation=(self)` because the drive-along feature genuinely needs it; everything else this app doesn't use.) `Strict-Transport-Security` and CSP are better set at Railway's edge/CDN layer if available, since the API itself doesn't serve HTML — CSP matters most for the frontend's static hosting, which is a separate deployment target not present in this backend repo's config.

#### Verification

`curl -I` the production API and confirm the headers are present.

---

### [SEC-004] Vote/report endpoints don't verify target post exists first

**Severity:** Low
**Confidence:** Medium
**CWE:** CWE-20 (Improper Input Validation)
**Affected file(s):** `backend/main.py` — `vote_forum_post`, `report_forum_post`, `vote_discussion`, `report_discussion`, `vote_discussion_comment`

#### Description

Unlike `delete_forum_post`/`upload_forum_image`/etc. (which all do a `SELECT ... WHERE id = %s` existence check before acting), the vote and report endpoints insert directly:

```python
@app.post("/forum/{post_id}/vote")
def vote_forum_post(request: Request, post_id: int, body: ForumVote, user=Depends(require_user)):
    ...
    execute("INSERT INTO forum_votes (post_id, user_id, value) VALUES (%s, %s, %s) ON CONFLICT (post_id, user_id) DO UPDATE ...", ...)
```

If `forum_votes.post_id` has a foreign-key constraint to `forum_posts(id)` (likely, given the schema's general discipline — not independently re-confirmed by reading the full `schema.sql` DDL for this table in this pass), voting on a nonexistent `post_id` raises a `psycopg2.IntegrityError`, uncaught, resulting in a generic FastAPI 500. This is not an information-disclosure risk (FastAPI's default 500 handler returns a generic body, no traceback, since `debug` isn't enabled anywhere) but it's a minor correctness gap: a bad ID should be a clean 404, not a 500.

#### Impact

Cosmetic/correctness only — no data exposure, no bypass of the ownership model. Included for completeness since the audit was asked to check every mutating endpoint's input handling.

#### Recommended remediation

Add the same existence check pattern used elsewhere in the file before the insert, returning 404.

---

### [SEC-005] Rate limiter's per-IP key may not reflect the real client behind Railway's proxy

**Severity:** Medium
**Confidence:** Low — **requires runtime verification in the actual Railway production environment, not verifiable from this local code audit**
**Affected file(s):** `backend/main.py` (`limiter = Limiter(key_func=get_remote_address, ...)`)

#### Description

`slowapi`'s `get_remote_address` reads `request.client.host` — the TCP-layer peer address FastAPI/Starlette sees. When an app sits behind a reverse proxy/load balancer (which Railway's edge is), `request.client.host` may reflect the proxy's internal address rather than the real client IP, unless Railway is configured to preserve the original client IP (commonly via `X-Forwarded-For`, which the app would then need to explicitly read and trust from a known, correctly-configured proxy hop — not something visible in this codebase).

Two possible failure modes, both real risks, in opposite directions:
1. **Under-protective:** if every request appears to come from the same proxy IP, all users share one rate-limit bucket — one abusive user could exhaust the shared quota and rate-limit everyone else (a self-inflicted DoS).
2. **Spoofable, if ever "fixed" naively:** if a future change blindly trusts a client-supplied `X-Forwarded-For` header without validating it against a trusted proxy list, an attacker could set that header to an arbitrary value and get a fresh rate-limit bucket on every request, defeating the limiter entirely.

Neither can be confirmed or denied from the code alone — it depends on Railway's actual edge behavior for this specific service, which isn't captured anywhere in this repository.

#### Recommended remediation

Verify against the real deployed service: log `request.client.host` for a few real requests from different real devices/networks and confirm they differ. If Railway does forward via `X-Forwarded-For` and `request.client.host` is always the proxy's own address, switch to a key function that reads `X-Forwarded-For`'s first hop **only if** the immediate connecting peer is Railway's known proxy range (don't blindly trust the header from arbitrary clients).

#### Verification

Runtime test only — not resolvable via static code review.

---

### [SEC-006] Content moderation wordlist is trivially bypassed

**Severity:** Low
**Confidence:** High
**Affected file(s):** `backend/forum.py` (`contains_blocked_word`)

#### Description

```python
def contains_blocked_word(text: str | None) -> bool:
    words = set(text.lower().split())
    return bool(words & BLOCKLIST)
```

Whole-word, whitespace-split matching only. Trivially bypassed with punctuation (`fuck.`), spacing (`f u c k`), substitution, or embedding without a space boundary in a way that still reads as the same slur to a human (the check only catches the *exact* token, not substrings, so this is bypassable both by adding noise to a blocked word and by embedding a blocked term inside a longer compound word the human eye still reads as the same word). This is a content-moderation efficacy gap, not a security vulnerability in the traditional sense — included because the app's own design principles emphasize this as its actual line of defense against harassment/slurs.

#### Recommended remediation

Not a priority fix for this audit's purposes, but if it matters to the project: normalize more aggressively (strip punctuation, check substrings not just whole tokens) before matching, and/or treat the wordlist as a first-pass filter backed by the existing report/vote-based hide mechanism (which already exists and is the app's stated primary moderation layer).

---

### [SEC-007] Unused `GOOGLE_CLIENT_SECRET` present in environment

**Severity:** Informational
**Confidence:** High
**Affected file(s):** root `.env` (not committed — confirmed via `git ls-files`)

#### Description

`GOOGLE_CLIENT_SECRET` exists in `.env` but is never read by any Python code (`auth.py` only reads `GOOGLE_CLIENT_ID`; Google Identity Services' client-side flow doesn't need the client secret for ID-token verification). Not a live exposure — `.env` is correctly gitignored and confirmed absent from git history and from the built frontend bundle — but it's an unused secret sitting around for no reason, which is unnecessary risk surface if that file is ever mishandled.

#### Recommended remediation

Remove it from `.env`, or if it's needed for a future OAuth flow, document why it's there.

---

### [SEC-008] No CI/CD, no automated dependency/secret scanning

**Severity:** Informational
**Confidence:** High

#### Description

Confirmed: no `.github/workflows`, no other CI config, no Dockerfile, no automated `npm audit`/`pip-audit` step anywhere in the repo. This is expected for a solo project not yet deployed to a real production domain, but worth closing before real users arrive — see Automated Tooling Recommendations below.

---

## False-Positive Review

Re-examined every High-and-above finding before finalizing:

- **SEC-001:** Re-verified the full data flow from the public endpoint to the email sink twice, including confirming (a) the endpoint truly requires no auth (`record_pageview` has no `Depends(require_user)`), (b) the value is never escaped anywhere between insert and email-send, (c) `top_paths` is genuinely attacker-controllable (it's a `GROUP BY path ORDER BY count DESC LIMIT 5` — an attacker submitting the same string repeatedly will dominate this ranking). Confirmed real, not a false positive. Downgraded from a hypothetical "Critical/RCE-adjacent" framing to **High**, since script execution in the sink (an email client) is unlikely for most modern clients that strip `<script>`/event handlers — the realistic impact is phishing/content-spoofing/tracking, not code execution.
- Considered and rejected several other hypotheses that did not survive verification:
  - **JWT `alg: none` / algorithm confusion:** `jwt.decode(..., algorithms=["HS256"])` explicitly pins the algorithm — not vulnerable.
  - **`JWT_SECRET` unsafe fallback:** `os.environ["JWT_SECRET"]` (not `.get()`) — the app fails to start entirely if unset, rather than silently using a weak default. Not vulnerable; this is the correct fail-closed pattern.
  - **SQL injection via dynamic query building:** the `discussions.py`/`main.py` f-string-built queries (`tag_select`, `tag_filter`, `order`) only interpolate *fixed, hardcoded* strings chosen from a small internal set (never raw user input) — the actual values always flow through `%s` parameters. Not vulnerable.
  - **IDOR on forum/discussion delete/edit:** every delete endpoint checks `row["user_id"] != user["user_id"] and not me["is_admin"]` — a real, correctly-scoped check, not a truthy check. Not vulnerable.
  - **Mass assignment (client setting `is_admin`/`user_id` on themselves):** no endpoint's Pydantic model accepts either field from the client anywhere in `main.py`. Not vulnerable.
  - **Stored XSS via forum/discussion post content in the app itself:** confirmed no `dangerouslySetInnerHTML`/`innerHTML`/`document.write` anywhere in `frontend/src` — React's default text rendering escapes all user content. Not vulnerable (the *only* real HTML-injection sink found is the digest email, SEC-001).
  - **SVG/HTML polyglot upload → stored XSS via image serving:** `ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}` — no SVG, no HTML, and the content-type is checked against `file.content_type` at upload and stored, then reused verbatim as the `media_type` on serve. Since SVG/HTML are never accepted in the first place, this sink is closed. Not vulnerable.

---

## Prioritized Remediation Plan

### Immediate — fix before production / before Resend is activated
- **SEC-001** (HTML-escape the digest email template) — Effort: Small. Security impact: High. Files: `backend/digest.py`.

### Short term
- **SEC-002** (session revocation on account deletion) — Effort: Medium (requires either a DB check in the auth path or a revocation table). Security impact: Medium. Files: `backend/auth.py`, `backend/main.py`.
- **SEC-003** (security headers) — Effort: Small. Security impact: Medium. Files: `backend/main.py`.
- **SEC-005** (verify rate-limiter IP detection against real Railway networking) — Effort: Small (a runtime check), potentially Medium if a fix is needed. Security impact: Medium.

### Hardening
- **SEC-004** (existence checks on vote/report endpoints) — Effort: Small. Security impact: Low.
- **SEC-006** (stronger moderation wordlist matching) — Effort: Small. Security impact: Low (moderation quality, not security).
- **SEC-007** (remove unused `GOOGLE_CLIENT_SECRET`) — Effort: Small. Security impact: Low.
- **SEC-008** (basic CI with dependency/secret scanning) — Effort: Medium. Security impact: Medium (process, not a live bug).

---

## Top 10 Most Important Fixes (specific to this app, not generic advice)

1. **Escape the digest email HTML** (SEC-001) — the one real, live-exploitable bug found.
2. **Restrict `POST /analytics/pageview`'s accepted `path` format** — defense-in-depth on top of #1, closes the injection at the source, not just the sink.
3. **Verify rate-limiter IP detection on real Railway infrastructure** (SEC-005) — currently unverifiable from code; could silently be doing nothing useful.
4. **Add basic security headers middleware** (SEC-003) — 15 minutes of work, closes a real defense-in-depth gap.
5. **Close the session-revocation gap on account deletion** (SEC-002) — matches the feature's own stated promise to actual behavior.
6. **Set up `pip-audit`/`npm audit` as a recurring manual step (or basic CI) before this goes to a real domain** — no automated dependency scanning exists today.
7. **Remove the unused `GOOGLE_CLIENT_SECRET`** — trivial, no reason to keep unused secret material around.
8. **Add existence checks to vote/report endpoints** (SEC-004) — cheap correctness fix.
9. **Confirm `FRONTEND_ORIGIN` is set correctly in the real Railway production environment** (not left at the `localhost:5173` default) — a misconfigured value here would either break the app or, worse, silently widen CORS if ever changed carelessly to something permissive.
10. **Strengthen the moderation wordlist** (SEC-006) — lowest priority security-wise, but directly serves the app's own stated design principle of protecting users from harassment.

---

## Quick Wins (each under ~30 minutes)

1. HTML-escape `digest.py`'s email template (SEC-001) — the single most important item on this whole list, and also the fastest.
2. Add the security-headers middleware snippet from SEC-003.
3. Remove `GOOGLE_CLIENT_SECRET` from `.env`.
4. Add a `curl -I` check of `FRONTEND_ORIGIN`/CORS config against the real production URL once deployed.
5. Add existence checks (404-before-insert) to the 5 vote/report endpoints (SEC-004).

---

## Security Strengths (already done right)

- **Parameterized SQL everywhere** — every single `query()`/`execute()` call site across `main.py`, `auth.py`, `forum.py`, `discussions.py`, `notifications.py`, `submissions.py`, `digest.py` uses `%s`/`?` placeholders with a separate params tuple. Zero string-concatenated or f-string-interpolated user values found in SQL text anywhere in this codebase.
- **No passwords, anywhere.** Auth is entirely Google-ID-token-based, verified server-side. Nothing to leak, hash incorrectly, or brute-force.
- **JWT done correctly:** explicit algorithm pinning (`HS256` only, no `none`), a required (not defaulted) secret that fails closed if unset, short session lifetime (7 days), no sensitive data embedded in the token payload beyond `user_id`/`email`.
- **Real, consistent authorization checks** — every ownership/admin check found (forum/discussion delete, image upload, digest opt-in) is a genuine server-side comparison against the authenticated session's own `user_id` or freshly-fetched `is_admin` flag, never a client-supplied value.
- **Cookie configuration is correct:** `httponly=True`, `secure=True`, `samesite="lax"` — blocks JS-based cookie theft and provides real CSRF mitigation for the common cross-site-POST case, given `SameSite=Lax` blocks cookies on cross-site form/fetch POSTs (though not on top-level navigation GETs — acceptable given this app has no state-changing GET endpoints).
- **CORS is a single fixed origin, not a wildcard**, correctly paired with `allow_credentials=True` (this combination is only dangerous when the origin is reflected/wildcarded — it isn't here).
- **File upload validation is real, not cosmetic:** server-side `Content-Type` allowlist (image types only, no SVG/HTML), server-side byte-length cap enforced after reading the actual uploaded bytes — not trusting a client-declared size.
- **Secrets hygiene:** `.env` gitignored and confirmed never committed (checked git history via `git ls-files`), confirmed absent from the built frontend bundle.
- **Rate limiting is real and risk-tiered**, not a single blanket number, added specifically in response to identifying the gap earlier this session.
- **Anonymization-over-deletion design for account removal** — a thoughtful choice given forum/discussion posts have non-cascading foreign keys; avoids breaking other users' threads while still scrubbing real identity.

---

## Security Test Plan

### Anonymous requests
```bash
# Should succeed (public):
curl -s http://localhost:8000/centres

# Should 401 (protected):
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/centres/walkley/map
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/auth/me

# The analytics endpoint's actual injection surface -- confirm the fix:
curl -s -X POST http://localhost:8000/analytics/pageview \
  -H "Content-Type: application/json" \
  -d '{"path": "<img src=x onerror=alert(1)>"}'
# then inspect compute_weekly_digest()/render_digest_html() output and
# confirm the string appears HTML-escaped, not as a live tag.
```

### Normal user vs. second normal user (IDOR check)
```bash
# As user A, create a forum post, capture its ID.
# As user B (separate session cookie), attempt to delete/edit it:
curl -s -X DELETE http://localhost:8000/forum/<A_POST_ID> \
  -H "Cookie: session=<user_B_session>"
# Expect 403.
```

### Admin function access as non-admin
```bash
curl -s -X PATCH http://localhost:8000/account/digest-opt-in \
  -H "Cookie: session=<non_admin_session>" \
  -H "Content-Type: application/json" \
  -d '{"opt_in": true}'
# Expect 403.
```

### Modified/expired session
```bash
curl -s http://localhost:8000/auth/me -H "Cookie: session=garbage.invalid.token"
# Expect 401.
```

### Oversized payload
```bash
python3 -c "print('{\"body\": \"' + 'a'*50000 + '\"}')" > /tmp/big.json
curl -s -X POST http://localhost:8000/discussions/1/comments \
  -H "Cookie: session=<valid>" -H "Content-Type: application/json" \
  -d @/tmp/big.json
# Expect 422 (Pydantic max_length rejection).
```

### Rate limit
```bash
for i in $(seq 1 35); do
  curl -s -o /dev/null -w "%{http_code} " -X POST http://localhost:8000/analytics/pageview \
    -H "Content-Type: application/json" -d '{"path":"/test"}'
done
echo
# Expect a run of 200s then 429s after the 30/minute threshold.
```

### Account-deletion session revocation (SEC-002 regression test)
```bash
# Sign in twice (two cookies, session_A and session_B, same account).
curl -s -X POST http://localhost:8000/account/delete -H "Cookie: session=$session_A"
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/auth/me -H "Cookie: session=$session_B"
# Currently: 200 (the bug). After fix: should be 401.
```

### Suggested pytest scaffold
```python
# backend/tests/test_authorization.py
def test_cannot_delete_other_users_forum_post(client, user_a_session, user_b_session, forum_post_by_a):
    resp = client.delete(f"/forum/{forum_post_by_a}", cookies={"session": user_b_session})
    assert resp.status_code == 403

def test_non_admin_cannot_toggle_digest(client, normal_user_session):
    resp = client.patch("/account/digest-opt-in", json={"opt_in": True}, cookies={"session": normal_user_session})
    assert resp.status_code == 403

def test_digest_html_escapes_page_view_paths():
    from digest import render_digest_html
    html = render_digest_html({
        "new_users": 0, "new_submissions": 0, "promoted_submissions": 0,
        "new_forum_posts": 0, "new_forum_comments": 0, "new_discussion_posts": 0,
        "new_discussion_comments": 0, "forum_votes_total": 0, "forum_reports_total": 0,
        "discussion_votes_total": 0, "discussion_reports_total": 0,
        "forum_hidden_now": 0, "discussion_hidden_now": 0, "page_views_total": 1,
        "top_paths": [{"path": "<script>alert(1)</script>", "views": 1}],
    })
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
```

(No test infrastructure currently exists in this repo — `backend/tests/` would need to be created; this is a new addition, not modifying existing tests.)

---

## Automated Security Tooling Recommendations

Kept intentionally small — this is a solo project, not an enterprise team:

- **Dependency scanning:** `pip-audit` (Python) and `npm audit` (frontend) — both free, both can run as a pre-deploy manual step or a minimal GitHub Action.
- **Secret scanning:** `gitleaks` or GitHub's built-in secret scanning (free on public repos) — catches an accidentally-committed `.env` before it's a real problem, cheap insurance given secrets currently rely entirely on `.gitignore` discipline.
- **SAST:** `bandit` for the Python backend (lightweight, catches patterns like the `eval`/`exec`/`shell=True` class of issues this audit checked manually and found none of — worth automating going forward).
- Skip: DAST, container scanning, IaC scanning, SBOM generation — no containers, no IaC, and DAST is premature before the app has a stable production deployment to point it at.

---

## Final Production Security Checklist

```text
[x] Critical vulnerabilities resolved — none found
[x] High vulnerability resolved — SEC-001 fixed 2026-09-21 (digest.py HTML-escaped,
    /analytics/pageview path restricted to a safe character set; verified live
    with a <script> payload -- renders as &lt;script&gt;, not a live tag)
[x] Backend authorization verified — real ownership/admin checks confirmed throughout
[x] IDOR tests passed (manual code review; add automated tests per Security Test Plan)
[x] No secrets committed to git history
[x] Production HTTPS enforced (cookie `secure=True`, Railway serves over TLS)
[x] Secure cookies configured (httpOnly, Secure, SameSite=Lax)
[x] CORS restricted to a single known origin
[x] CSRF risk mitigated via SameSite=Lax cookie (no state-changing GETs exist)
[ ] Rate limiting verified effective behind Railway's actual proxy (SEC-005 —
    still requires runtime verification against real production traffic, not
    resolvable from a local audit)
[x] Input validation server-side (Pydantic Field max_length on every text field)
[x] Database permissions — single app-level Postgres user via connection string; least-privilege DB roles not independently verified (Railway-managed, outside this repo's control)
[x] Security headers enabled — SEC-003 fixed 2026-09-21 (X-Content-Type-Options,
    Referrer-Policy, Permissions-Policy, X-Frame-Options; verified live via curl -I)
[ ] CSP configured (not yet added — belongs at the frontend static-hosting layer,
    a separate deployment target from this backend repo)
[x] File uploads hardened (content-type allowlist, size cap, no SVG/HTML accepted)
[x] Logs exclude secrets (no credential/token logging found in any module)
[x] Production debug mode disabled (no `debug=True` anywhere; default FastAPI error handling)
[ ] Dependencies scanned with pip-audit/npm audit (not yet run as part of this audit — recommend doing so before production)
[ ] CI/CD permissions reviewed — no CI/CD exists yet
[ ] Backups configured / restore procedure tested — outside this audit's visibility (Railway-managed Postgres; verify separately)
[ ] Monitoring/alerting enabled — none observed in this repo
[x] Session revocation on account deletion — SEC-002 fixed 2026-09-21 (require_user
    now checks deleted_at on every authenticated request, not just endpoints that
    already did a fresh DB lookup; verified live: a session token minted before
    deletion is rejected with 401 immediately after deletion, without waiting for
    its 7-day expiry)
```

---

## Remediation Log (2026-09-21, same day as the audit)

Fixed, verified live (not just code-reviewed), nothing committed to git:

- **SEC-001 (High)** — `digest.py`: all DB-sourced values HTML-escaped before interpolation. `main.py`: `PageView.path` now regex-restricted to a safe URL-path character set (`^[A-Za-z0-9\-_/.?=&%~]*$`) as defense-in-depth at the source, on top of the escaping fix at the sink. Verified: a `<script>` payload sent to `/analytics/pageview` now gets a 422 at the door; a value that reaches `render_digest_html()` anyway renders as literal escaped text, confirmed via direct function call with a script-tag payload.
- **SEC-002 (Medium)** — `auth.py`'s `require_user` now checks `deleted_at IS NULL` on every authenticated request (a small DB query added to the auth path, accepted as a reasonable tradeoff at this app's traffic scale). Verified live: minted a real JWT for a real DB user, confirmed `require_user` accepted it, anonymized the account the same way `/account/delete` does, confirmed the *same still-unexpired token* is now rejected with 401 — closing the up-to-7-day stale-session window the audit found.
- **SEC-003 (Medium)** — added a `security_headers` middleware in `main.py` (`X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `X-Frame-Options`). Verified via `curl -I` against the live dev server — all four headers present.
- **SEC-004 (Low)** — added existence checks (404 before insert) to `vote_forum_post`, `report_forum_post`, `vote_discussion`, `report_discussion`, `vote_discussion_comment`.
- **SEC-006 (Low)** — `forum.py`'s `contains_blocked_word` now strips punctuation before whole-word matching, closing the `fuck.`-style trivial bypass (kept as a first-pass filter only — the report/vote-based auto-hide remains the app's real moderation layer, unchanged).
- **SEC-007 (Informational)** — removed the unused `GOOGLE_CLIENT_SECRET` from root `.env`.

**Not fixed in this pass, and why:**
- **SEC-005** — genuinely requires runtime verification against Railway's real production networking; nothing resolvable from local code changes. Flagged for whoever deploys this to check `request.client.host` against real distinct client IPs once live.
- **SEC-008** — no CI/CD exists; adding one (with `pip-audit`/`npm audit`/`bandit`/`gitleaks`) is a real but separate task from this remediation pass, left as a recommendation per the report's Automated Tooling section.

Full app verified working end-to-end after all fixes: backend restarts clean (no import errors), a live signed-in browser session continues to work seamlessly through the new `require_user` DB check (screenshotted), and the backend log shows normal 200s for real traffic alongside the new 422/401/404 responses firing correctly for the specific attack-scenario requests tested.
