CREATE TABLE IF NOT EXISTS event_outbox (
    event_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    schema_subject TEXT NOT NULL,
    event_json TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_outbox_pending
    ON event_outbox(status, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS idx_event_outbox_correlation
    ON event_outbox(correlation_id, created_at);

CREATE TABLE IF NOT EXISTS consumed_events (
    consumer_name TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    topic TEXT NOT NULL,
    partition_id INTEGER,
    offset_id BIGINT,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    last_error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (consumer_name, event_id)
);

CREATE INDEX IF NOT EXISTS idx_consumed_events_status
    ON consumed_events(consumer_name, status, started_at);

CREATE TABLE IF NOT EXISTS event_replay_jobs (
    replay_job_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    correlation_id TEXT,
    partition_id INTEGER,
    start_offset BIGINT,
    end_offset BIGINT,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    processed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
