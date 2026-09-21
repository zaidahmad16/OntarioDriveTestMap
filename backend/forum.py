"""
forum.py — moderation for the community forum.

Primary layer is still fully automated (the original standing
constraint, Notion "Frontend Design" backlog page): the crowd's own
votes and reports gate visibility (query-time filtering in main.py's
list_forum_posts, not a stored flag that could drift), a small wordlist
blocks the obvious cases, and a per-user daily cap limits how much
damage one account can do before either signal has had time to
accumulate. An admin override (is_admin on users, main.py's delete
endpoints) was added on top of that afterwards -- a deliberate
departure from "no manual review of any kind," at the owner's explicit
request, not an oversight.
"""

import re

from db import query

BLOCKLIST = {
    "fuck", "fucking", "shit", "cunt", "nigger", "nigga", "faggot",
    "retard", "retarded", "whore", "spic", "chink", "kike",
}

POST_DAILY_LIMIT = 10
COMMENT_DAILY_LIMIT = 30

SCORE_HIDE_THRESHOLD = -5
REPORT_HIDE_THRESHOLD = 3


_ADJECTIVES = [
    "Careful", "Nervous", "Confident", "Cautious", "Quick", "Steady",
    "Patient", "Sharp", "Calm", "Bold", "Quiet", "Focused", "Lucky",
    "Determined", "Rusty", "Smooth",
]
_NOUNS = [
    "Otter", "Falcon", "Beaver", "Moose", "Heron", "Fox", "Loon",
    "Badger", "Raven", "Lynx", "Gull", "Deer", "Owl", "Hare", "Wolf",
    "Swan",
]


def pseudonym(user_id: int) -> str:
    """Deterministic, non-identifying display handle for a forum
    post/comment -- e.g. "Careful Otter" -- so a thread reads as a real
    discussion between people, not disconnected anonymous text blocks,
    without exposing the poster's real name or email. Same spirit as
    the rest of the app using author_hash instead of raw identity."""
    return f"{_ADJECTIVES[user_id % len(_ADJECTIVES)]} {_NOUNS[(user_id * 7) % len(_NOUNS)]}"


_WORD_STRIP_RE = re.compile(r"[^\w\s]")


def contains_blocked_word(text: str | None) -> bool:
    """SEC-006, 2026-09-21 security audit: whole-word whitespace-split
    matching alone was trivially bypassed with punctuation ("fuck.").
    Stripping punctuation before splitting closes that specific gap.
    This remains a first-pass filter, not the app's real moderation
    line of defense -- that's the report/vote-based auto-hide, which
    exists precisely because no wordlist can be complete."""
    if not text:
        return False
    stripped = _WORD_STRIP_RE.sub("", text.lower())
    words = set(stripped.split())
    return bool(words & BLOCKLIST)


def over_post_rate_limit(user_id: int) -> bool:
    row = query(
        "SELECT COUNT(*) AS n FROM forum_posts "
        "WHERE user_id = %s AND created_at > now() - interval '1 day'",
        (user_id,),
        one=True,
    )
    return row["n"] >= POST_DAILY_LIMIT


def over_comment_rate_limit(user_id: int) -> bool:
    row = query(
        "SELECT COUNT(*) AS n FROM forum_comments "
        "WHERE user_id = %s AND created_at > now() - interval '1 day'",
        (user_id,),
        one=True,
    )
    return row["n"] >= COMMENT_DAILY_LIMIT
