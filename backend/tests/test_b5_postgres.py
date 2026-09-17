"""Only the explicit local disposable integration database is allowed."""
from concurrent.futures import ThreadPoolExecutor
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from services.b5_repository import ProjectionRepository
from services.database import connect
from tests.test_b5_repository import doc


@pytest.fixture
def repository():
    target = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not target:
        pytest.skip("explicit disposable POSTGRES_TEST_DATABASE_URL absent")
    parsed = urlparse(target)
    if parsed.hostname not in {"localhost","127.0.0.1","::1","postgres"} or "test" not in parsed.path.lower():
        pytest.fail("B5 integration requires a local database whose name contains test")
    return ProjectionRepository(target)


@pytest.mark.integration
def test_postgres_duplicate_delivery_and_pending_minilm_corpus(repository):
    identity = "b5_test_"+uuid4().hex
    document = doc(identity)
    def acquire(_):
        return repository.record_document(document,source_event_id=identity+":acquire")
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(acquire,range(4)))
    assert len({snapshot["snapshot_id"] for snapshot in snapshots})==1
    with connect(repository.target) as db:
        row = db.execute("SELECT embedding_model,embedding_status,citable,embedding FROM semantic_document_corpus WHERE document_id=?",(identity,)).fetchone()
        assert row["embedding_model"]=="sentence-transformers/all-MiniLM-L6-v2"
        assert row["embedding_status"]=="pending" and row["citable"] and row["embedding"] is None
        assert db.execute("SELECT COUNT(*) AS n FROM b5_snapshots WHERE domain_id=?",(identity,)).fetchone()["n"]==1
    tombstone = repository.withdraw(identity,operation_id=identity+":remove",reason="integration")
    with connect(repository.target) as db:
        assert not db.execute("SELECT citable FROM semantic_document_corpus WHERE document_id=?",(identity,)).fetchone()["citable"]
    assert tombstone["revision"]==2 and not tombstone["eligible"]


@pytest.mark.integration
def test_postgres_outbox_failure_has_no_canonical_or_vector_side_effect(repository,monkeypatch):
    identity = "b5_test_"+uuid4().hex
    def fail(*args,**kwargs):
        raise RuntimeError("injected dispatch failure")
    monkeypatch.setattr(repository.events,"enqueue",fail)
    with pytest.raises(RuntimeError):
        repository.record_document(doc(identity),source_event_id=identity+":acquire")
    assert repository.current("document",identity) is None
    with connect(repository.target) as db:
        assert db.execute("SELECT document_id FROM semantic_document_corpus WHERE document_id=?",(identity,)).fetchone() is None
