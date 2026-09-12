-- Run once, separately from the main schema.sql (which is already applied).
-- Google sign-in never gives us a password -- google_sub is the permanent,
-- unique identifier Google assigns per account, safe to key on.
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    google_sub TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_login TIMESTAMPTZ DEFAULT now()
);
