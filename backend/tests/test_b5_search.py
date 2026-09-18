from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.document import Document
from services.b5_search import SearchService


def test_literal_unicode_composition_preserves_original_offsets_without_stemming():
    text = "A cafe\u0301\t\npolicy and \u1100\u1161 records changed."
    spans = SearchService._spans(text, "CAFÉ policy")
    assert spans == [{"start": 2, "end": 15, "text": "cafe\u0301\t\npolicy"}]
    assert SearchService._spans(text, "가 records")[0]["text"] == "\u1100\u1161 records"
    assert SearchService._spans(text, "record change") == []


def _document(doc_id: str, text: str, *, lead: bool = False, published_at=None) -> Document:
    return Document(
        id=doc_id,
        source_id=f"source:{doc_id}",
        source_name=f"Source {doc_id}",
        source_type="national_news",
        url=f"https://example.test/{doc_id}",
        title=text,
        published_at=published_at,
        collected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        text=text,
        snippet=text,
        language="en",
        entities=[],
        phrases=[],
        metadata={"acquisition_receipt_valid": not lead, "source_kind": "discovery" if lead else "research"},
    )


def _snap(doc: Document, *, revision=1, digest=None, eligible=True, operation="upsert"):
    return {
        "snapshot_id": f"snap_{doc.id}_{revision}",
        "kind": "document",
        "domain_id": doc.id,
        "revision": revision,
        "semantic_hash": digest or f"hash_{doc.id}_{revision}",
        "operation": operation,
        "eligible": eligible,
        "data": {"document": doc.model_dump(mode="json"), "source_kind": "research", "withdrawn": not eligible},
    }


class FakeRepository:
    def __init__(self, snapshots, investigation_snapshot, workspace=None):
        self.snapshots = snapshots
        self.investigation_snapshot = investigation_snapshot
        self.workspace = workspace or {"investigation_id": "inv_1", "status": "report_completed", "retrieved_documents": []}
        self.semantic_store = None

    def current(self, kind, domain_id):
        if kind == "investigation":
            return self.investigation_snapshot
        for snapshot in self.snapshots:
            if snapshot.get("domain_id") == domain_id:
                return snapshot
        return None

    def documents(self, ids=None, limit=10000):
        if ids is None:
            return self.snapshots[:limit]
        return [item for item in self.snapshots if item["snapshot_id"] in ids or item["domain_id"] in ids][:limit]

    def manifest(self):
        return {"generation": "g1", "elasticsearch_index": "idx", "previous_generation": None}

    def get_investigation_workspace(self, investigation_id):
        return self.workspace if investigation_id == "inv_1" else None


def _service(*, elastic=None, repository=None, embedding=None):
    docs = [_document("d1", "The Hidden\tEnergy Tax is real."), _document("d2", "A different housing story.", lead=True)]
    inv = {
        "snapshot_id": "inv_snap",
        "kind": "investigation",
        "domain_id": "inv_1",
        "revision": 2,
        "semantic_hash": "inv_hash",
        "operation": "upsert",
        "eligible": True,
        "data": {"document_snapshot_ids": [f"snap_{doc.id}_1"] for doc in []},
    }
    inv["data"] = {"document_snapshot_ids": ["snap_d1_1", "snap_d2_1"], "workspace": {"investigation_id": "inv_1"}}
    repo = repository or FakeRepository([_snap(docs[0]), _snap(docs[1], eligible=False, operation="withdraw")], inv)
    if embedding is not None:
        class Semantic:
            def search(self, *args, **kwargs):
                return []
        repo.semantic_store = Semantic()
    return SearchService(repository=repo, elastic=elastic, embedding_service=embedding)


def test_search_withdrawal_and_default_evidence_filter_and_spans():
    service = _service()
    result = service.search('"hidden   energy tax"', investigation_id="inv_1")
    assert [row["document_id"] for row in result["results"]] == ["d1"]
    assert result["results"][0]["spans"] == [{"start": 4, "end": 21, "text": "Hidden\tEnergy Tax"}]
    assert result["results"][0]["citable"] is True
    assert result["complete"] is False  # target is absent, so canonical fallback is explicit
    assert result["fallback_active"] is True


