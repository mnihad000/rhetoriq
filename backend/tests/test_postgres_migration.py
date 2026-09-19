from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from migrations.runner import run_migrations
from models.document import Document
from models.investigation import InvestigationPlan, InvestigationPlanTimeWindow, TimelineResult
from services.database import connect
from services.investigation_repository import InvestigationRepository
from services.postgres_corpus import (
    CorpusValidationError,
    PostgresCorpusStore,
    validate_embedding,
)
from services.research_repository import ResearchRepository
from services.trending_repository import TrendingRepository


@pytest.mark.integration
def test_postgres_migration_creates_all_repository_tables() -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")

    run_migrations.cache_clear()
    run_migrations(database_url)
    InvestigationRepository(database_url)
    ResearchRepository(database_url)
    TrendingRepository(database_url)
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(database_url) as checkpointer:
        checkpointer.setup()

    with connect(database_url) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    tables = {row["tablename"] for row in rows}
    assert {
        "schema_migrations",
        "investigations",
        "investigation_recent_summaries",
        "research_runs",
        "discovery_runs",
        "checkpoints",
    }.issubset(tables)


@pytest.mark.integration
def test_postgres_workspace_and_recent_summary_queries() -> None:
    database_url = _postgres_url()
    investigation_id = f"inv_p2_{uuid4().hex}"
    plan = InvestigationPlan(
        query_text="Trace the compact dashboard projection",
        topic="compact dashboard projection",
        canonical_phrase="compact dashboard projection",
        intent="origin",
        search_queries=["compact dashboard projection"],
        semantic_queries=["compact dashboard projection"],
        target_source_types=["national_news"],
        requested_outputs=["timeline"],
        time_window=InvestigationPlanTimeWindow(label="all_time"),
        retrieval_mode="broad",
        risk_notes=[],
        uncertainty_requirements=[],
    )
    repository = InvestigationRepository(database_url)
    try:
        repository.save_plan(investigation_id, plan.query_text, plan)
        repository.save_timeline_result(
            TimelineResult(
                investigation_id=investigation_id,
                plan_snapshot=plan,
                timeline_events=[],
                first_observed_doc_id=None,
                timeline_summary="PostgreSQL timeline fallback.",
                limitations=[],
                confidence_score=0.4,
                confidence_label="medium",
            )
        )

        workspace = repository.get_investigation_workspace(investigation_id)
        assert workspace is not None
        assert workspace.timeline is not None
        assert workspace.timeline.timeline_summary == "PostgreSQL timeline fallback."

        summary = next(
            item
            for item in repository.get_recent_investigations(limit=1000)
            if item.investigation_id == investigation_id
        )
        assert summary.report_title == "Compact Dashboard Projection Investigation"
        assert summary.report_summary == "PostgreSQL timeline fallback."
    finally:
        with connect(database_url) as connection:
            connection.execute(
                "DELETE FROM investigation_recent_summaries WHERE investigation_id = ?",
                (investigation_id,),
            )
            connection.execute(
                "DELETE FROM timeline_results WHERE investigation_id = ?",
                (investigation_id,),
            )
            connection.execute(
                "DELETE FROM investigations WHERE investigation_id = ?",
                (investigation_id,),
            )


def _postgres_url() -> str:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    run_migrations.cache_clear()
    run_migrations(database_url)
    return database_url


class _FakeEmbeddings:
    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    dimension = 384

    def embed_document(self, document: Document) -> list[float]:
        vector = [0.0] * 384
        vector[0 if "alpha" in document.title.lower() else 1] = 1.0
        return vector

    def embed_query(self, _query: str) -> list[float]:
        vector = [0.0] * 384
        vector[0] = 1.0
        return vector


def _document(document_id: str, title: str, *, citable: bool = False) -> Document:
    return Document(
        id=document_id,
        source_name="example.test",
        source_type="national_news",
        url=f"https://example.test/{document_id}",
        title=title,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        collected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        text=title,
        snippet=title,
        entities=[],
        phrases=[],
        metadata={"acquisition_receipt_valid": citable} if citable else {},
    )


@pytest.mark.integration
def test_semantic_corpus_migration_is_repeatable_and_additive() -> None:
    database_url = _postgres_url()
    run_migrations.cache_clear()
    run_migrations(database_url)
    run_migrations.cache_clear()
    run_migrations(database_url)
    with connect(database_url) as connection:
        table = connection.execute(
            "SELECT to_regclass('public.semantic_document_corpus') AS table_name"
        ).fetchone()
        extension = connection.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    assert table["table_name"] == "semantic_document_corpus"
    assert extension is not None


