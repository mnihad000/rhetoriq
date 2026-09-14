from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from models.document import Document
from models.research import ResearchActionDecision
from services.postgres_corpus import (
    CorpusSearchResult,
    CorpusValidationError,
    embedding_input_hash,
    validate_embedding,
)
from services.research_tools import ResearchToolRegistry


def _document(document_id: str, *, receipt_valid: bool) -> Document:
    return Document(
        id=document_id,
        source_name="example.test",
        source_type="national_news",
        url=f"https://example.test/{document_id}",
        title="Alpha report",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        text="Alpha report text",
        snippet="Alpha report snippet",
        entities=[],
        phrases=[],
        metadata={"acquisition_receipt_valid": receipt_valid},
    )


def _registry() -> ResearchToolRegistry:
    registry = object.__new__(ResearchToolRegistry)
    registry.settings = SimpleNamespace(
        ENABLE_POSTGRES_VECTOR_SEARCH=True,
        DATABASE_URL="postgresql://test.invalid/corpus",
        EMBEDDING_DIMENSION=384,
        POSTGRES_VECTOR_SEARCH_TOP_K=8,
        RESEARCH_SEARCH_RESULTS_PER_ACTION=8,
    )
    registry._internal_search_warning = None
    return registry


def test_pgvector_result_eligibility_controls_evidence_partition(monkeypatch) -> None:
    class FakeEmbeddings:
        model_name = "sentence-transformers/all-MiniLM-L6-v2"

        def embed_query(self, _query: str) -> list[float]:
            return [1.0] + [0.0] * 383

    class FakeStore:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def search(self, *_args, **_kwargs) -> list[CorpusSearchResult]:
            return [
                CorpusSearchResult(_document("discovery", receipt_valid=True), 0.9, "discovery", False),
                CorpusSearchResult(_document("canonical", receipt_valid=False), 0.8, "research", True),
            ]

    monkeypatch.setattr("services.embedding_service.get_embedding_service", lambda: FakeEmbeddings())
    monkeypatch.setattr("services.postgres_corpus.PostgresCorpusStore", FakeStore)
    outcome = _registry().execute(
        ResearchActionDecision(
            action_type="internal_search",
            query="alpha report",
            action_summary="Search the persisted corpus",
            expected_evidence="Relevant source documents",
        ),
        SimpleNamespace(query_text="alpha report"),
        [],
    )

    assert [doc.id for doc in outcome.documents] == ["canonical"]
    assert [candidate.metadata["source_document_id"] for candidate in outcome.candidates] == ["discovery"]
    assert outcome.candidates[0].metadata["requires_canonical_revalidation"] is True
    assert outcome.documents[0].metadata["retrieval_provider"] == "neon_pgvector"


def test_embedding_outage_uses_visible_legacy_fallback(monkeypatch) -> None:
    class UnavailableEmbeddings:
        model_name = "sentence-transformers/all-MiniLM-L6-v2"

        def embed_query(self, _query: str) -> list[float]:
            return [0.0] * 384

    monkeypatch.setattr("services.embedding_service.get_embedding_service", lambda: UnavailableEmbeddings())
    monkeypatch.setattr("services.research_tools.get_merged_documents", lambda _seed: [_document("legacy", receipt_valid=False)])
    registry = _registry()
    documents = registry._internal_search("alpha report")
    assert [doc.id for doc in documents] == ["legacy"]
    assert "embedding unavailable" in registry._internal_search_warning


def test_embedding_input_hash_changes_with_search_text() -> None:
    first = _document("one", receipt_valid=False)
    changed = first.model_copy(update={"snippet": "A different lead"})
    assert embedding_input_hash(first) != embedding_input_hash(changed)


def test_embedding_shape_and_model_are_enforced() -> None:
    with pytest.raises(CorpusValidationError, match="dimension mismatch"):
        validate_embedding([1.0], model_name="minilm", expected_model="minilm")
    with pytest.raises(CorpusValidationError, match="model mismatch"):
        validate_embedding([0.0] * 384, model_name="other", expected_model="minilm")


def test_live_sync_defers_embedding_until_vector_search_is_enabled(monkeypatch) -> None:
    seen: list[bool] = []

    class FakeStore:
        def __init__(self, _database_url: str) -> None:
            pass

        def upsert_document(self, _document: Document, *, source_kind: str, embed: bool) -> str:
            assert source_kind == "research"
            seen.append(embed)
            return "pending" if not embed else "ready"

    monkeypatch.setattr("services.postgres_corpus.PostgresCorpusStore", FakeStore)
    monkeypatch.setattr(
        "config.get_settings",
        lambda: SimpleNamespace(ENABLE_POSTGRES_VECTOR_SEARCH=False),
    )
    from services.postgres_corpus import sync_document

    assert sync_document(
        "postgresql://test.invalid/corpus",
        _document("pending", receipt_valid=False),
        source_kind="research",
    ) == "pending"
    assert seen == [False]
