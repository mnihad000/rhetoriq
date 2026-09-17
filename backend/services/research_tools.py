from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import time
from urllib.parse import urlparse

import httpx

from config import get_settings
from demo_data import ALL_DOCUMENTS
from models.document import Document
from models.investigation import FetchFailure, InvestigationPlan, RawPage, SearchResult
from models.research import ResearchActionDecision
from services.document_normalizer import DocumentNormalizer
from services.federal_register import FederalRegisterClient, FederalRegisterUnavailable
from services.gdelt import GDELTIngestion
from services.hn_ingestion import HNIngestion
from services.ingestion import get_merged_documents
from services.provider_http import retry_after_seconds
from services.search_provider import SearchProvider, SearchProviderUnavailable, build_search_provider
from services.url_policy import PublicUrlPolicy

logger = logging.getLogger(__name__)


@dataclass
class ToolOutcome:
    provider: str
    candidates: list[SearchResult] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    receipts: list[tuple[str, dict]] = field(default_factory=list)
    warning: str | None = None
    retryable: bool = False
    retry_after_seconds: float | None = None


class SafePageFetcher:
    def __init__(self, policy: PublicUrlPolicy | None = None) -> None:
        self.settings = get_settings()
        self.policy = policy or PublicUrlPolicy()

    def fetch(self, url: str) -> RawPage | FetchFailure:
        started_url = url
        try:
            self.policy.validate(url)
            self.policy.check_robots(url)
        except Exception as exc:
            return FetchFailure(url=url, error_type="policy_blocked", message=str(exc), retryable=False)

        headers = {
            "User-Agent": self.policy.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
        }
        try:
            with httpx.Client(timeout=self.settings.FETCH_TIMEOUT_SECONDS, headers=headers, follow_redirects=False) as client:
                for _ in range(self.settings.FETCH_MAX_REDIRECTS + 1):
                    response = client.get(url)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        target = response.headers.get("location")
                        if not target:
                            break
                        target = str(response.url.join(target))
                        self.policy.validate(target)
                        url = target
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type")
                    if content_type and "html" not in content_type and "xml" not in content_type:
                        return FetchFailure(
                            url=url, error_type="unsupported_content_type",
                            message=f"Unsupported content type: {content_type}",
                            status_code=response.status_code, retryable=False,
                        )
                    content = response.content
                    if len(content) > self.settings.FETCH_MAX_RESPONSE_BYTES:
                        return FetchFailure(
                            url=url, error_type="response_too_large",
                            message="Response exceeded the configured evidence size limit.",
                            status_code=response.status_code, retryable=False,
                        )
                    return RawPage(
                        url=started_url, final_url=str(response.url), status_code=response.status_code,
                        content_type=content_type, html=response.text, fetched_at=datetime.now(timezone.utc),
                    )
            return FetchFailure(url=url, error_type="redirect_limit", message="Redirect limit exceeded.", retryable=False)
        except httpx.TimeoutException as exc:
            return FetchFailure(url=url, error_type="timeout", message=str(exc), retryable=True)
        except httpx.HTTPStatusError as exc:
            return FetchFailure(
                url=url, error_type="http_status", message=str(exc), status_code=exc.response.status_code,
                retryable=exc.response.status_code == 429 or exc.response.status_code >= 500,
                retry_after_seconds=retry_after_seconds(exc.response.headers.get("Retry-After")),
            )
        except httpx.HTTPError as exc:
            return FetchFailure(url=url, error_type="http_error", message=str(exc), retryable=True)
        except Exception as exc:
            return FetchFailure(url=url, error_type="policy_blocked", message=str(exc), retryable=False)


class BrowserServiceClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.policy = PublicUrlPolicy()

    def fetch(self, url: str) -> RawPage | FetchFailure:
        try:
            self.policy.validate(url)
            self.policy.check_robots(url)
        except Exception as exc:
            return FetchFailure(url=url, error_type="policy_blocked", message=str(exc), retryable=False)
        headers = {"X-RhetoriQ-Browser-Token": self.settings.BROWSER_SERVICE_TOKEN} if self.settings.BROWSER_SERVICE_TOKEN else {}
        try:
            response = httpx.post(
                f"{self.settings.BROWSER_SERVICE_URL.rstrip('/')}/render",
                json={
                    "url": url,
                    "timeout_ms": self.settings.FETCH_TIMEOUT_SECONDS * 1000,
                    "max_response_bytes": self.settings.FETCH_MAX_RESPONSE_BYTES,
                },
                headers=headers,
                timeout=self.settings.FETCH_TIMEOUT_SECONDS + 5,
            )
            response.raise_for_status()
            payload = response.json()
            self.policy.validate(payload.get("final_url") or url)
            self.policy.check_robots(payload.get("final_url") or url)
            return RawPage.model_validate(payload)
        except Exception as exc:
            return FetchFailure(url=url, error_type="browser_unavailable", message=str(exc), retryable=True)


