from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
from typing import Any

import httpx

from config import get_settings
from models.document import Document, SourceProfile
from models.investigation import InvestigationPlanTimeWindow
from services.provider_http import ProviderRequestError, RetryingJsonClient


LEGAL_STATUS_LIMITATION = (
    "FederalRegister.gov is an unofficial informational rendition; legal reliance should be "
    "verified against the linked official GovInfo PDF."
)


@dataclass
class FederalRegisterBatch:
    documents: list[Document] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None


class FederalRegisterUnavailable(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class FederalRegisterClient:
    """Bounded first-party Federal Register document search."""

    name = "federal_register"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=None,
    ) -> None:
        settings = get_settings()
        self._settings = settings
        self._base_url = (base_url or settings.FEDERAL_REGISTER_BASE_URL).rstrip("/")
        kwargs: dict[str, Any] = {}
        if sleep is not None:
            kwargs["sleep"] = sleep
        self._client = RetryingJsonClient(
            self.name,
            timeout_seconds=settings.FEDERAL_REGISTER_TIMEOUT_SECONDS,
            max_retries=settings.FEDERAL_REGISTER_MAX_RETRIES,
            backoff_seconds=settings.PROVIDER_RETRY_BACKOFF_SECONDS,
            max_backoff_seconds=settings.PROVIDER_MAX_BACKOFF_SECONDS,
            min_interval_seconds=settings.FEDERAL_REGISTER_MIN_INTERVAL_SECONDS,
            transport=transport,
            **kwargs,
        )
        self.last_diagnostics: dict[str, Any] = {
            "provider": self.name,
            "outcome": "unknown",
        }

    def search(
        self,
        query: str,
        time_window: InvestigationPlanTimeWindow,
        *,
        limit: int,
    ) -> FederalRegisterBatch:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Federal Register search requires a non-empty query.")
        limit = max(1, limit)
        page_size = min(limit, self._settings.FEDERAL_REGISTER_PAGE_SIZE)
        start_date, end_date = _date_bounds(time_window)
        params: dict[str, Any] = {
            "conditions[term]": normalized_query,
            "per_page": page_size,
            "order": "newest",
        }
        if start_date:
            params["conditions[publication_date][gte]"] = start_date
        if end_date:
            params["conditions[publication_date][lte]"] = end_date

        documents: list[Document] = []
        receipts: list[dict[str, Any]] = []
        seen_native_ids: set[str] = set()
        malformed = 0
        duplicates = 0
        total_retries = 0
        page_count = 0
        collected_at = datetime.now(timezone.utc)
        last_request: dict[str, Any] = {}

        for page in range(1, self._settings.FEDERAL_REGISTER_MAX_PAGES + 1):
            if len(documents) >= limit:
                break
            page_params = {**params, "page": page}
            try:
                payload, request_diagnostics = self._client.get_json(
                    f"{self._base_url}/documents.json",
                    params=page_params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "RhetoriQ/0.3 (+public evidence research)",
                    },
                )
            except ProviderRequestError as exc:
                self.last_diagnostics = {
                    **exc.diagnostics.as_dict(),
                    "query": normalized_query,
                    "pages_completed": page_count,
                    "fallback": "broad_web_search",
                }
                raise FederalRegisterUnavailable(str(exc), self.last_diagnostics) from exc

            last_request = request_diagnostics.as_dict()
            total_retries += request_diagnostics.retry_count
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                self.last_diagnostics = {
                    **last_request,
                    "outcome": "unavailable",
                    "failure_category": "malformed_response",
                    "detail": "Federal Register response did not contain a results array.",
                    "query": normalized_query,
                    "pages_completed": page_count,
                    "fallback": "broad_web_search",
                }
                raise FederalRegisterUnavailable(
                    "Federal Register returned a malformed response.",
                    self.last_diagnostics,
                )
            page_count += 1
            for rank_on_page, item in enumerate(raw_results, start=1):
                if len(documents) >= limit:
                    break
                if not isinstance(item, dict):
                    malformed += 1
                    continue
                native_id = str(item.get("document_number") or "").strip()
                title = str(item.get("title") or "").strip()
                if not native_id or not title:
                    malformed += 1
                    continue
                if native_id in seen_native_ids:
                    duplicates += 1
                    continue
                seen_native_ids.add(native_id)
                rank = (page - 1) * page_size + rank_on_page
                document, receipt = self._map_record(
                    item,
                    query=normalized_query,
                    rank=rank,
                    collected_at=collected_at,
                    request_diagnostics=request_diagnostics.as_dict(),
                )
                documents.append(document)
                receipts.append(receipt)

            total_pages = _positive_int(payload.get("total_pages"))
            if not raw_results or (total_pages is not None and page >= total_pages):
                break

        partial = malformed > 0
        warning = (
            f"Federal Register skipped {malformed} malformed record(s)."
            if malformed
            else None
        )
        self.last_diagnostics = {
            **last_request,
            "outcome": "partial" if partial else "success",
            "query": normalized_query,
            "accepted_results": len(documents),
            "malformed_results": malformed,
            "duplicate_results": duplicates,
            "pages_completed": page_count,
            "retry_count": total_retries,
            "source_policy": _source_policy(),
        }
        return FederalRegisterBatch(
            documents=documents,
            receipts=receipts,
            diagnostics=self.last_diagnostics,
            warning=warning,
        )

    def health(self, *, probe: bool = False) -> dict[str, Any]:
        if probe:
            try:
                self.search(
                    "public records",
                    InvestigationPlanTimeWindow(label="recent"),
                    limit=1,
                )
            except (FederalRegisterUnavailable, ValueError):
                pass
        outcome = self.last_diagnostics.get("outcome", "unknown")
        return {
            **self.last_diagnostics,
            "status": "ready" if outcome in {"success", "partial"} else "unavailable" if outcome == "unavailable" else "unknown",
            "probe_performed": probe,
        }

    def _map_record(
        self,
        item: dict[str, Any],
        *,
        query: str,
        rank: int,
        collected_at: datetime,
        request_diagnostics: dict[str, Any],
    ) -> tuple[Document, dict[str, Any]]:
        native_id = str(item["document_number"]).strip()
        canonical_url = str(item.get("html_url") or f"https://www.federalregister.gov/d/{native_id}").strip()
        title = str(item["title"]).strip()
        abstract = str(item.get("abstract") or "").strip()
        published_at = _parse_publication_date(item.get("publication_date"))
        agencies = _agency_names(item.get("agencies"))
        source_name = agencies[0] if agencies else "Federal Register"
        limitations = [LEGAL_STATUS_LIMITATION]
        if not abstract:
            limitations.append("The API record did not include an abstract; only title and structured metadata were retained.")
        if published_at is None:
            limitations.append("The API record did not include a usable publication date.")
        policy = _source_policy()
        fetch_outcome = {
            "status": "success",
            "http_status": request_diagnostics.get("http_status"),
            "attempts": request_diagnostics.get("attempts"),
            "retry_count": request_diagnostics.get("retry_count"),
        }
        metadata = {
            "provider": self.name,
            "transport": "first_party_public_api",
            "retrieval_transport": "federal_register_api",
            "source_native_id": native_id,
            "search_query": query,
            "search_rank": rank,
            "canonical_url": canonical_url,
            "published_at": published_at.isoformat() if published_at else None,
            "collected_at": collected_at.isoformat(),
            "document_type": item.get("type"),
            "agencies": agencies,
            "citation": item.get("citation"),
            "docket_ids": item.get("docket_ids") or [],
            "raw_text_url": item.get("raw_text_url"),
            "pdf_url": item.get("pdf_url"),
            "body_html_url": item.get("body_html_url"),
            "source_policy": policy,
            "fetch_outcome": fetch_outcome,
            "limitations": limitations,
            "evidence_status": "primary_source_record",
            "acquisition_receipt_valid": True,
        }
        document = Document(
            id="fr_" + hashlib.sha256(native_id.encode("utf-8")).hexdigest()[:16],
            source_id=f"federal_register:{native_id}",
            source_name=source_name,
            source_type="government_record",
            url=canonical_url,
            title=title,
            published_at=published_at,
            collected_at=collected_at,
            text="\n\n".join(part for part in (title, abstract) if part),
            snippet=abstract[:320] if abstract else title[:320],
            language="en",
            content_type=str(item.get("type") or "federal_register_document").lower().replace(" ", "_"),
            geographic_scope="national",
            entities=agencies[:12],
            phrases=_phrases(title, query),
            metadata=metadata,
            source_profile=SourceProfile(
                institution_kind="official",
                content_form="unknown",
                ideology="unknown",
                classification_method="registry",
                classification_confidence="high",
            ),
        )
        receipt = {
            "query": query,
            "provider": self.name,
            "source_native_id": native_id,
            "rank": rank,
            "canonical_url": canonical_url,
            "publication_timestamp": published_at.isoformat() if published_at else None,
            "collection_timestamp": collected_at.isoformat(),
            "document_id": document.id,
            "source_policy": policy,
            "fetch_outcome": fetch_outcome,
            "evidence_status": "primary_source_record",
            "limitations": limitations,
        }
        return document, receipt


def _source_policy() -> dict[str, Any]:
    return {
        "decision": "allow",
        "basis": "first_party_public_record_api",
        "public_access_only": True,
        "authentication_required": False,
        "citable": True,
        "retention": "public_record_metadata_and_abstract",
    }


def _date_bounds(time_window: InvestigationPlanTimeWindow) -> tuple[str | None, str | None]:
    if time_window.start or time_window.end:
        return time_window.start, time_window.end
    today = date.today()
    days = {
        "today": 0,
        "this_week": 7,
        "this_month": 31,
        "recent": 31,
    }.get(time_window.label)
    if days is None:
        return None, None
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def _parse_publication_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _agency_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("raw_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _phrases(title: str, query: str) -> list[str]:
    values = [query.strip().lower()]
    words = [word.strip(".,:;()[]").lower() for word in title.split() if len(word.strip(".,:;()[]")) > 3]
    values.extend(" ".join(words[index:index + 2]) for index in range(max(0, len(words) - 1)))
    return list(dict.fromkeys(value for value in values if value))[:8]
