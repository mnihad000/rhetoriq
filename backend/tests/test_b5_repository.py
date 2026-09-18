from datetime import datetime, timedelta, timezone
import json

import pytest

from models.document import Document
from models.events import (CORPUS_PROJECTIONS_TOPIC, ProcessedDocumentEvent, ProcessedDocumentPayload,
                           ProcessingReceipt, validate_event)
from services.b5_repository import ProjectionRepository
from services.database import connect
from services.event_store import EventStore
from services.event_handlers import EventHandlers
from services.research_repository import ResearchRepository


def doc(id="doc-1", *, citable=True, day=1, text="Public records changed today."):
    return Document(id=id,source_id="domain:example.gov",source_name="example.gov",source_type="government_record",
                    url=f"https://example.gov/{id}",title="Public records",text=text,entities=[],phrases=[],
                    collected_at=datetime(2026,9,day,tzinfo=timezone.utc),published_at=None,
                    metadata={"acquisition_receipt_valid":citable})


def test_atomic_snapshot_dispatch_and_redelivery(tmp_path):
    repo = ProjectionRepository(str(tmp_path/"state.db"))
    first = repo.record_document(doc(),source_event_id="source-1")
    replay = repo.record_document(doc(),source_event_id="source-1")
    assert first==replay
    assert first["data"]["projection_methods"]["graph"] == "b5-graph-v1"
    assert repo.high_watermark()==1
    pending = repo.events.pending()
    assert len(pending)==1 and pending[0].topic==CORPUS_PROJECTIONS_TOPIC
    event = validate_event(pending[0].topic,pending[0].event_json)
    assert event.payload.snapshot_id==first["snapshot_id"]
    with pytest.raises(ValueError):
        repo.record_document(doc(text="Different body"),source_event_id="source-1")


def test_enqueue_failure_rolls_back_canonical_mutation(tmp_path,monkeypatch):
    repo = ProjectionRepository(str(tmp_path/"state.db"))
    def fail(*args,**kwargs):
        raise RuntimeError("injected outbox failure")
    monkeypatch.setattr(repo.events,"enqueue",fail)
    with pytest.raises(RuntimeError):
        repo.record_document(doc(),source_event_id="source-1")
    assert repo.current("document","doc-1") is None
    assert repo.high_watermark()==0


def test_precedence_stale_update_and_withdrawal_replay_protection(tmp_path):
    repo = ProjectionRepository(str(tmp_path/"state.db"))
    first = repo.record_document(doc(day=5),source_event_id="canonical")
    assert repo.record_document(doc(citable=False,day=6),source_event_id="lead",source_kind="discovery")["snapshot_id"]==first["snapshot_id"]
    assert repo.record_document(doc(day=1,text="Old body"),source_event_id="older")["revision"]==1
    tombstone = repo.withdraw("doc-1",operation_id="remove-1",reason="Provider withdrew source")
    assert not tombstone["eligible"] and tombstone["revision"]==2
    assert repo.record_document(doc(day=5),source_event_id="canonical")["revision"]==1
    newer = repo.record_document(doc(day=7,text="New source body"),source_event_id="newer")
    assert newer["data"]["withdrawn"] and not newer["eligible"]
    assert repo.withdraw("doc-1",operation_id="remove-1",reason="Provider withdrew source")["revision"]==2
    restored = repo.withdraw("doc-1",operation_id="restore-1",reason="Provider restored source",restore=True)
    assert restored["eligible"] and not restored["data"]["withdrawn"]
    with pytest.raises(ValueError):
        repo.withdraw("doc-1",operation_id="restore-1",reason="Other reason",restore=True)


def test_restore_never_promotes_discovery(tmp_path):
    repo = ProjectionRepository(str(tmp_path/"state.db"))
    repo.record_document(doc(citable=False),source_event_id="lead",source_kind="discovery")
    repo.withdraw("doc-1",operation_id="remove",reason="policy")
    assert not repo.withdraw("doc-1",operation_id="restore",reason="policy cleared",restore=True)["eligible"]


def test_delivery_namespaces_and_cache_token_change_on_catchup(tmp_path):
    repo = ProjectionRepository(str(tmp_path/"state.db"))
    first = repo.record_document(doc(),source_event_id="source-1")
    token = repo.cache_token("document",None,"elasticsearch")
    repo.mark_delivery("elasticsearch",first)
    assert token!=repo.cache_token("document",None,"elasticsearch")
    assert repo.delivery("neo4j","document","doc-1") is None
    second = repo.record_document(doc(day=2,text="New body"),source_event_id="source-2")
    repo.mark_delivery("elasticsearch",second)
    repo.mark_delivery("elasticsearch",first)
    assert repo.delivery("elasticsearch","document","doc-1")["revision"]==2
    repo.mark_delivery("neo4j",first,"shadow")
    assert repo.delivery("neo4j","document","doc-1","shadow")["revision"]==1


def test_withheld_snapshot_clears_report_and_scopes_reverification(tmp_path):
    repo = ProjectionRepository(str(tmp_path/"state.db"))
    repo.record_document(doc(),source_event_id="source-1")
    workspace={"investigation_id":"inv-1","retrieved_documents":[doc().model_dump(mode="json")],"report":{"key_claims":[]},"cached":False}
    first=repo.record_investigation(workspace,run_id="run-1",terminal_decision="published",source_event_id="completed-1")
    assert first["eligible"] and len(first["data"]["document_snapshot_ids"])==1
    workspace["cached"]=True
    assert repo.record_investigation(workspace,run_id="run-1",terminal_decision="published",source_event_id="completed-1")["revision"]==1
    withheld=repo.record_investigation(workspace,run_id="run-2",terminal_decision="insufficient_evidence",source_event_id="completed-2")
    assert not withheld["eligible"] and withheld["data"]["workspace"]["report"] is None
    assert repo.get_snapshot(first["snapshot_id"])["data"]["workspace"]["report"] is not None


def test_manifest_cas_and_rollback_validation(tmp_path):
    repo=ProjectionRepository(str(tmp_path/"state.db"))
    repo.create_generation("shadow-1")
    watermark=repo.high_watermark()
    repo.record_document(doc(),source_event_id="source-1")
    with pytest.raises(ValueError,match="changed"):
        repo.activate_generation("shadow-1",{"complete":True,"drift":[],"high_watermark":watermark})
    repo.activate_generation("shadow-1",{"complete":True,"drift":[],"high_watermark":repo.high_watermark()})
    assert repo.manifest()["previous_generation"]=="live"
    with pytest.raises(ValueError):
        repo.activate_generation("live",{"complete":False,"drift":[]})
    repo.activate_generation("live",{"complete":True,"drift":[],"high_watermark":repo.high_watermark()})
    assert repo.manifest()["generation"]=="live"


def test_processed_transaction_failure_has_no_partial_lineage(tmp_path,monkeypatch):
    target=str(tmp_path/"state.db")
    handlers=EventHandlers(target)
    repo=ProjectionRepository(target)
    event=ProcessedDocumentEvent.create(ProcessedDocumentPayload(document=doc(),processing=ProcessingReceipt(normalizer_version="b4-v1",raw_event_id="raw-1")),
                                       producer="test",correlation_id="test",partition_key="doc-1",event_id="processed-1")
    monkeypatch.setattr("services.signal_repository.SignalRepository.project_processed",lambda *a,**k:(_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        handlers.persist_processed_document(event)
    assert repo.current("document","doc-1") is None
    assert repo.events.pending()==[]
    with connect(target) as db:
        assert db.execute("SELECT COUNT(*) AS n FROM processed_document_lineage").fetchone()["n"]==0