def test_stale_target_result_is_rejected():
    class Elastic:
        def search(self, *args, **kwargs):
            return [{"document_id": "d1", "score": 100, "revision": 0, "semantic_hash": "old"}]

    result = _service(elastic=Elastic()).search("hidden", investigation_id="inv_1", mode="lexical")
    assert result["results"] == []
    assert result["fallback_active"] is True


def test_rrf_contributions_and_model_refusal():
    class Elastic:
        def search(self, *args, **kwargs):
            return [{"document_id": "d1", "score": 1, "revision": 1, "semantic_hash": "hash_d1_1"}]

    class Embedding:
        model_name = "gemini-embedding"

        def embed_query(self, query):
            return [1.0]

    service = _service(elastic=Elastic(), embedding=Embedding())
    with pytest.raises(ValueError, match="Embedding model mismatch"):
        service.search("hidden", investigation_id="inv_1")


def test_pending_workspace_is_distinct_from_healthy_empty():
    class PendingRepo(FakeRepository):
        def current(self, kind, domain_id):
            return None

    repo = PendingRepo([], None, {"investigation_id": "inv_1", "status": "running", "retrieved_documents": []})
    pending = SearchService(repository=repo).search("anything", investigation_id="inv_1")
    assert pending["pending"] is True
    assert pending["complete"] is False

    empty = SearchService(repository=FakeRepository([], {"data": {"document_snapshot_ids": []}}, {"investigation_id": "inv_1", "status": "report_completed", "retrieved_documents": []})).search("anything", investigation_id="inv_1")
    assert empty["pending"] is False
    assert empty["complete"] is True
    assert empty["source"] == "empty"


def test_include_leads_can_surface_canonical_ineligible_documents():
    docs = [_document("d2", "Housing lead only.", lead=True)]
    inv = {"snapshot_id": "inv_snap", "revision": 1, "data": {"document_snapshot_ids": ["snap_d2_1"]}}
    lead_snapshot = _snap(docs[0], eligible=False, operation="upsert")
    lead_snapshot["data"]["withdrawn"] = False
    repo = FakeRepository([lead_snapshot], inv)
    service = SearchService(repository=repo)
    result = service.search("housing", investigation_id="inv_1", filters={"include_leads": True})
    assert [item["document_id"] for item in result["results"]] == ["d2"]
    assert result["results"][0]["citable"] is False


def test_internal_search_adds_bounded_canonical_graph_context_and_filters_withdrawn():
    primary = _document("d1", "Alpha evidence")
    neighbor = _document("d3", "Related context")
    withdrawn = _document("d4", "Withdrawn alpha context")
    neighbor_snapshot = _snap(neighbor)
    withdrawn_snapshot = _snap(withdrawn, eligible=False, operation="withdraw")

    class Neo:
        def related_document_ids(self, document_ids, *, generation, limit):
            assert document_ids == ["d1"]
            assert limit == 20
            return ["d3", "d4"]

    repo = FakeRepository([_snap(primary), neighbor_snapshot, withdrawn_snapshot], None)
    service = SearchService(repository=repo, neo4j=Neo())
    results = service.internal_search("alpha", limit=2)
    assert [item.id for item in results] == ["d1", "d3"]
    assert results[1].metadata["retrieval_context_only"] is True
    assert results[1].metadata["retrieval_score"] == 0.0


def test_public_modes_are_preserved_and_internal_results_are_hydrated():
    docs = [_document("d1", "Alpha beta evidence")]
    snap = _snap(docs[0])
    inv = {"snapshot_id": "inv_snap", "revision": 1, "data": {"document_snapshot_ids": ["snap_d1_1"]}}
    service = SearchService(repository=FakeRepository([snap], inv))
    assert service.search("alpha", mode="fulltext", investigation_id="inv_1")["mode"] == "fulltext"
    results = service.internal_search("alpha", limit=1)
    assert results[0].metadata["retrieval_contributions"]
