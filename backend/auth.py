"""
auth.py — Google sign-in, verified server-side, then our own session.

The frontend never sends us a password. It gets an ID token straight from
Google via Google Identity Services, we verify that token against
Google's own public keys (not by trusting the frontend), then issue our
own short-lived JWT in an httpOnly cookie. Every protected route just
checks that cookie — Google is only involved at login.
"""

from dotenv import load_dotenv
load_dotenv()  # reads the repo root .env automatically

import os
import time

import jwt
from fastapi import Cookie, HTTPException
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from db import execute, query

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
JWT_SECRET = os.environ["JWT_SECRET"]
SESSION_DAYS = 7

_google_request = google_requests.Request()


def verify_google_token(credential: str) -> dict:
    """Raises ValueError if the token is invalid, expired, or wasn't
    issued for this app -- never trust a token without this check."""
    return id_token.verify_oauth2_token(
        credential, _google_request, GOOGLE_CLIENT_ID
    )


def get_or_create_user(google_payload: dict) -> dict:
    sub = google_payload["sub"]
    email = google_payload["email"]
    name = google_payload.get("name")

    existing = query("SELECT * FROM users WHERE google_sub = %s", (sub,), one=True)
    if existing:
        execute(
            "UPDATE users SET last_login = now() WHERE id = %s", (existing["id"],)
        )
        return existing

    rows = execute(
        """
        INSERT INTO users (google_sub, email, name)
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        (sub, email, name),
    )
    return rows[0]


def make_session_token(user: dict) -> str:
    payload = {
        "user_id": user["id"],
        "email": user["email"],
        "exp": int(time.time()) + SESSION_DAYS * 86400,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def require_user(session: str | None = Cookie(default=None)) -> dict:
    """FastAPI dependency for any route that needs a signed-in user.
    A missing or invalid cookie is a 401, not a redirect -- that
    decision belongs to the frontend, not this API.

    Sessions are stateless JWTs with no revocation list, so a session
    issued before account deletion would otherwise stay valid for the
    rest of its 7-day lifetime even after /account/delete runs on a
    different device (SEC-002, 2026-09-21 security audit). Checking
    deleted_at here -- on every authenticated request, not just the
    ones that happen to call get_user_by_id -- closes that gap for any
    endpoint behind this dependency, not just the ones that already did
    a fresh DB lookup."""
    if not session:
        raise HTTPException(status_code=401, detail="Sign in required.")
    try:
        payload = jwt.decode(session, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")

    row = query(
        "SELECT deleted_at FROM users WHERE id = %s", (payload["user_id"],), one=True
    )
    if row is None or row["deleted_at"] is not None:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")

    return payload


def get_user_by_id(user_id: int) -> dict | None:
    """The JWT session only carries user_id/email -- username and
    is_admin can change after a token is issued, so anything that needs
    their current value looks them up fresh rather than trusting the
    cookie."""
    return query("SELECT * FROM users WHERE id = %s", (user_id,), one=True)
