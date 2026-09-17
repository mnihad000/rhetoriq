-- B4 stream projections.  JSON is kept as text so the same migration can
-- initialize isolated SQLite test databases and PostgreSQL production stores.
CREATE TABLE IF NOT EXISTS processed_document_lineage (
    event_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    raw_event_id TEXT,
    semantic_output_hash TEXT,
    enrichment_receipt_json TEXT,
    document_json TEXT NOT NULL,
    event_json TEXT NOT NULL,
    occurred_at TEXT,
    projected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_processed_lineage_document
    ON processed_document_lineage(document_id, occurred_at);

CREATE TABLE IF NOT EXISTS signal_revisions (
    signal_id TEXT NOT NULL,
    signal_revision INTEGER NOT NULL,
    canonical_phrase_id TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    horizon TEXT,
    scoring_version TEXT,
    event_time_quality TEXT,
    baseline_statistics_json TEXT,
    artifact_lineage_json TEXT,
    payload_json TEXT NOT NULL,
    event_json TEXT NOT NULL,
    occurred_at TEXT,
    produced_at TEXT,
    projected_at TEXT NOT NULL,
    PRIMARY KEY(signal_id, signal_revision),
    UNIQUE(signal_id, horizon, signal_revision)
);
CREATE INDEX IF NOT EXISTS idx_signal_revisions_phrase
    ON signal_revisions(canonical_phrase_id, signal_revision);

CREATE TABLE IF NOT EXISTS signal_horizon_metrics (
    signal_id TEXT NOT NULL,
    signal_revision INTEGER NOT NULL,
    horizon TEXT NOT NULL,
    observed_count INTEGER NOT NULL DEFAULT 0,
    baseline_count REAL NOT NULL DEFAULT 0,
    spike REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    source_diversity INTEGER NOT NULL DEFAULT 0,
    publisher_diversity INTEGER NOT NULL DEFAULT 0,
    observed_window_json TEXT,
    baseline_window_json TEXT,
    PRIMARY KEY(signal_id, signal_revision, horizon),
    FOREIGN KEY(signal_id, signal_revision)
        REFERENCES signal_revisions(signal_id, signal_revision)
);

CREATE TABLE IF NOT EXISTS signal_supporting_documents (
    signal_id TEXT NOT NULL,
    signal_revision INTEGER NOT NULL,
    document_id TEXT NOT NULL,
    PRIMARY KEY(signal_id, signal_revision, document_id),
    FOREIGN KEY(signal_id, signal_revision)
        REFERENCES signal_revisions(signal_id, signal_revision)
);

CREATE TABLE IF NOT EXISTS signal_limitations (
    signal_id TEXT NOT NULL,
    signal_revision INTEGER NOT NULL,
    limitation TEXT NOT NULL,
    PRIMARY KEY(signal_id, signal_revision, limitation),
    FOREIGN KEY(signal_id, signal_revision)
        REFERENCES signal_revisions(signal_id, signal_revision)
);

CREATE TABLE IF NOT EXISTS evaluation_heartbeats (
    heartbeat_id TEXT PRIMARY KEY,
    evaluated_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluation_heartbeats_evaluated
    ON evaluation_heartbeats(evaluated_at DESC);

CREATE TABLE IF NOT EXISTS late_document_audit (
    event_id TEXT PRIMARY KEY,
    document_id TEXT,
    reason TEXT,
    event_json TEXT NOT NULL,
    occurred_at TEXT,
    recorded_at TEXT NOT NULL
);
