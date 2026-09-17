"""Recovery around the external-write/durable-ledger boundary."""
from types import SimpleNamespace

import pytest

from events.b5_worker import ProjectionHandlers
from services.b5_repository import ProjectionRepository, MINILM_IDENTITY
from services.postgres_corpus import embedding_input_hash
from tests.test_b5_repository import doc


class Target:
    def __init__(self):
        self.writes = {}
        self.calls = 0

    def initialize(self, generation):
        return {"status": "ready"}

    def apply(self, snapshot, generation, **kwargs):
        self.calls += 1
        self.writes[(generation,snapshot["domain_id"])] = snapshot["semantic_hash"]
        return {"status": "applied"}


def test_crash_after_target_write_retries_before_completion(tmp_path,monkeypatch):
    repository = ProjectionRepository(str(tmp_path/"recovery.db"))
    snapshot = repository.record_document(doc(),source_event_id="acquire")
    adapter = Target()
    worker = ProjectionHandlers("elasticsearch",settings=SimpleNamespace(),repository=repository,adapter=adapter)
    original = repository.mark_delivery
    fail_once = [True]
    def crash(target,snapshot,generation,error=None):
        if error is None and fail_once and fail_once.pop():
            raise RuntimeError("injected crash after external write")
        return original(target,snapshot,generation,error=error)
    monkeypatch.setattr(repository,"mark_delivery",crash)
    with pytest.raises(RuntimeError):
        worker.apply(snapshot)
    assert len(adapter.writes)==1
    assert repository.delivery("elasticsearch","document","doc-1")["status"]=="failed"
    assert worker.apply(snapshot)
    assert not worker.apply(snapshot)
    assert len(adapter.writes)==1 and adapter.calls==2


def test_incomplete_target_write_never_completes_delivery(tmp_path):
    repository = ProjectionRepository(str(tmp_path/"partial.db"))
    snapshot = repository.record_document(doc(),source_event_id="acquire")
    adapter = Target()
    adapter.apply = lambda *args,**kwargs: {"status":"partial"}
    worker = ProjectionHandlers("elasticsearch",settings=SimpleNamespace(),repository=repository,adapter=adapter)
    with pytest.raises(RuntimeError):
        worker.apply(snapshot)
    assert repository.delivery("elasticsearch","document","doc-1")["status"]=="failed"


def test_recorded_semantic_replay_requires_pinned_artifact(tmp_path):
    repository = ProjectionRepository(str(tmp_path/"vectors.db"))
    snapshot = repository.record_document(doc(),source_event_id="acquire")
    worker = ProjectionHandlers("minilm",settings=SimpleNamespace(),repository=repository,recorded_only=True)
    with pytest.raises(ValueError,match="recorded MiniLM"):
        worker.apply(snapshot)
    repository.save_embedding(MINILM_IDENTITY,embedding_input_hash(doc()),[1.0]+[0.0]*383)
    assert worker.apply(snapshot)
    assert worker.embedding_service is None
    tombstone = repository.withdraw("doc-1",operation_id="remove",reason="provider withdrawal")
    assert worker.apply(tombstone)
