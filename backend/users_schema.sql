-- Run once, separately from the main schema.sql (which is already applied).
-- Google sign-in never gives us a password -- google_sub is the permanent,
-- unique identifier Google assigns per account, safe to key on.
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    google_sub TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    -- Chosen after Google sign-in, not from Google itself -- nullable
    -- until the user picks one. Forum posts/comments need it for
    -- @mentions to mean anything; a fallback pseudonym (forum.py)
    -- covers a user before they've set one.
    username TEXT UNIQUE,
    is_admin BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_login TIMESTAMPTZ DEFAULT now(),
    -- Set by POST /account/delete (2026-09-21). We anonymize rather than
    -- DELETE the row: forum/discussion posts, comments, votes, and
    -- submissions all have NOT NULL FKs to users(id) with no ON DELETE
    -- CASCADE, so a real row delete would either fail outright or require
    -- cascading away other people's ability to read a thread they replied
    -- to. Anonymizing (scrub email/name/google_sub/username, keep the id)
    -- preserves referential integrity and community content while removing
    -- the actual PII. deleted_at is set once and the account can never log
    -- back in afterward (google_sub is overwritten with a tombstone value
    -- that will never match a real Google sub).
    deleted_at TIMESTAMPTZ,
    -- Admin-only opt-in (2026-09-21) for the weekly summary email sent by
    -- scripts/send_weekly_digest.py. Default false -- nobody gets emailed
    -- without explicitly turning this on, and it's ignored entirely for
    -- non-admin accounts even if somehow set.
    weekly_digest_opt_in BOOLEAN NOT NULL DEFAULT false
);
