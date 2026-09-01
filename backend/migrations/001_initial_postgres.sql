-- RhetoriQ's durable application state. JSON is kept as TEXT deliberately:
-- Pydantic owns the versioned artifact contracts and the repositories preserve
-- the same serialized boundary used by the completed SQLite MVP.

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id TEXT PRIMARY KEY, query_text TEXT NOT NULL, status TEXT NOT NULL,
    current_stage TEXT NOT NULL, plan_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_results (
    investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieved_documents (
    investigation_id TEXT NOT NULL, doc_id TEXT NOT NULL, document_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (investigation_id, doc_id)
);
CREATE TABLE IF NOT EXISTS retrieval_rounds (
    investigation_id TEXT NOT NULL, round_number INTEGER NOT NULL, round_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (investigation_id, round_number)
);
CREATE TABLE IF NOT EXISTS duplicate_candidates (
    investigation_id TEXT NOT NULL, left_doc_id TEXT NOT NULL, right_doc_id TEXT NOT NULL, duplicate_json TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (investigation_id, left_doc_id, right_doc_id)
);
CREATE TABLE IF NOT EXISTS search_results (
    investigation_id TEXT NOT NULL, round_number INTEGER NOT NULL, url TEXT NOT NULL, result_json TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (investigation_id, round_number, url)
);

CREATE TABLE IF NOT EXISTS timeline_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_diversity_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counter_narrative_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS narrative_family_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analyst_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gap_analysis_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skeptic_review_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_ledger_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gap_ledger_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provenance_trace_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_loop_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_counterpoint_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_verification_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_debate_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS final_report_results (investigation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL, parent_run_id TEXT, mode TEXT NOT NULL,
    plan_version TEXT NOT NULL DEFAULT 'a2-v1', status TEXT NOT NULL, active_node TEXT, active_action TEXT,
    limits_json TEXT NOT NULL, usage_json TEXT NOT NULL, terminal_decision TEXT, warnings_json TEXT NOT NULL,
    worker_id TEXT, lease_until TEXT, created_at TEXT NOT NULL, started_at TEXT, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_runs_investigation ON research_runs(investigation_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_research_run ON research_runs(investigation_id) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS research_events (
    run_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(run_id, sequence)
);
CREATE TABLE IF NOT EXISTS research_actions (
    action_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL, idempotency_key TEXT,
    decision_json TEXT NOT NULL, status TEXT NOT NULL, provider TEXT, result_count INTEGER NOT NULL DEFAULT 0,
    document_ids_json TEXT NOT NULL, receipt_ids_json TEXT NOT NULL, duration_ms INTEGER, failure_category TEXT,
    warning TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id, sequence)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_action_idempotency ON research_actions(run_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS research_receipts (receipt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_documents (run_id TEXT NOT NULL, doc_id TEXT NOT NULL, document_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id, doc_id));
CREATE TABLE IF NOT EXISTS research_candidates (run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id, candidate_id));
CREATE TABLE IF NOT EXISTS research_candidate_reports (run_id TEXT PRIMARY KEY, report_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_model_calls (call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_id TEXT, provider TEXT, model TEXT, prompt_version TEXT, prompt_hash TEXT, schema_name TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost DOUBLE PRECISION, duration_ms INTEGER, status TEXT, error TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_evaluations (run_id TEXT PRIMARY KEY, evaluation_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_replay_comparisons (replay_run_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL, comparison_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_workers (worker_id TEXT PRIMARY KEY, mode TEXT NOT NULL, active_run_id TEXT, last_seen_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS discovery_runs (
    run_id TEXT PRIMARY KEY, status TEXT NOT NULL, is_reseed INTEGER NOT NULL, started_at TEXT NOT NULL,
    completed_at TEXT, stats_json TEXT NOT NULL, warnings_json TEXT NOT NULL, error TEXT, queries_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_documents (
    doc_id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL, domain TEXT NOT NULL, document_json TEXT NOT NULL,
    providers_json TEXT NOT NULL, search_queries_json TEXT NOT NULL, first_seen_at TEXT NOT NULL,
    latest_seen_at TEXT NOT NULL, first_run_id TEXT NOT NULL, latest_run_id TEXT NOT NULL, seen_run_ids_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_document_runs (
    run_id TEXT NOT NULL, doc_id TEXT NOT NULL, provider TEXT NOT NULL, search_query TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (run_id, doc_id, provider, search_query)
);
CREATE TABLE IF NOT EXISTS published_trending_snapshots (
    snapshot_id TEXT PRIMARY KEY, state TEXT NOT NULL, generated_at TEXT NOT NULL, fresh_until TEXT NOT NULL,
    last_completed_run_at TEXT, last_reseed_at TEXT, warning TEXT, snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_clusters (
    snapshot_id TEXT NOT NULL, topic_id TEXT NOT NULL, canonical_phrase TEXT NOT NULL, topic_json TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (snapshot_id, topic_id)
);
