import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from demo_data import DEMO_DOCUMENTS
from flink.contracts import stable_id
from services.b5_graph import build_document_graph, build_investigation_graph, provenance_paths


def _document_snapshot(document, *, snapshot_id=None, domain_id="domain-1", revision=1, operation="upsert", eligible=True):
    return {
        "snapshot_id": snapshot_id or f"snap:{document.id}",
        "kind": "document",
        "domain_id": domain_id,
        "revision": revision,
        "semantic_hash": f"sha256:{document.id}:{revision}",
        "operation": operation,
        "eligible": eligible,
        "data": {
            "document": document.model_dump(mode="json"),
            "enrichment": None,
            "processing": {},
            "source_kind": "retrieved",
            "withdrawn": operation == "withdraw",
        },
    }


def test_document_graph_preserves_document_id_and_b4_relationships():
    anchor = DEMO_DOCUMENTS[0].text[:6]
    document = DEMO_DOCUMENTS[0].model_copy(update={"metadata": {"references": [{"target_url": "https://example.org/source", "anchor_text": anchor, "context": "see source", "start": 0, "end": 6, "extraction_version": "b4-v1", "reference_kind": "hyperlink"}]}})
    graph = build_document_graph(_document_snapshot(document))
    assert any(node["id"] == document.id and node["kind"] == "document" for node in graph["nodes"])
    assert stable_id("phrase", document.phrases[0].strip().lower(), length=16) in {node["id"] for node in graph["nodes"] if node["kind"] == "phrase"}
    links = [edge for edge in graph["edges"] if edge["relationship"] == "references"]
    assert links and links[0]["evidence_class"] == "observed"
    assert links[0]["evidence"]["reference_kind"] == "hyperlink"


def test_withdrawn_document_retains_tombstone_node_without_edges():
    snapshot = _document_snapshot(DEMO_DOCUMENTS[0], operation="withdraw", eligible=False)
    graph = build_document_graph(snapshot)
    assert [node["id"] for node in graph["nodes"]] == [DEMO_DOCUMENTS[0].id]
    assert graph["nodes"][0]["withdrawn"] is True
    assert graph["edges"] == []


def test_investigation_claim_ids_are_scoped_and_inferred_paths_are_opt_in():
    first = _document_snapshot(DEMO_DOCUMENTS[0], snapshot_id="doc-snap-1")
    second = _document_snapshot(DEMO_DOCUMENTS[1], snapshot_id="doc-snap-2")
    workspace = {
        "investigation_id": "inv-1",
        "query_text": "energy",
        "retrieved_documents": [DEMO_DOCUMENTS[0].model_dump(mode="json"), DEMO_DOCUMENTS[1].model_dump(mode="json")],
        "report": {"key_claims": [{"claim_id": "claim-1", "claim_text": "An observed claim", "claim_type": "observed_fact", "supporting_document_ids": [DEMO_DOCUMENTS[0].id]}]},
        "timeline": {"timeline_events": []},
    }
    snapshot = {"snapshot_id": "inv-snap", "kind": "investigation", "domain_id": "inv-1", "revision": 1, "semantic_hash": "sha256:inv", "operation": "upsert", "eligible": True, "data": {"workspace": workspace, "run_id": "run-1", "terminal_decision": "published", "document_snapshot_ids": ["doc-snap-1", "doc-snap-2"]}}
    graph = build_investigation_graph(snapshot, [first, second])
    assert "claim:inv-1:claim-1" in {node["id"] for node in graph["nodes"]}
    assert any(edge["relationship"] == "supported_by" and edge["evidence_class"] == "observed" for edge in graph["edges"])
    assert provenance_paths(graph, DEMO_DOCUMENTS[0].id, DEMO_DOCUMENTS[1].id) == []
