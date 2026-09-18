"""Offline repair/rebuild invariants; these are not live-store evidence."""
from copy import deepcopy

from events.b5_acceptance import build_seed_documents
from events.b5_ops import rebuild, reconcile
from services.b5_graph import build_document_graph, build_investigation_graph
from services.b5_repository import ProjectionRepository


class RecordedTarget:
    def __init__(self, kind):
        self.kind, self.records, self.payloads = kind, {}, {}

    def initialize(self, generation):
        self.records.setdefault(generation, {})

    def apply(self, snapshot, generation, document_snapshots=None):
        self.initialize(generation)
        key = snapshot["domain_id"] if snapshot["kind"] == "document" else "investigation:" + snapshot["domain_id"]
        self.records[generation][key] = {name: snapshot[name] for name in ("revision", "semantic_hash", "eligible")}
        graph = build_document_graph(snapshot) if snapshot["kind"] == "document" else build_investigation_graph(snapshot, document_snapshots or [])
        self.payloads[generation, key] = deepcopy(graph)
        return {"status": "applied"}

    repair = apply

    def inventory(self, generation):
        return deepcopy(self.records.get(generation, {}))

    def validate_snapshot(self, snapshot, generation, document_snapshots=None):
        key = snapshot["domain_id"] if snapshot["kind"] == "document" else "investigation:" + snapshot["domain_id"]
        expected = build_document_graph(snapshot) if snapshot["kind"] == "document" else build_investigation_graph(snapshot, document_snapshots or [])
        return [] if expected == self.payloads.get((generation, key)) else ["invalid_payload"]

    def remove_unexpected(self, domain_id, generation, kind="document"):
        key = domain_id if kind == "document" else "investigation:" + domain_id
        self.records[generation].pop(key, None)
        self.payloads.pop((generation, key), None)


def test_drift_detects_missing_stale_extra_invalid_and_repair_obeys_budget(tmp_path):
    repository = ProjectionRepository(str(tmp_path / "state.db"))
    documents = build_seed_documents("run", 3)
    for document in documents:
        repository.record_document(document, source_event_id=document.id)
    target = RecordedTarget("neo4j")
    targets = {"neo4j": target}
    rebuild(repository, targets, "shadow")
    del target.records["shadow"][documents[0].id]
    target.records["shadow"][documents[1].id]["revision"] = 0
    target.payloads["shadow", documents[2].id]["edges"] = []
    target.records["shadow"]["extra"] = {"revision": 1, "semantic_hash": "extra", "eligible": True}
    check = reconcile(repository, targets, "shadow")
    assert len(check["drift"]) == 4 and check["repaired"] == 0
    repaired = reconcile(repository, targets, "shadow", repair=True, repair_limit=2)
    assert repaired["repaired"] == 2
    assert len(reconcile(repository, targets, "shadow")["drift"]) == 2
    reconcile(repository, targets, "shadow", repair=True)
    assert reconcile(repository, targets, "shadow")["drift"] == []


def test_isolated_rebuild_uses_pinned_historical_content_and_resumes_mutations(tmp_path):
    repository = ProjectionRepository(str(tmp_path / "state.db"))
    document = build_seed_documents("run", 1)[0]
    first = repository.record_document(document, source_event_id="first")
    investigation = repository.record_investigation({"investigation_id": "inv", "retrieved_documents": [document.model_dump(mode="json")], "report": {"key_claims": []}}, run_id="run", terminal_decision="published", source_event_id="terminal")
    target = RecordedTarget("neo4j")
    rebuilt = rebuild(repository, {"neo4j": target}, "shadow-a")
    repository.withdraw(document.id, operation_id="withdraw", reason="concurrent rebuild mutation")
    rebuild(repository, {"neo4j": target}, "shadow-a", after=rebuilt["next_after"])
    rebuild(repository, {"neo4j": target}, "shadow-b")
    assert target.records["shadow-a"] == target.records["shadow-b"]
    assert target.payloads["shadow-a", "investigation:inv"] == target.payloads["shadow-b", "investigation:inv"]
    assert investigation["data"]["document_snapshot_ids"] == [first["snapshot_id"]]
    assert not target.records["shadow-a"][document.id]["eligible"]
    assert repository.manifest()["generation"] == "live"
    assert reconcile(repository, {"neo4j": target}, "shadow-a")["drift"] == []
