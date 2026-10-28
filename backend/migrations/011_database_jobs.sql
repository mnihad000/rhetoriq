-- Database-backed dispatch leases for the deployment branch.
-- Existing Kafka outbox publications are re-queued exactly once; completed
-- consumer-ledger entries keep their handlers idempotent during the replay.
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS lease_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS lease_until TEXT;
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS finished_at TEXT;

UPDATE event_outbox
SET status = 'pending', published_at = NULL
WHERE status = 'published';

CREATE INDEX IF NOT EXISTS idx_event_outbox_jobs
    ON event_outbox(status, next_attempt_at, lease_until, created_at);
