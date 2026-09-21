"""
notifications.py — in-app notifications only.

No email, no push -- a stored list a user checks in the app itself
(a bell icon). No new external dependency: same "basic version" spirit
as everything else in this project. Two triggers, both fired right
after the post/comment that caused them is inserted:

  mention  a real, distinct, non-self @username appears in the text
  comment  someone other than the post's own author comments on it

Both are best-effort: a failure here should never block the post/
comment itself from succeeding, so callers wrap these in a way that
doesn't propagate exceptions past the main write.
"""

import re

from db import execute, query

MENTION_RE = re.compile(r"@(\w+)")


def notify_mentions(text: str | None, source: str, post_id: int, comment_id: int | None, actor_user_id: int) -> None:
    # Best-effort per the module docstring -- but none of the 6 call
    # sites in main.py actually wrapped this, so a notification failure
    # (e.g. a transient DB error) was propagating past an *already
    # committed* post/comment insert and turning into a false 500 for
    # the caller (bug found in a 2026-09-21 cleanup pass). The try/except
    # belongs here, once, not duplicated at every call site.
    try:
        if not text:
            return
        usernames = list(set(MENTION_RE.findall(text)))
        if not usernames:
            return
        rows = query("SELECT id FROM users WHERE username = ANY(%s)", (usernames,))
        for row in rows:
            if row["id"] == actor_user_id:
                continue
            _create(row["id"], "mention", source, post_id, comment_id, actor_user_id, text[:140])
    except Exception as e:
        print(f"[notifications] notify_mentions failed (non-fatal): {e}")


def notify_comment(post_author_id: int, source: str, post_id: int, comment_id: int, actor_user_id: int, text: str) -> None:
    try:
        if post_author_id == actor_user_id:
            return
        _create(post_author_id, "comment", source, post_id, comment_id, actor_user_id, text[:140])
    except Exception as e:
        print(f"[notifications] notify_comment failed (non-fatal): {e}")


def _create(user_id, kind, source, post_id, comment_id, actor_user_id, excerpt) -> None:
    execute(
        """
        INSERT INTO notifications (user_id, kind, source, post_id, comment_id, actor_user_id, excerpt)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, kind, source, post_id, comment_id, actor_user_id, excerpt),
    )
