"""Independent, at-least-once projection workers with target revision guards."""
from __future__ import annotations

import logging
import socket
import time

from config import get_settings
from events.worker import EventProcessor, KafkaEventWorker
from models.document import Document
from models.events import CORPUS_PROJECTIONS_TOPIC, INVESTIGATION_PROJECTIONS_TOPIC, ProjectionPayload
from services.b5_repository import ProjectionRepository, MINILM_IDENTITY, MINILM_REVISION
from services.database import connect, is_postgres_database
from services.event_store import EventStore
from services.postgres_corpus import DEFAULT_MODEL, embedding_input_hash, validate_embedding, _vector_literal


class ProjectionHandlers:
    def __init__(self, target=None, *, settings=None, repository=None, adapter=None, embedding_service=None, recorded_only=False):
        self.settings = settings or get_settings()
        self.target = target or self.settings.B5_WORKER_TARGET
        self.repository = repository or ProjectionRepository(self.settings.persistence_target)
        self.adapter = adapter
        self.embedding_service = embedding_service
        self.recorded_only = recorded_only
        self.worker_id = f"b5-{self.target}-{socket.gethostname()}"
        self._last_heartbeat = 0.0
        self._initialized: set[str] = set()

    def _adapter(self):
        if self.adapter is None and self.target != "minilm":
            from services.b5_targets import ElasticsearchTarget, Neo4jTarget
            self.adapter = (ElasticsearchTarget if self.target == "elasticsearch" else Neo4jTarget)(self.settings)
        return self.adapter

    def handler_for(self, topic):
        if topic not in {CORPUS_PROJECTIONS_TOPIC, INVESTIGATION_PROJECTIONS_TOPIC}:
            raise ValueError("B5 workers only accept immutable projection events")
        if topic == INVESTIGATION_PROJECTIONS_TOPIC and self.target != "neo4j":
            raise ValueError("Investigation projection belongs to Neo4j")
        return f"b5-{self.target}-projector-v1", self.project_event

    def project_event(self, event):
        payload = ProjectionPayload.model_validate(event.payload)
        snapshot = self.repository.get_snapshot(payload.snapshot_id)
        if snapshot is None:
            raise ValueError("Recorded projection snapshot is missing")
        expected_kind = "investigation" if event.event_type == "investigation.projection.requested" else "document"
        if (snapshot["kind"],snapshot["domain_id"],snapshot["revision"],snapshot["semantic_hash"],snapshot["eligible"],snapshot["operation"]) != (
            expected_kind,payload.domain_id,payload.revision,payload.semantic_hash,payload.eligible,payload.operation):
            raise ValueError("Projection event does not match its immutable snapshot")
        generations = [self.repository.manifest()["generation"], *self.repository.building_generations()]
        for generation in dict.fromkeys(generations):
            self.apply(snapshot, generation)

    def apply(self, snapshot, generation="live"):
        prior = self.repository.delivery(self.target,snapshot["kind"],snapshot["domain_id"],generation)
        if prior and prior["revision"] >= snapshot["revision"] and prior["status"] == "applied":
            if prior["revision"] == snapshot["revision"] and prior["semantic_hash"] != snapshot["semantic_hash"]:
                raise ValueError("Projection revision conflict")
            return False
        try:
            if self.target == "minilm":
                self._semantic(snapshot)
            else:
                document_snapshots = [self.repository.get_snapshot(ref) for ref in snapshot["data"].get("document_snapshot_ids", [])]
                if any(item is None for item in document_snapshots):
                    raise ValueError("Investigation references a missing recorded document snapshot")
                adapter = self._adapter()
                if generation not in self._initialized:
                    adapter.initialize(generation)
                    self._initialized.add(generation)
                result = adapter.apply(snapshot,generation,document_snapshots=document_snapshots)
                if result.get("status") not in {"applied", "stale", "skipped"}:
                    raise RuntimeError("Projection target did not confirm a complete write")
            self.repository.mark_delivery(self.target,snapshot,generation)
            return True
        except Exception as exc:
            # Never persist credentials, target exception strings, or source text.
            self.repository.mark_delivery(self.target,snapshot,generation,error=type(exc).__name__)
            self.repository.heartbeat(self.worker_id,self.target,"degraded",type(exc).__name__)
            raise

    def _semantic(self, snapshot):
        if snapshot["kind"] != "document":
            raise ValueError("Semantic worker only projects documents")
        if snapshot["data"].get("withdrawn"):
            return
        document = Document.model_validate(snapshot["data"]["document"])
        identity = snapshot["data"].get("model_identities", {}).get("semantic", MINILM_IDENTITY)
        if identity != MINILM_IDENTITY or getattr(self.settings,"B5_MODEL_REVISION",MINILM_REVISION) != MINILM_REVISION:
            raise ValueError("Recorded MiniLM model revision does not match the pinned runtime")
        input_hash = embedding_input_hash(document)
        vector = self.repository.embedding(identity,input_hash)
        if vector is None:
            if self.recorded_only:
                raise ValueError("Replay requires a recorded MiniLM embedding")
            if self.embedding_service is None:
                from services.embedding_service import get_embedding_service
                self.embedding_service = get_embedding_service()
            if self.embedding_service.model_name != DEFAULT_MODEL:
                raise ValueError("Semantic worker must use the MiniLM embedding space")
            vector = self.embedding_service.embed_document(document)
            vector = validate_embedding(vector,model_name=self.embedding_service.model_name,expected_model=DEFAULT_MODEL)
            if vector is None or not any(vector):
                raise RuntimeError("MiniLM inference unavailable")
            self.repository.save_embedding(identity,input_hash,vector)
        _vector_literal(vector)
        if is_postgres_database(self.repository.target):
            with connect(self.repository.target) as db:
                self.repository._lock(db,"document",document.id)
                current = self.repository.current("document",document.id,connection=db)
                if current is None or current["snapshot_id"] != snapshot["snapshot_id"]:
                    return
                db.execute("UPDATE semantic_document_corpus SET embedding=?::vector,embedding_status='ready',embedding_model=? "
                           "WHERE document_id=? AND embedding_input_hash=?",
                           (_vector_literal(vector),DEFAULT_MODEL,document.id,input_hash))

    def heartbeat(self):
        if time.monotonic()-self._last_heartbeat > 15:
            self.repository.heartbeat(self.worker_id,self.target)
            self._last_heartbeat = time.monotonic()

    def close(self):
        self.repository.heartbeat(self.worker_id,self.target,"stopped")
        if self.adapter is not None and hasattr(self.adapter,"close"):
            self.adapter.close()


def main():
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    handlers = ProjectionHandlers(settings=settings)
    adapter = handlers._adapter()
    if adapter:
        adapter.initialize(handlers.repository.manifest()["generation"])
    else:
        from services.embedding_service import get_embedding_service
        handlers.embedding_service = get_embedding_service()
        handlers.embedding_service.load_model()
    worker = KafkaEventWorker(role=f"b5-{settings.B5_WORKER_TARGET}")
    worker.processor = EventProcessor(EventStore(settings.persistence_target),handlers)
    worker.run_forever()


if __name__ == "__main__":
    main()
