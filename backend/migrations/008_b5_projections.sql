-- Immutable rebuild inputs; external indexes are never authoritative.
CREATE TABLE IF NOT EXISTS b5_clock (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), sequence_id BIGINT NOT NULL);
INSERT INTO b5_clock(singleton, sequence_id) VALUES(1, 0) ON CONFLICT(singleton) DO NOTHING;
CREATE TABLE IF NOT EXISTS b5_snapshots (
    snapshot_id TEXT PRIMARY KEY, kind TEXT NOT NULL, domain_id TEXT NOT NULL,
    revision BIGINT NOT NULL, sequence_id BIGINT NOT NULL UNIQUE,
    semantic_hash TEXT NOT NULL, source_event_id TEXT NOT NULL,
    operation TEXT NOT NULL, eligible INTEGER NOT NULL, data_json TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(kind, domain_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_b5_snapshots_domain ON b5_snapshots(kind, domain_id, revision);
CREATE TABLE IF NOT EXISTS b5_current (
    kind TEXT NOT NULL, domain_id TEXT NOT NULL, snapshot_id TEXT NOT NULL REFERENCES b5_snapshots(snapshot_id),
    PRIMARY KEY(kind, domain_id)
);
CREATE TABLE IF NOT EXISTS b5_source_operations (
    kind TEXT NOT NULL, domain_id TEXT NOT NULL, source_event_id TEXT NOT NULL,
    input_hash TEXT NOT NULL, snapshot_id TEXT NOT NULL REFERENCES b5_snapshots(snapshot_id),
    PRIMARY KEY(kind, domain_id, source_event_id)
);
CREATE TABLE IF NOT EXISTS b5_deliveries (
    target TEXT NOT NULL, generation TEXT NOT NULL, kind TEXT NOT NULL, domain_id TEXT NOT NULL,
    revision BIGINT NOT NULL, sequence_id BIGINT NOT NULL, semantic_hash TEXT NOT NULL,
    eligible INTEGER NOT NULL, status TEXT NOT NULL, applied_at TEXT, last_error TEXT,
    PRIMARY KEY(target, generation, kind, domain_id)
);
CREATE TABLE IF NOT EXISTS b5_embedding_artifacts (
    model TEXT NOT NULL, input_hash TEXT NOT NULL, embedding_json TEXT NOT NULL,
    output_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(model, input_hash)
);
CREATE TABLE IF NOT EXISTS b5_generations (
    generation TEXT PRIMARY KEY, status TEXT NOT NULL, high_watermark BIGINT NOT NULL,
    created_at TEXT NOT NULL, validated_at TEXT, validation_json TEXT
);
CREATE TABLE IF NOT EXISTS b5_manifest (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1), manifest_json TEXT NOT NULL
);
INSERT INTO b5_manifest(singleton, manifest_json)
VALUES(1, '{"generation":"live","elasticsearch_index":"rhetoriq-documents-b5-live","previous_generation":null}')
ON CONFLICT(singleton) DO NOTHING;
INSERT INTO b5_generations(generation,status,high_watermark,created_at)
VALUES('live','active',0,'bootstrap') ON CONFLICT(generation) DO NOTHING;
CREATE TABLE IF NOT EXISTS b5_operator_audit (
    operation_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, action TEXT NOT NULL,
    reason TEXT NOT NULL, snapshot_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS b5_worker_health (
    worker_id TEXT PRIMARY KEY, target TEXT NOT NULL, state TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL, detail TEXT
);
