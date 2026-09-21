CREATE TABLE centres (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE traces (
    id SERIAL PRIMARY KEY,
    source_id TEXT NOT NULL,
    centre_id TEXT NOT NULL REFERENCES centres(id),
    test_class TEXT,
    reliability NUMERIC,
    observed_at DATE,
    author_hash TEXT,
    status TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_id, centre_id)
);

CREATE TABLE trace_turns (
    id SERIAL PRIMARY KEY,
    trace_id INTEGER NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    turn_order INTEGER NOT NULL,
    direction TEXT NOT NULL,
    street TEXT NOT NULL
);

CREATE TABLE trace_waypoints (
    id SERIAL PRIMARY KEY,
    trace_id INTEGER NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    waypoint_order INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    pair_street_a TEXT,
    pair_street_b TEXT,
    candidates INTEGER
);

CREATE TABLE consensus_segments (
    id SERIAL PRIMARY KEY,
    centre_id TEXT NOT NULL REFERENCES centres(id),
    street_a TEXT NOT NULL,
    street_b TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    authors INTEGER NOT NULL,
    weight NUMERIC NOT NULL,
    video_count INTEGER DEFAULT 0,
    text_count INTEGER DEFAULT 0,
    last_seen DATE,
    junction_type TEXT,
    node_id TEXT,
    UNIQUE (centre_id, street_a, street_b)
);

CREATE TABLE route_lines (
    id SERIAL PRIMARY KEY,
    centre_id TEXT NOT NULL REFERENCES centres(id),
    family INTEGER,
    run INTEGER,
    trace_count INTEGER,
    authors INTEGER,
    distance_m INTEGER,
    geometry JSONB NOT NULL,
    test_class TEXT,
    mixed_classes BOOLEAN NOT NULL DEFAULT false,
    below_threshold BOOLEAN NOT NULL DEFAULT false,
    predicted BOOLEAN NOT NULL DEFAULT false,
    source TEXT NOT NULL DEFAULT 'consensus_geometry'
);

CREATE TABLE route_line_segments (
    id SERIAL PRIMARY KEY,
    route_line_id INTEGER NOT NULL REFERENCES route_lines(id) ON DELETE CASCADE,
    segment_order INTEGER NOT NULL,
    street_a TEXT NOT NULL,
    street_b TEXT NOT NULL,
    authors INTEGER,
    video_count INTEGER DEFAULT 0,
    text_count INTEGER DEFAULT 0,
    weight NUMERIC,
    junction_type TEXT,
    last_seen DATE
);

