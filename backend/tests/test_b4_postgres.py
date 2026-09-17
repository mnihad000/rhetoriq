"""B4 PostgreSQL gate. Requires an explicit disposable test database."""
import os
from uuid import uuid4

import pytest

from migrations.runner import run_migrations
from models.events import EnrichmentReceipt
from services.hosted_vectors import persist_hosted_vector


@pytest.mark.integration
def test_hosted_vectors_persist_in_separate_pgvector_space():
    target = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not target:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    import psycopg
    run_migrations(target)
    document_id = f"test_b4_{uuid4().hex}"
    receipt = EnrichmentReceipt(
        artifact_id=f"artifact_{uuid4().hex}", input_hash="test-input", output_hash="test-output",
        provider="gemini", model="gemini-2.5-flash", prompt_version="b4-v1",
        schema_version="enrichment.v1", embedding=[1.0] + [0.0] * 383,
    )
    try:
        persist_hosted_vector(target, document_id, receipt)
        persist_hosted_vector(target, document_id, receipt)
        with psycopg.connect(target) as db:
            row = db.execute(
                "SELECT embedding_model, vector_dims(embedding), count(*) OVER () "
                "FROM hosted_document_embeddings WHERE document_id = %s", (document_id,),
            ).fetchone()
            assert row == ("gemini-embedding-001", 384, 1)
            assert db.execute("SELECT count(*) FROM semantic_document_corpus WHERE document_id = %s",
                              (document_id,)).fetchone()[0] == 0
    finally:
        with psycopg.connect(target) as db:
            db.execute("DELETE FROM hosted_document_embeddings WHERE document_id = %s", (document_id,))


@pytest.mark.integration
def test_postgres_artifact_replay_projection_and_atomic_budget():
    target = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not target:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    from concurrent.futures import ThreadPoolExecutor
    from services.enrichment_artifacts import BudgetExceeded, EnrichmentBudgetLedger
    from services.signal_repository import SignalRepository
    from tests.test_b4_e2e_fixture import NOW, raw_fixture, recorded_service
    from services.document_normalization import normalize_raw_event
    import psycopg

    budget_name = f"b4_test_{uuid4().hex}"
    request = normalize_raw_event(raw_fixture(uuid4().int % 1_000_000, NOW))
    service = recorded_service(target)
    repository = SignalRepository(target)
    ledger = EnrichmentBudgetLedger(target)

    def reserve(_index):
        try:
            reservation = ledger.reserve(budget_name=budget_name, request_limit=3, spend_limit_micros=30,
                                         spend_micros=10, now=NOW)
            reservation.settle()
            return True
        except BudgetExceeded:
            return False

    artifact_id = None
    try:
        enriched = service.enrich(request)
        artifact_id = enriched.payload.enrichment.artifact_id
        assert service.enrich(request).payload.enrichment == enriched.payload.enrichment
        with ThreadPoolExecutor(max_workers=8) as executor:
            assert sum(executor.map(reserve, range(8))) == 3
        from flink.stream_engine import StreamIntelligenceEngine
        processed = StreamIntelligenceEngine().process(enriched).processed[0]
        assert repository.project_processed(processed)
        assert not repository.project_processed(processed)
        with psycopg.connect(target) as db:
            assert db.execute("SELECT used_requests, used_spend_micros, reserved_requests FROM enrichment_budget_windows WHERE budget_name=%s",
                              (budget_name,)).fetchone() == (3, 30, 0)
    finally:
        with psycopg.connect(target) as db:
            db.execute("DELETE FROM enrichment_budget_windows WHERE budget_name=%s", (budget_name,))
            db.execute("DELETE FROM processed_document_lineage WHERE document_id=%s", (request.payload.document.id,))
            if artifact_id:
                db.execute("DELETE FROM enrichment_artifacts WHERE artifact_id=%s", (artifact_id,))
