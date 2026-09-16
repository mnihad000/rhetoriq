from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from models.document import Document
from models.events import RawDocumentEvent, RawDocumentPayload, RetrievalReceipt


def raw_event_from_document(
    document: Document,
    *,
    producer: str,
    correlation_id: str,
    provider: str | None = None,
    transport: str | None = None,
    run_id: str | None = None,
    action_id: str | None = None,
    investigation_id: str | None = None,
    causation_id: str | None = None,
) -> RawDocumentEvent:
    metadata = document.metadata or {}
    resolved_provider = provider or str(metadata.get("provider") or document.source_name)
    resolved_transport = transport or str(
        metadata.get("retrieval_transport") or metadata.get("transport") or "connector"
    )
    evidence_status = str(metadata.get("evidence_status") or "provider_metadata_only")
    if evidence_status == "canonical_evidence":
        receipt_status = "canonical_evidence"
    elif evidence_status == "primary_source_record":
        receipt_status = "primary_source_record"
    else:
        receipt_status = "provider_metadata_only"
    policy = metadata.get("source_policy") if isinstance(metadata.get("source_policy"), dict) else {}
    fetch = metadata.get("fetch_outcome") if isinstance(metadata.get("fetch_outcome"), dict) else {}
    native_id = str(metadata.get("source_native_id") or document.source_id or document.id)
    collected_at = document.collected_at or datetime.now(timezone.utc)
    content_hash = hashlib.sha256(document.text.encode("utf-8")).hexdigest() if document.text else None
    return RawDocumentEvent.create(
        RawDocumentPayload(
            provider=resolved_provider,
            transport=resolved_transport,
            provider_record_id=native_id,
            provider_query=metadata.get("search_query") or metadata.get("provider_query"),
            provider_cursor=metadata.get("provider_cursor"),
            source_url=document.url,
            canonical_url=str(metadata.get("canonical_url") or document.url),
            title=document.title,
            body=document.text,
            snippet=document.snippet,
            author=document.author,
            published_at=document.published_at,
            collected_at=collected_at,
            language=document.language,
            content_type=document.content_type or "document",
            source_type=document.source_type,
            retrieval=RetrievalReceipt(
                status=receipt_status,
                http_status=fetch.get("http_status"),
                parser_version=fetch.get("parser_version") or metadata.get("parser_version"),
                content_hash=fetch.get("content_hash") or content_hash,
                policy_decision=str(policy.get("decision") or "not_evaluated"),
                limitations=[str(item) for item in metadata.get("limitations", [])],
            ),
            provider_metadata={
                key: value for key, value in metadata.items()
                if key not in {"source_policy", "fetch_outcome", "limitations"}
            },
            investigation_id=investigation_id,
            run_id=run_id,
            action_id=action_id,
            normalized_document=document,
        ),
        producer=producer,
        correlation_id=correlation_id,
        causation_id=causation_id,
        partition_key=document.id,
        occurred_at=document.published_at or collected_at,
    )
