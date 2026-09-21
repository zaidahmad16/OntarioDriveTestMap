"""
discussions.py — general Q&A / comment board.

Deliberately a separate feature from forum.py's "where I messed up"
board, not a generalization of it (the owner's explicit call: two
different features, not one flexible one). Same moderation shape --
wordlist, per-user daily rate limit, score/report auto-hide computed at
query time in main.py -- but no junction requirement: a discussion post
isn't anchored to a real location, so there's nothing here that touches
osm.db.
"""

from db import query

# The 2026-09-21 redesign added test_type/post_type/route_line_id to
# discussion_posts (see schema.sql). New installs get them for free via
# CREATE TABLE; an already-running database needs the matching ALTER TABLE
# run once by an operator with direct DB access (this app's own sandboxed
# environment cannot write to the live database itself). Checked once and
# cached rather than per-request, since the schema doesn't change at
# runtime.
_TAG_COLUMNS_CACHE = None


def has_tag_columns() -> bool:
    global _TAG_COLUMNS_CACHE
    if _TAG_COLUMNS_CACHE is None:
        row = query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'discussion_posts' AND column_name = 'test_type'",
            one=True,
        )
        _TAG_COLUMNS_CACHE = row is not None
    return _TAG_COLUMNS_CACHE


POST_DAILY_LIMIT = 10
COMMENT_DAILY_LIMIT = 30

SCORE_HIDE_THRESHOLD = -5
REPORT_HIDE_THRESHOLD = 3


def over_post_rate_limit(user_id: int) -> bool:
    row = query(
        "SELECT COUNT(*) AS n FROM discussion_posts "
        "WHERE user_id = %s AND created_at > now() - interval '1 day'",
        (user_id,),
        one=True,
    )
    return row["n"] >= POST_DAILY_LIMIT


def over_comment_rate_limit(user_id: int) -> bool:
    row = query(
        "SELECT COUNT(*) AS n FROM discussion_comments "
        "WHERE user_id = %s AND created_at > now() - interval '1 day'",
        (user_id,),
        one=True,
    )
    return row["n"] >= COMMENT_DAILY_LIMIT