CREATE TABLE route_line_steps (
    id SERIAL PRIMARY KEY,
    route_line_id INTEGER NOT NULL REFERENCES route_lines(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    instruction TEXT NOT NULL,
    distance_m INTEGER,
    duration_s INTEGER,
    traffic_control TEXT,
    speed_limit TEXT
);

CREATE INDEX idx_traces_centre ON traces(centre_id);
CREATE INDEX idx_segments_centre ON consensus_segments(centre_id);
CREATE INDEX idx_routes_centre ON route_lines(centre_id);

-- User-reported errors on a specific turn-by-turn instruction row. Feeds
-- re-verification; does not itself change route_lines/route_line_steps.
CREATE TABLE IF NOT EXISTS instruction_reports (
    id SERIAL PRIMARY KEY,
    route_line_id INTEGER NOT NULL REFERENCES route_lines(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_reports_route ON instruction_reports(route_line_id);

-- Community-submitted turn-by-turn routes: a new low-trust tier, kept
-- structurally separate from traces/trace_turns. scripts/migrate_to_postgres.py
-- deletes and reinserts traces per centre from the pipeline's own JSON
-- files on every run -- writing live user submissions into that table
-- would make the next pipeline rebuild silently wipe them.
--
-- centre_id is nullable: a submission can name a DriveTest centre this
-- app has no data for yet (osm.db's street/junction data is Ontario-wide,
-- not scoped to the handful of centres already in the `centres` table, so
-- street validation works regardless). centre_name is always populated --
-- either copied from a matched real centre or typed freeform -- so a
-- submission is displayable without depending on a join that may not
-- resolve.
CREATE TABLE IF NOT EXISTS user_submissions (
    id SERIAL PRIMARY KEY,
    centre_id TEXT REFERENCES centres(id),
    centre_name TEXT NOT NULL,
    test_class TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    -- Set by scripts/promote_submissions.py, never by the API. 'pending'
    -- until enough independent submissions corroborate the same route;
    -- 'promoted' once a route_lines row exists for it. Recomputed fresh
    -- each pipeline run (not a one-way ratchet) -- see that script's
    -- docstring for why.
    status TEXT NOT NULL DEFAULT 'pending',
    promoted_route_line_id INTEGER REFERENCES route_lines(id) ON DELETE SET NULL
);

-- junction_type is the user's own recollection of what's at that point
-- (traffic light, stop sign, roundabout, median, fork, filter lane,
-- plain intersection) -- descriptive, not fact-checked against osm.db
-- (there's no reliable source to verify it against), same low-trust
-- spirit as the rest of a submission.
CREATE TABLE IF NOT EXISTS user_submission_turns (
    id SERIAL PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES user_submissions(id) ON DELETE CASCADE,
    turn_order INTEGER NOT NULL,
    street TEXT NOT NULL,
    junction_type TEXT
);

CREATE INDEX idx_user_submissions_centre ON user_submissions(centre_id);

-- Forum -- "where I messed up". Every post resolves to a real junction
-- (street_a x street_b, same Graph.junction() adjacency check as route
-- submissions), so it has a real lat/lon like every other point feature
-- in this app -- not a schematic guess. centre_id/centre_name follow
-- the same nullable-FK + always-populated-name pattern as
-- user_submissions, for the same reason (any Ontario centre, including
-- ones not tracked here yet).
--
-- Moderation is fully automated, no volunteer/manual review (owner's
-- explicit standing constraint, see Notion Frontend Design backlog):
-- score-based auto-hide and report-count auto-hide are computed at
-- query time in main.py (WHERE net score > threshold AND reports <
-- threshold) rather than a stored/cached flag, so hidden posts can
-- never drift out of sync with their live vote/report counts.
CREATE TABLE IF NOT EXISTS forum_posts (
    id SERIAL PRIMARY KEY,
    centre_id TEXT REFERENCES centres(id),
    centre_name TEXT NOT NULL,
    test_class TEXT NOT NULL,
    street_a TEXT NOT NULL,
    street_b TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    maneuver_type TEXT NOT NULL,
    outcome TEXT,
    note TEXT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS forum_votes (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES forum_posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    value SMALLINT NOT NULL CHECK (value IN (-1, 1)),
    UNIQUE (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS forum_reports (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES forum_posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    UNIQUE (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS forum_comments (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES forum_posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Stored as bytea, not an external object-store URL -- this app has no
-- existing file-storage integration (no S3/R2 credentials anywhere in
-- the codebase), and adding one is a bigger infra decision than "let
-- people attach a photo to a post." Fine at this scale (one small image
-- per post/comment, no gallery); revisit if upload volume ever makes
-- Postgres row size a real problem. content_type is checked against an
-- image-only whitelist server-side, not trusted from the client.
--
-- post_id/comment_id are both nullable, exactly one set -- an image
-- belongs to either a post or a comment, never both, never neither.
-- One table for both rather than a second near-duplicate one for
-- comment images.
CREATE TABLE IF NOT EXISTS forum_images (
    id SERIAL PRIMARY KEY,
    post_id INTEGER REFERENCES forum_posts(id) ON DELETE CASCADE,
    comment_id INTEGER REFERENCES forum_comments(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL,
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT forum_images_post_xor_comment
        CHECK ((post_id IS NOT NULL) <> (comment_id IS NOT NULL))
);

CREATE INDEX idx_forum_posts_centre ON forum_posts(centre_id);
CREATE INDEX idx_forum_votes_post ON forum_votes(post_id);
CREATE INDEX idx_forum_reports_post ON forum_reports(post_id);
CREATE INDEX idx_forum_comments_post ON forum_comments(post_id);
CREATE INDEX idx_forum_images_post ON forum_images(post_id);
CREATE INDEX idx_forum_images_comment ON forum_images(comment_id);

-- Discussion board -- a general Q&A/comment section, deliberately
-- separate from forum_posts ("where I messed up"). Not anchored to a
-- real junction (no street_a/street_b/lat/lon, no Graph.junction()
-- check) -- centre_id/centre_name are both nullable, since a question
-- doesn't have to be about any specific centre at all. Same
-- vote/report/comment shape and the same query-time score/report
-- auto-hide moderation as the forum (see discussions.py), just no
-- location requirement.
CREATE TABLE IF NOT EXISTS discussion_posts (
    id SERIAL PRIMARY KEY,
    centre_id TEXT REFERENCES centres(id),
    centre_name TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    -- Additive (2026-09-21 Reddit-style redesign): all nullable, existing
    -- rows unaffected. test_type/post_type are real feed filters, not
    -- decoration; route_line_id links a post to a REAL route_lines row
    -- (never arbitrary text impersonating a route) so a click can open
    -- the actual route.
    test_type TEXT CHECK (test_type IN ('G', 'G2')),
    post_type TEXT CHECK (post_type IN ('question', 'experience', 'tip')),
    route_line_id INTEGER REFERENCES route_lines(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS discussion_votes (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES discussion_posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    value SMALLINT NOT NULL CHECK (value IN (-1, 1)),
    UNIQUE (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS discussion_reports (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES discussion_posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    UNIQUE (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS discussion_comments (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES discussion_posts(id) ON DELETE CASCADE,
    parent_comment_id INTEGER REFERENCES discussion_comments(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- One row per (comment, user) exactly like discussion_votes, just
-- scoped to comments instead of posts -- same net-score-at-query-time
-- pattern as everything else in this feature, no stored counter.
CREATE TABLE IF NOT EXISTS discussion_comment_votes (
    id SERIAL PRIMARY KEY,
    comment_id INTEGER NOT NULL REFERENCES discussion_comments(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    value SMALLINT NOT NULL CHECK (value IN (-1, 1)),
    UNIQUE (comment_id, user_id)
);

-- Same rationale as forum_images: bytea in Postgres, not an external
-- object store; post_id/comment_id both nullable, exactly one set.
CREATE TABLE IF NOT EXISTS discussion_images (
    id SERIAL PRIMARY KEY,
    post_id INTEGER REFERENCES discussion_posts(id) ON DELETE CASCADE,
    comment_id INTEGER REFERENCES discussion_comments(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL,
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT discussion_images_post_xor_comment
        CHECK ((post_id IS NOT NULL) <> (comment_id IS NOT NULL))
);

CREATE INDEX idx_discussion_posts_centre ON discussion_posts(centre_id);
CREATE INDEX idx_discussion_votes_post ON discussion_votes(post_id);
CREATE INDEX idx_discussion_reports_post ON discussion_reports(post_id);
CREATE INDEX idx_discussion_comments_post ON discussion_comments(post_id);
CREATE INDEX idx_discussion_comments_parent ON discussion_comments(parent_comment_id);
CREATE INDEX idx_discussion_comment_votes_comment ON discussion_comment_votes(comment_id);
CREATE INDEX idx_discussion_images_post ON discussion_images(post_id);
CREATE INDEX idx_discussion_images_comment ON discussion_images(comment_id);

-- In-app notifications only -- no email, no push, no external service
-- (this app has no email-sending code anywhere). A user checks a bell
-- icon; that's the whole delivery mechanism. post_id has no FK because
-- it points into forum_posts OR discussion_posts depending on `source`
-- -- no single table it could reference.
-- post_id/actor_user_id are nullable: a self-triggered 'booking_reminder'
-- notification (see booking_reminders below) has no source post and no
-- other user who caused it -- unlike 'mention'/'comment', which always
-- have both.
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),        -- recipient
    kind TEXT NOT NULL,                                    -- 'mention' | 'comment' | 'booking_reminder'
    source TEXT NOT NULL,                                  -- 'forum' | 'discussion' | 'booking_reminder'
    post_id INTEGER,
    comment_id INTEGER,
    actor_user_id INTEGER REFERENCES users(id),            -- who triggered it, if anyone
    excerpt TEXT,
    read BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Appointment-opening notifier (Hard tier), scoped down from real
-- scraping: drivetest.ca has no public availability data at all -- even
-- "sign in to see your bookings" requires a driver's licence number +
-- expiry first, there is a stated 10-logins/day cap, and it's a real
-- government service (Serco Canada Inc. on behalf of MTO) with its own
-- Terms of Use. Automating login as the user is something this
-- assistant will never do itself. This is the honest alternative: an
-- in-app reminder to go check drivetest.ca yourself, reusing the
-- existing notification bell -- not a real-time scraper.
CREATE TABLE IF NOT EXISTS booking_reminders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    centre_id TEXT REFERENCES centres(id),  -- nullable: a general reminder, not tied to one centre
    remind_at DATE NOT NULL,
    note TEXT,
    notified BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_booking_reminders_due ON booking_reminders(user_id, remind_at) WHERE NOT notified;

CREATE INDEX idx_notifications_user ON notifications(user_id, read, created_at DESC);

-- First-party, cookie-free page-view counter (2026-09-21). Deliberately
-- minimal: path + timestamp only, nothing else -- no IP, no user_id, no
-- session/user-agent. This is what keeps the "zero trackers" claim in
-- the Privacy Policy true: it's anonymous in fact, not just in name.
-- Coarse by design -- there's no per-visitor dedupe, since that would
-- require storing something identifying. Feeds the admin weekly digest.
CREATE TABLE IF NOT EXISTS page_views (
    id BIGSERIAL PRIMARY KEY,
    path TEXT NOT NULL,
    viewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_page_views_viewed_at ON page_views (viewed_at);
