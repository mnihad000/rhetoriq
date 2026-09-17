-- B4 hosted enrichment artifacts. The identity key is immutable and includes
-- every configuration value that can change semantic output.
CREATE TABLE IF NOT EXISTS enrichment_artifacts (
    artifact_key TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    embedding_model TEXT NOT NULL CHECK (embedding_model = 'gemini-embedding-001'),
    output_hash TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    failure_state TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enrichment_artifacts_input
    ON enrichment_artifacts(input_hash);

CREATE TABLE IF NOT EXISTS enrichment_budget_windows (
    budget_name TEXT NOT NULL,
    window_start TEXT NOT NULL,
    request_limit INTEGER NOT NULL,
    spend_limit_micros BIGINT NOT NULL,
    reserved_requests INTEGER NOT NULL DEFAULT 0,
    reserved_spend_micros BIGINT NOT NULL DEFAULT 0,
    used_requests INTEGER NOT NULL DEFAULT 0,
    used_spend_micros BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (budget_name, window_start)
);

CREATE TABLE IF NOT EXISTS enrichment_worker_health (
    worker_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    budget_paused INTEGER NOT NULL DEFAULT 0,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    detail TEXT
);
