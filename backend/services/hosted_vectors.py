"""Persist receipted Gemini vectors without mixing the MiniLM retrieval corpus."""
from __future__ import annotations

import math

from models.events import EnrichmentReceipt
from services.database import connect, is_postgres_database


def validate_hosted_vector(receipt: EnrichmentReceipt) -> list[float]:
    if receipt.embedding_model != "gemini-embedding-001":
        raise ValueError("Hosted corpus requires gemini-embedding-001 model identity")
    vector = receipt.embedding
    if len(vector) != 384 or not all(math.isfinite(value) for value in vector):
        raise ValueError("Hosted embedding requires 384 finite values")
    if not math.isclose(sum(value * value for value in vector), 1.0, abs_tol=1e-6):
        raise ValueError("Hosted embedding must be normalized before persistence")
    if receipt.failure_state is not None:
        raise ValueError("Failed enrichment cannot supply a hosted embedding")
    return vector


def persist_hosted_vector(target: str, document_id: str, receipt: EnrichmentReceipt) -> None:
    vector = validate_hosted_vector(receipt)
    if not is_postgres_database(target):
        # The isolated development projection stores the receipt as JSON.
        return
    from migrations.runner import run_migrations
    run_migrations(target)
    literal = "[" + ",".join(str(value) for value in vector) + "]"
    with connect(target) as db:
        db.execute(
            """INSERT INTO hosted_document_embeddings
            (document_id, artifact_id, embedding_model, input_hash, output_hash, embedding)
            VALUES (?, ?, ?, ?, ?, ?::vector)
            ON CONFLICT(document_id, artifact_id) DO NOTHING""",
            (document_id, receipt.artifact_id, receipt.embedding_model,
             receipt.input_hash, receipt.output_hash, literal),
        )
        stored = db.execute(
            "SELECT embedding_model, input_hash, output_hash FROM hosted_document_embeddings "
            "WHERE document_id = ? AND artifact_id = ?", (document_id, receipt.artifact_id),
        ).fetchone()
        if (stored["embedding_model"], stored["input_hash"], stored["output_hash"]) != (
            receipt.embedding_model, receipt.input_hash, receipt.output_hash,
        ):
            raise ValueError("Hosted vector artifact identity is immutable")
