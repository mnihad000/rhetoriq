CREATE TABLE IF NOT EXISTS runtime_worker_health (
    worker_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    state TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_runtime_worker_health_role
    ON runtime_worker_health(role, heartbeat_at);
