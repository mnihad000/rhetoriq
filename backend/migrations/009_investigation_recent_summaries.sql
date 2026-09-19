-- Compact dashboard projections. Artifact JSON remains authoritative; these
-- fields avoid loading full artifacts to render recent-investigation cards.
CREATE TABLE IF NOT EXISTS investigation_recent_summaries (
    investigation_id TEXT PRIMARY KEY,
    fallback_title TEXT NOT NULL,
    timeline_summary TEXT,
    analyst_summary TEXT,
    report_title TEXT,
    report_summary TEXT,
    source_count INTEGER NOT NULL DEFAULT 0,
    receipt_count INTEGER NOT NULL DEFAULT 0
);