@pytest.mark.integration
def test_semantic_corpus_deduplicates_ids_and_preserves_citable_canonical() -> None:
    database_url = _postgres_url()
    document_id = f"b1_dedup_{uuid4().hex}"
    store = PostgresCorpusStore(database_url, embedding_service=_FakeEmbeddings())
    lead = _document(document_id, "Alpha discovery lead", citable=True)
    canonical = _document(document_id, "Alpha canonical evidence", citable=True)
    store.upsert_document(lead, source_kind="discovery")
    with connect(database_url) as connection:
        discovery_row = connection.execute(
            "SELECT citable FROM semantic_document_corpus WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    assert discovery_row["citable"] is False
    store.upsert_document(canonical, source_kind="research")
    with connect(database_url) as connection:
        row = connection.execute(
            "SELECT document_id, source_kind, citable, document_json FROM semantic_document_corpus WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    assert row["document_id"] == document_id
    assert row["source_kind"] == "research"
    assert row["citable"] is True
    assert "canonical evidence" in row["document_json"]


@pytest.mark.integration
def test_semantic_corpus_validates_dimension_and_model() -> None:
    with pytest.raises(CorpusValidationError, match="dimension mismatch"):
        validate_embedding([0.0] * 3, model_name="sentence-transformers/all-MiniLM-L6-v2", expected_model="sentence-transformers/all-MiniLM-L6-v2")
    with pytest.raises(CorpusValidationError, match="model mismatch"):
        validate_embedding([0.0] * 384, model_name="other/model", expected_model="sentence-transformers/all-MiniLM-L6-v2")


@pytest.mark.integration
def test_semantic_search_orders_by_cosine_and_survives_new_store() -> None:
    database_url = _postgres_url()
    prefix = f"b1_order_{uuid4().hex}"
    store = PostgresCorpusStore(database_url, embedding_service=_FakeEmbeddings())
    store.upsert_document(_document(f"{prefix}_alpha", "Alpha evidence", citable=True), source_kind="research")
    store.upsert_document(_document(f"{prefix}_beta", "Beta lead"), source_kind="discovery")
    # Use the persisted rows through a fresh adapter to cover restart-style
    # process boundaries.
    results = PostgresCorpusStore(database_url, embedding_service=_FakeEmbeddings()).search(
        [1.0] + [0.0] * 383, limit=10
    )
    selected = [item for item in results if item.document.id.startswith(prefix)]
    assert [item.document.id for item in selected] == [f"{prefix}_alpha", f"{prefix}_beta"]
    assert selected[0].citable is True
    assert selected[1].citable is False


@pytest.mark.integration
def test_semantic_backfill_is_resumable_and_idempotent() -> None:
    database_url = _postgres_url()
    document_id = f"zz_b1_backfill_{uuid4().hex}"
    document = _document(document_id, "Alpha backfill source")
    with connect(database_url) as connection:
        connection.execute(
            "INSERT INTO research_documents (run_id, doc_id, document_json, created_at) VALUES (?, ?, ?, ?)",
            (f"b1_run_{uuid4().hex}", document_id, document.model_dump_json(), datetime.now(timezone.utc).isoformat()),
        )
    store = PostgresCorpusStore(database_url, embedding_service=_FakeEmbeddings())
    first = store.backfill(batch_size=10000, after_id=None)
    assert first.processed >= 1
    assert first.next_after_id is not None
    second = store.backfill(batch_size=10000, after_id=first.next_after_id)
    assert second.processed == 0
    with connect(database_url) as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM semantic_document_corpus WHERE document_id = ?",
            (document_id,),
        ).fetchone()["n"]
    assert count == 1


@pytest.mark.integration
def test_backfill_boundary_duplicate_prefers_receipted_source() -> None:
    database_url = _postgres_url()
    document_id = f"z_b1_duplicate_{uuid4().hex}"
    lead = _document(document_id, "Alpha discovery lead", citable=True)
    evidence = _document(document_id, "Alpha receipted evidence", citable=True)
    with connect(database_url) as connection:
        connection.execute(
            """INSERT INTO discovery_documents
               (doc_id, canonical_url, domain, document_json, providers_json,
                search_queries_json, first_seen_at, latest_seen_at, first_run_id,
                latest_run_id, seen_run_ids_json)
               VALUES (?, ?, ?, ?, '[]', '[]', ?, ?, ?, ?, '[]')""",
            (document_id, lead.url, "example.test", lead.model_dump_json(),
             "2026-01-01", "2026-01-01", "discovery-run", "discovery-run"),
        )
        connection.execute(
            "INSERT INTO retrieved_documents (investigation_id, doc_id, document_json, created_at) VALUES (?, ?, ?, ?)",
            (f"investigation_{uuid4().hex}", document_id, evidence.model_dump_json(), "2026-01-01"),
        )
    store = PostgresCorpusStore(database_url, embedding_service=_FakeEmbeddings())
    result = store.backfill(batch_size=1, after_id=document_id[:-1])
    assert result.processed == 1
    with connect(database_url) as connection:
        row = connection.execute(
            "SELECT source_kind, citable, document_json FROM semantic_document_corpus WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    assert row["source_kind"] == "retrieved"
    assert row["citable"] is True
    assert "receipted evidence" in row["document_json"]


@pytest.mark.integration
def test_postgres_replay_comparison_upsert() -> None:
    database_url = _postgres_url()
    repository = ResearchRepository(database_url)
    replay_id = f"b1_replay_{uuid4().hex}"
    repository.record_replay_comparison("source", replay_id, {"result": "first"})
    repository.record_replay_comparison("source", replay_id, {"result": "updated"})
    assert repository.get_replay_comparison(replay_id) == {
        "source_run_id": "source",
        "result": "updated",
    }
