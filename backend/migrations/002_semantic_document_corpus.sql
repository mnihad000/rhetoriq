-- Additive canonical corpus for Neon/PostgreSQL semantic retrieval.
-- Discovery records are retained as leads and never become citable merely
-- because they are present in this table.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS semantic_document_corpus (
    document_id TEXT PRIMARY KEY,
    document_json TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_provenance_json TEXT NOT NULL DEFAULT '{}',
    citable BOOLEAN NOT NULL DEFAULT FALSE,
    embedding vector(384),
    embedding_model TEXT,
    embedding_input_hash TEXT,
    embedding_status TEXT NOT NULL DEFAULT 'pending',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_semantic_document_corpus_citable
    ON semantic_document_corpus (citable);

CREATE INDEX IF NOT EXISTS idx_semantic_document_corpus_embedding_model
    ON semantic_document_corpus (embedding_model);
