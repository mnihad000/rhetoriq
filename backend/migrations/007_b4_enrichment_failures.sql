-- Failed attempts are audited separately from immutable successful artifacts.
CREATE TABLE IF NOT EXISTS enrichment_artifact_failures (
    failure_id TEXT PRIMARY KEY,
    request_event_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    failure_state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    failed_at TEXT NOT NULL
);
