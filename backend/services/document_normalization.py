"""Deterministic normalization used by the B4 stream boundary.

The normalizer has no network or clock dependency.  It turns a collected raw
event into the immutable input sent to enrichment, so replaying the same raw
event produces the same document and input hash.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from models.document import Document
from models.events import EnrichmentRequestedEvent, EnrichmentRequestedPayload, RawDocumentEvent


NORMALIZER_VERSION = "b4-v1"
_WHITESPACE = re.compile(r"\s+")
_SOURCE_TYPES = {
    "forum", "blog", "local_news", "national_news", "commentary",
    "speech_transcript", "government_record",
}


def _clean(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFC", str(value))
    # Keep line/tab separators as whitespace, while dropping invisible control
    # characters that otherwise make hashes and evidence offsets unstable.
    normalized = "".join(
        char for char in normalized
        if not unicodedata.category(char).startswith("C") or char in "\t\n\r"
    )
    return _WHITESPACE.sub(" ", normalized).strip()


def normalize_url(value: str) -> str:
    """Canonicalize a URL without changing its path or query semantics."""
    value = _clean(value)
    if not value:
        return value
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if parts.scheme and not hostname:
        raise ValueError("Malformed URL host")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Malformed URL port") from exc
    if parts.username or parts.password:
        raise ValueError("URLs with userinfo are not accepted")
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    netloc = host
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    # Sort parameter names while preserving the order of repeated values.
    grouped: dict[str, list[str]] = {}
    for key, item in query_pairs:
        grouped.setdefault(key, []).append(item)
    query = "&".join(
        f"{quote(key, safe='')}={quote(item, safe='')}"
        for key in sorted(grouped)
        for item in grouped[key]
    )
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_type(raw_type: str | None, host: str, source_name: str) -> str:
    candidate = (raw_type or "").strip().lower()
    if candidate in _SOURCE_TYPES:
        return candidate
    if host.endswith(".gov") or host == "federalregister.gov":
        return "government_record"
    if any(token in host for token in ("forum", "reddit", "discourse")):
        return "forum"
    if any(token in host for token in ("blog", "substack", "medium", "wordpress")):
        return "blog"
    if any(token in host for token in ("opinion", "commentary")):
        return "commentary"
    return "commentary"


def _stable_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _input_hash(document: Document) -> str:
    data = document.model_dump(mode="json", exclude={"metadata", "embedding", "entities", "phrases"})
    return _stable_hash(data)


def normalize_raw_event(event: RawDocumentEvent) -> EnrichmentRequestedEvent:
    """Normalize one raw event into a deterministic enrichment request."""
    raw = event.payload
    source_url = normalize_url(raw.source_url)
    canonical_url = normalize_url(raw.canonical_url or raw.source_url)
    parts = urlsplit(canonical_url or source_url)
    host = parts.hostname or ""
    title = _clean(raw.title) or "Untitled document"
    text = _clean(raw.body or raw.snippet or title)
    snippet = _clean(raw.snippet) or text[:320]
    collected_at = _to_utc(raw.collected_at)
    published_at = _to_utc(raw.published_at)
    event_time_quality = "published_at"
    quality_flags = list(dict.fromkeys(str(flag) for flag in raw.retrieval.limitations))
    if not raw.body:
        quality_flags.append("body_fallback")
    if published_at is None:
        quality_flags.append("published_at_fallback")
        published_at = collected_at
        event_time_quality = "collected_at_fallback"
    elif collected_at and published_at > collected_at:
        quality_flags.extend(("future_published_at", "published_at_fallback"))
        published_at = collected_at
        event_time_quality = "collected_at_fallback"
    if not raw.language:
        quality_flags.append("language_unknown")
    language = _clean(raw.language).lower() or None
    if language:
        language = language.split("-")[0].split("_")[0]
    source_name = host or _clean(raw.provider) or "unknown"
    if not (raw.source_type or host):
        quality_flags.append("source_type_unknown")
    source_type = _source_type(raw.source_type, host, source_name)
    existing = raw.normalized_document
    document_id = existing.id if existing else "doc_" + _stable_hash({"url": canonical_url, "provider_record_id": raw.provider_record_id})[:16]
    document = Document(
        id=document_id,
        source_id=existing.source_id if existing else f"domain:{host}" if host else None,
        source_name=(_clean(existing.source_name) or source_name) if existing else source_name,
        source_type=existing.source_type if existing and existing.source_type in _SOURCE_TYPES else source_type,
        url=canonical_url or source_url,
        title=title,
        author=_clean(raw.author) or (existing.author if existing else None),
        published_at=published_at,
        collected_at=collected_at,
        text=text,
        snippet=snippet,
        language=language or (existing.language if existing else None),
        content_type=_clean(raw.content_type) or (existing.content_type if existing else "document"),
        geographic_scope=existing.geographic_scope if existing else None,
        # Entity/phrase extraction belongs to enrichment.  Reusing values from
        # a legacy normalized payload would make replay depend on old code.
        entities=[],
        phrases=[],
        claims=None,
        duplicate_of_doc_id=None,
        is_seeded_demo_data=existing.is_seeded_demo_data if existing else None,
        source_profile=existing.source_profile if existing else None,
        metadata={},
    )
    input_hash = _input_hash(document)
    exact_content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    inherited_metadata = dict(existing.metadata or {}) if existing else {}
    inherited_metadata.pop("input_hash", None)
    document.metadata = {
        **inherited_metadata,
        "normalizer_version": NORMALIZER_VERSION,
        "input_hash": input_hash,
        "exact_content_hash": exact_content_hash,
        "event_time_quality": event_time_quality,
        "raw_event_id": event.event_id,
        "provider": raw.provider,
        "provider_record_id": raw.provider_record_id,
        "transport": raw.transport,
        "retrieval": raw.retrieval.model_dump(mode="json"),
        "provider_metadata": dict(raw.provider_metadata),
        "canonical_url": canonical_url,
        "lineage": {"raw_event_id": event.event_id, "correlation_id": event.correlation_id},
    }
    payload = EnrichmentRequestedPayload(
        document=document,
        input_hash=input_hash,
        pipeline_version=NORMALIZER_VERSION,
        raw_event_id=event.event_id,
        quality_flags=list(dict.fromkeys(quality_flags)),
        investigation_id=raw.investigation_id,
        run_id=raw.run_id,
        action_id=raw.action_id,
    )
    event_id = "enrichreq_" + _stable_hash({"raw_event_id": event.event_id, "input_hash": input_hash})[:24]
    return EnrichmentRequestedEvent.create(
        payload,
        producer="flink-normalizer",
        correlation_id=event.correlation_id,
        causation_id=event.event_id,
        partition_key=document.id,
        occurred_at=published_at or collected_at or event.occurred_at,
        event_id=event_id,
    )


# Compatibility alias used by early B4 fixtures.
normalize_event = normalize_raw_event
