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
    geometry JSONB NOT NULL
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

CREATE INDEX idx_traces_centre ON traces(centre_id);
CREATE INDEX idx_segments_centre ON consensus_segments(centre_id);
CREATE INDEX idx_routes_centre ON route_lines(centre_id);
