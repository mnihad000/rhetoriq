-- Publication attempts from the former transport are not processing attempts.
-- Preserve them for audit and give unclaimed legacy jobs a fresh execution budget.
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS legacy_publish_attempts INTEGER NOT NULL DEFAULT 0;

UPDATE event_outbox
SET legacy_publish_attempts = attempts,
    attempts = 0,
    next_attempt_at = NULL,
    last_error = NULL
WHERE status = 'pending' AND lease_generation = 0;