class ResearchToolRegistry:
    def __init__(
        self,
        *,
        search_provider: SearchProvider | None = None,
        federal_register: FederalRegisterClient | None = None,
        fetcher: SafePageFetcher | None = None,
    ) -> None:
        self.settings = get_settings()
        self.search = search_provider or build_search_provider()
        self.federal_register = federal_register or FederalRegisterClient()
        self.gdelt = GDELTIngestion()
        self.hn = HNIngestion()
        self.fetcher = fetcher or SafePageFetcher()
        self.browser = BrowserServiceClient()
        self.normalizer = DocumentNormalizer()
        self._internal_search_warning: str | None = None

    def execute(
        self,
        decision: ResearchActionDecision,
        plan: InvestigationPlan,
        candidates: list[SearchResult],
    ) -> ToolOutcome:
        if decision.action_type == "browser_fetch" and not self.settings.BROWSER_RENDERING_ENABLED:
            return ToolOutcome(
                provider="policy",
                warning="Browser rendering is disabled for this deployment; use accessible canonical sources.",
            )
        if decision.action_type == "web_search":
            query = decision.query or plan.query_text
            try:
                results = self.search.search(
                    query,
                    plan.time_window,
                    decision.requested_source_classes or plan.target_source_types,
                    self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION,
                )
                diagnostics = self.search.health()
            except SearchProviderUnavailable as exc:
                diagnostics = {
                    **exc.diagnostics,
                    "fallback": "internal_corpus",
                    "visible_limitation": (
                        "Live broad-web discovery was unavailable; internal-corpus recall can continue, "
                        "but it cannot establish current web coverage."
                    ),
                }
                return ToolOutcome(
                    provider=self.search.name,
                    receipts=[("provider_status", diagnostics)],
                    warning=(
                        "live_web_search_unavailable: internal corpus fallback remains available; "
                        "current broad-web coverage is limited."
                    ),
                    retryable=False,
                )
            partial = diagnostics.get("outcome") == "partial"
            return ToolOutcome(
                provider=self.search.name,
                candidates=results,
                receipts=[
                    *[("discovery", _discovery_receipt(item)) for item in results],
                    ("provider_status", diagnostics),
                ],
                warning="Broad-web search returned partial or malformed-engine results." if partial else None,
            )
        if decision.action_type == "federal_register_search":
            query = decision.query or plan.query_text
            try:
                batch = self.federal_register.search(
                    query,
                    plan.time_window,
                    limit=self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION,
                )
            except FederalRegisterUnavailable as exc:
                return ToolOutcome(
                    provider="federal_register",
                    receipts=[("provider_status", exc.diagnostics)],
                    warning=(
                        "federal_register_unavailable: the first-party public-record lane failed; "
                        "official-source coverage is limited."
                    ),
                    retryable=False,
                )
            return ToolOutcome(
                provider="federal_register",
                documents=batch.documents,
                receipts=[
                    *[("primary_source", receipt) for receipt in batch.receipts],
                    ("provider_status", batch.diagnostics),
                ],
                warning=batch.warning,
            )
        if decision.action_type == "gdelt_search":
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=365 if plan.time_window.label == "all_time" else 31)
            documents = self.gdelt.fetch_articles(
                decision.query or plan.query_text, start, end,
                max_records=self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION,
            )
            gdelt_candidates = [
                    SearchResult(
                        query=decision.query or plan.query_text,
                        title=item.title,
                        url=item.url,
                        snippet=item.snippet or item.title,
                        rank=index,
                        provider="gdelt",
                        metadata={
                            "source_document_id": item.id,
                            "published_at": item.published_at.isoformat() if item.published_at else None,
                            "source_native_metadata": item.metadata or {},
                            "requires_canonical_revalidation": True,
                            "canonical_url": item.url,
                            "collected_at": item.collected_at.isoformat() if item.collected_at else datetime.now(timezone.utc).isoformat(),
                            "evidence_status": "discovery_only",
                        },
                    )
                    for index, item in enumerate(documents, start=1)
                    if item.url
                ]
            return ToolOutcome(
                provider="gdelt",
                candidates=gdelt_candidates,
                receipts=[("discovery", _discovery_receipt(item)) for item in gdelt_candidates],
            )
        if decision.action_type == "hacker_news_search":
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=365)
            documents = self.hn.fetch_stories(
                decision.query or plan.query_text, start, end,
                num_results=self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION,
            )
            hn_candidates = [
                    SearchResult(
                        query=decision.query or plan.query_text,
                        title=item.title,
                        url=item.url,
                        snippet=item.snippet or item.title,
                        rank=index,
                        provider="hacker_news",
                        metadata={
                            "source_document_id": item.id,
                            "published_at": item.published_at.isoformat() if item.published_at else None,
                            "source_native_metadata": item.metadata or {},
                            "requires_canonical_revalidation": True,
                            "canonical_url": item.url,
                            "collected_at": item.collected_at.isoformat() if item.collected_at else datetime.now(timezone.utc).isoformat(),
                            "evidence_status": "discovery_only",
                        },
                    )
                    for index, item in enumerate(documents, start=1)
                    if item.url
                ]
            return ToolOutcome(
                provider="hacker_news",
                candidates=hn_candidates,
                receipts=[("discovery", _discovery_receipt(item)) for item in hn_candidates],
            )
        if decision.action_type == "internal_search":
            documents = self._internal_search(decision.query or plan.query_text)
            citable = [item for item in documents if (item.metadata or {}).get("acquisition_receipt_valid") is True]
            leads = [item for item in documents if item not in citable]
            return ToolOutcome(
                provider="internal",
                documents=citable,
                candidates=[
                    SearchResult(
                        query=decision.query or plan.query_text,
                        title=item.title,
                        url=item.url,
                        snippet=item.snippet,
                        rank=index,
                        provider="internal_corpus",
                        metadata={"source_document_id": item.id, "requires_canonical_revalidation": True},
                    )
                    for index, item in enumerate(leads, start=1)
                    if item.url
                ],
                receipts=[
                    ("internal", {
                        "document_id": item.id,
                        "url": item.url,
                        "existing_receipt_valid": (item.metadata or {}).get("acquisition_receipt_valid") is True,
                        "requires_canonical_revalidation": (item.metadata or {}).get("acquisition_receipt_valid") is not True,
                    })
                    for item in documents
                ],
                warning=self._internal_search_warning,
            )
        if decision.action_type in {"canonical_fetch", "browser_fetch"}:
            result = next((item for item in candidates if self._candidate_id(item) == decision.candidate_id), None)
            if result is None:
                return ToolOutcome(provider="policy", warning="Selected discovery candidate no longer exists.")
            fetched = self.browser.fetch(result.url) if decision.action_type == "browser_fetch" else self.fetcher.fetch(result.url)
            if isinstance(fetched, FetchFailure):
                policy_decision = "block" if fetched.error_type == "policy_blocked" else "allow_attempt"
                return ToolOutcome(
                    provider="browser" if decision.action_type == "browser_fetch" else "canonical",
                    receipts=[("retrieval_failure", {
                        "query": result.query,
                        "provider": result.provider,
                        "source_native_id": (result.metadata or {}).get("source_native_id")
                        or (result.metadata or {}).get("source_document_id"),
                        "rank": result.rank,
                        "canonical_url": result.url,
                        "publication_timestamp": (result.metadata or {}).get("published_at")
                        or (result.metadata or {}).get("published_date"),
                        "collection_timestamp": datetime.now(timezone.utc).isoformat(),
                        "source_policy": {
                            "decision": policy_decision,
                            "basis": fetched.message if fetched.error_type == "policy_blocked" else "public_url_policy_passed",
                            "citable": False,
                        },
                        "fetch_outcome": fetched.model_dump(mode="json"),
                        "evidence_status": "discovery_only",
                        "limitations": [f"Canonical retrieval failed: {fetched.error_type}."],
                    })],
                    warning=f"{fetched.error_type}: {fetched.message}",
                    retryable=fetched.retryable,
                    retry_after_seconds=fetched.retry_after_seconds,
                )
            document = self.normalizer.normalize(fetched, plan, result)
            source_policy = {
                "decision": "allow",
                "basis": "public_url_and_robots_checks_passed",
                "public_access_only": True,
                "citable": True,
            }
            fetch_outcome = {
                "status": "success",
                "http_status": fetched.status_code,
                "final_url": fetched.final_url,
            }
            document.metadata = {
                **(document.metadata or {}),
                "research_retrieval_lane": decision.retrieval_lane,
                "research_action_summary": decision.action_summary,
                "retrieval_transport": decision.action_type,
                "source_native_id": (result.metadata or {}).get("source_native_id")
                or (result.metadata or {}).get("source_document_id"),
                "canonical_url": fetched.final_url,
                "publication_timestamp": (
                    document.published_at.isoformat() if document.published_at else (result.metadata or {}).get("published_at")
                ),
                "collection_timestamp": fetched.fetched_at.isoformat(),
                "source_policy": source_policy,
                "fetch_outcome": fetch_outcome,
                "evidence_status": "canonical_evidence",
                "acquisition_receipt_valid": True,
            }
            return ToolOutcome(
                provider="browser" if decision.action_type == "browser_fetch" else "canonical",
                documents=[document],
                receipts=[("retrieval", {
                    "query": result.query,
                    "provider": result.provider,
                    "source_native_id": (result.metadata or {}).get("source_native_id")
                    or (result.metadata or {}).get("source_document_id"),
                    "rank": result.rank,
                    "url": fetched.url,
                    "canonical_url": fetched.final_url,
                    "publication_timestamp": document.published_at.isoformat() if document.published_at else None,
                    "collection_timestamp": fetched.fetched_at.isoformat(),
                    "content_type": fetched.content_type,
                    "document_id": document.id,
                    "parser_version": self.settings.DOCUMENT_PARSER_VERSION,
                    "source_policy": source_policy,
                    "fetch_outcome": fetch_outcome,
                    "evidence_status": "canonical_evidence",
                    "limitations": [],
                })],
            )
        return ToolOutcome(provider="assessment")

    def _internal_search(self, query: str) -> list[Document]:
        self._internal_search_warning = None
        if getattr(self.settings, "ENABLE_B5_RETRIEVAL", False):
            from services.b5_search import SearchService
            service = SearchService(settings=self.settings)
            documents = service.internal_search(query, limit=self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION)
            self._internal_search_warning = getattr(service, "warning", None)
            return documents
        if self.settings.ENABLE_POSTGRES_VECTOR_SEARCH and self.settings.DATABASE_URL:
            try:
                from services.postgres_corpus import DEFAULT_DIMENSION, DEFAULT_MODEL, PostgresCorpusStore
                from services.embedding_service import get_embedding_service

                embedding_service = get_embedding_service()
                query_embedding = embedding_service.embed_query(query)
                if not query_embedding or not any(query_embedding):
                    self._internal_search_warning = (
                        "PostgreSQL semantic search embedding unavailable; using legacy internal corpus."
                    )
                else:
                    results = PostgresCorpusStore(
                        self.settings.DATABASE_URL,
                        embedding_service=embedding_service,
                        expected_dimension=DEFAULT_DIMENSION,
                        expected_model=DEFAULT_MODEL,
                    ).search(
                        query_embedding,
                        limit=self.settings.POSTGRES_VECTOR_SEARCH_TOP_K,
                        model_name=embedding_service.model_name,
                    )
                    if results:
                        return [
                            item.document.model_copy(update={
                                "metadata": {
                                    **(item.document.metadata or {}),
                                    # The database-level eligibility bit is
                                    # authoritative. Discovery rows can never
                                    # be promoted by stale serialized metadata.
                                    "acquisition_receipt_valid": bool(item.citable),
                                    "retrieval_score": item.score,
                                    "retrieval_provider": "neon_pgvector",
                                    "retrieval_source_kind": item.source_kind,
                                }
                            })
                            for item in results
                        ]
                    self._internal_search_warning = (
                        "PostgreSQL semantic corpus is empty or has no usable embeddings; "
                        "using legacy internal corpus."
                    )
            except Exception as exc:
                logger.warning("PostgreSQL semantic search unavailable: %s", exc)
                self._internal_search_warning = (
                    "PostgreSQL semantic search unavailable; using legacy internal corpus."
                )
        elif self.settings.ENABLE_POSTGRES_VECTOR_SEARCH:
            self._internal_search_warning = (
                "PostgreSQL semantic search has no database URL; using legacy internal corpus."
            )
        terms = {term.lower() for term in query.split() if len(term) > 3}
        scored: list[tuple[int, Document]] = []
        for document in get_merged_documents(ALL_DOCUMENTS):
            haystack = f"{document.title} {document.snippet or ''} {document.text}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, document.model_copy(deep=True)))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[: self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION]]

    @staticmethod
    def _candidate_id(result: SearchResult) -> str:
        import hashlib
        return "candidate_" + hashlib.sha256(result.url.encode()).hexdigest()[:16]


def candidate_id(result: SearchResult) -> str:
    return ResearchToolRegistry._candidate_id(result)


def _discovery_receipt(result: SearchResult) -> dict:
    payload = result.model_dump(mode="json")
    metadata = payload.get("metadata") or {}
    return {
        **payload,
        "source_native_id": metadata.get("source_native_id") or metadata.get("source_document_id"),
        "canonical_url": metadata.get("canonical_url") or result.url,
        "publication_timestamp": metadata.get("published_at") or metadata.get("published_date"),
        "collection_timestamp": metadata.get("collected_at") or datetime.now(timezone.utc).isoformat(),
        "source_policy": metadata.get("source_policy") or {
            "decision": "allow_discovery",
            "basis": "approved_discovery_provider",
            "citable": False,
        },
        "fetch_outcome": {"status": "not_fetched"},
        "evidence_status": "discovery_only",
        "limitations": ["Discovery metadata and snippets are not citable canonical evidence."],
    }
