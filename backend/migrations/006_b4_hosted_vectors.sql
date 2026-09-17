-- Gemini and MiniLM occupy different vector spaces despite equal dimensions.
-- Keep hosted artifact vectors separate from the existing retrieval corpus.
CREATE TABLE IF NOT EXISTS hosted_document_embeddings (
    document_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    embedding_model TEXT NOT NULL CHECK (embedding_model = 'gemini-embedding-001'),
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    embedding vector(384) NOT NULL,
    PRIMARY KEY (document_id, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_hosted_document_embeddings_model
    ON hosted_document_embeddings(embedding_model);
