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
    decision belongs to the frontend, not this API."""
    if not session:
        raise HTTPException(status_code=401, detail="Sign in required.")
    try:
        payload = jwt.decode(session, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    return payload
