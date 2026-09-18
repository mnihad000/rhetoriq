import os
import sys
from datetime import datetime, timezone
import pytest

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
    snapshot["data"]["projection_methods"] = {"graph": "unsupported"}
    with pytest.raises(ValueError, match="versions are unsupported"):
        build_document_graph(snapshot)


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


def test_recorded_links_resolve_in_scope_and_cycles_yield_simple_observed_paths():
    from events.b5_acceptance import build_seed_documents
    documents = build_seed_documents("run", 3)
    snapshots = [_document_snapshot(document, domain_id=document.id) for document in documents]
    investigation = {"snapshot_id": "investigation", "kind": "investigation", "domain_id": "inv",
                     "revision": 1, "semantic_hash": "hash", "operation": "upsert", "eligible": True,
                     "data": {"workspace": {"investigation_id": "inv"}, "run_id": "run", "terminal_decision": "published",
                              "document_snapshot_ids": [snapshot["snapshot_id"] for snapshot in snapshots]}}
    graph = build_investigation_graph(investigation, snapshots)
    paths = provenance_paths(graph, documents[0].id, documents[1].id)
    assert paths and all(step["relationship"] == "references" for path in paths for step in path["steps"])
    assert all(len(path["document_ids"]) == len(set(path["document_ids"])) for path in paths)
    assert all(not node.get("placeholder") for node in graph["nodes"] if node.get("kind") == "document")
    partial = build_investigation_graph(investigation, snapshots[:1])
    assert any(node.get("unresolved") for node in partial["nodes"] if node["kind"] == "reference_target")
    assert provenance_paths(partial, documents[0].id, documents[1].id) == []


def test_invalid_mentions_and_historical_reference_coverage_remain_visible():
    document = DEMO_DOCUMENTS[0].model_copy(update={"phrases": [], "entities": [], "metadata": {"reference_limitations": ["Historical HTML unavailable"]}})
    snapshot = _document_snapshot(document)
    snapshot["data"]["enrichment"] = {"artifact_id": "artifact", "input_hash": "input", "output_hash": "output", "provider": "recorded", "model": "recorded",
                                      "prompt_version": "v1", "schema_version": "v1", "embedding": [0.1] * 384,
                                      "canonical_phrases": [{"phrase": "invented", "surface_form": "invented", "evidence": {"surface_form": "invented", "start": 999999, "end": 1000007}}]}
    graph = build_document_graph(snapshot)
    assert not any(edge["relationship"] == "mentions_phrase" for edge in graph["edges"])
    assert "Historical HTML unavailable" in graph["limitations"]
    assert len(graph["limitations"]) > 1
