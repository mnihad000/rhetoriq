"""
Ingestion coordinator.

Sources:
  GDELT  — news articles, national/local/blog classification, no key required
  HN     — forum layer (Hacker News), no key required

Both results are staged through the Kafka outbox. The processed-document
consumer is the only production writer to the live/canonical stores.
"""

from datetime import datetime, timezone
from uuid import uuid4

from config import get_settings
from models.document import Document
from services.document_store import live_store
from models.events import RAW_DOCUMENTS_TOPIC
from services.event_factory import raw_event_from_document
from services.event_store import EventStore
from services.gdelt import GDELTIngestion
from services.hn_ingestion import HNIngestion


class IngestionCoordinator:
    def __init__(self) -> None:
        self._gdelt = GDELTIngestion()
        self._hn = HNIngestion()
        self._events = EventStore(get_settings().persistence_target)

    def ingest(
        self,
        query: str,
        start_dt: datetime,
        end_dt: datetime,
        include_hn: bool = True,
        hn_num_results: int = 50,
    ) -> dict:
        errors: list[str] = []

        gdelt_docs: list[Document] = []
        try:
            gdelt_docs = self._gdelt.fetch_articles(query, start_dt, end_dt)
        except Exception as exc:
            errors.append(f"GDELT: {exc}")

        hn_docs: list[Document] = []
        if include_hn:
            try:
                hn_docs = self._hn.fetch_stories(
                    query, start_dt, end_dt, num_results=hn_num_results
                )
            except Exception as exc:
                errors.append(f"HN: {exc}")

        collection_id = f"collection_{uuid4().hex}"
        event_ids: list[str] = []
        for document in [*gdelt_docs, *hn_docs]:
            event = raw_event_from_document(
                document,
                producer="ingestion-api",
                correlation_id=collection_id,
            )
            self._events.enqueue(RAW_DOCUMENTS_TOPIC, event)
            event_ids.append(event.event_id)

        return {
            "collection_id": collection_id,
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "status": "queued",
            "event_ids": event_ids,
            "accepted_count": len(event_ids),
            "query": query,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "gdelt_ingested": len(gdelt_docs),
            "hn_ingested": len(hn_docs),
            "total_ingested": len(gdelt_docs) + len(hn_docs),
            "errors": errors,
        }

    def stage_documents(self, documents: list[Document], *, producer: str, correlation_id: str) -> list[str]:
        event_ids: list[str] = []
        for document in documents:
            event = raw_event_from_document(
                document,
                producer=producer,
                correlation_id=correlation_id,
            )
            self._events.enqueue(RAW_DOCUMENTS_TOPIC, event)
            event_ids.append(event.event_id)
        return event_ids


def get_merged_documents(demo_docs: list[Document]) -> list[Document]:
    """
    Returns live store documents merged with the demo corpus.
    Live documents take precedence (deduplicated by id).
    Demo corpus fills gaps when the store is empty.
    """
    settings = get_settings()
    live = live_store.get_all()
    if not settings.DEMO_MODE:
        return live
    if not live:
        return demo_docs
    live_ids = {d.id for d in live}
    return live + [d for d in demo_docs if d.id not in live_ids]
