from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import urlparse

import httpx

from config import get_settings
from demo_data import ALL_DOCUMENTS
from models.document import Document
from models.investigation import FetchFailure, InvestigationPlan, RawPage, SearchResult
from models.research import ResearchActionDecision
from services.document_normalizer import DocumentNormalizer
from services.gdelt import GDELTIngestion
from services.hn_ingestion import HNIngestion
from services.ingestion import get_merged_documents
from services.search_provider import SearxngSearchProvider
from services.url_policy import PublicUrlPolicy


@dataclass
class ToolOutcome:
    provider: str
    candidates: list[SearchResult] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    receipts: list[tuple[str, dict]] = field(default_factory=list)
    warning: str | None = None
    retryable: bool = False


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
    def __init__(self) -> None:
        self.settings = get_settings()
        self.search = SearxngSearchProvider()
        self.gdelt = GDELTIngestion()
        self.hn = HNIngestion()
        self.fetcher = SafePageFetcher()
        self.browser = BrowserServiceClient()
        self.normalizer = DocumentNormalizer()

    def execute(
        self,
        decision: ResearchActionDecision,
        plan: InvestigationPlan,
        candidates: list[SearchResult],
    ) -> ToolOutcome:
        if decision.action_type == "web_search":
            results = self.search.search(
                decision.query or plan.query_text,
                plan.time_window,
                decision.requested_source_classes or plan.target_source_types,
                self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION,
            )
            diagnostics = self.search.last_diagnostics
            partial = bool(diagnostics.get("unresponsive_engines"))
            return ToolOutcome(
                provider="searxng",
                candidates=results,
                receipts=[
                    *[("discovery", item.model_dump(mode="json")) for item in results],
                    ("provider_status", diagnostics),
                ],
                warning="SearXNG returned partial engine failures." if partial else None,
            )
        if decision.action_type == "gdelt_search":
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=365 if plan.time_window.label == "all_time" else 31)
            documents = self.gdelt.fetch_articles(
                decision.query or plan.query_text, start, end,
                max_records=self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION,
            )
            return ToolOutcome(
                provider="gdelt",
                candidates=[
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
                        },
                    )
                    for index, item in enumerate(documents, start=1)
                    if item.url
                ],
                receipts=[("discovery", {"query": decision.query, "url": item.url, "title": item.title, "provider": "gdelt"}) for item in documents],
            )
        if decision.action_type == "hacker_news_search":
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=365)
            documents = self.hn.fetch_stories(
                decision.query or plan.query_text, start, end,
                num_results=self.settings.RESEARCH_SEARCH_RESULTS_PER_ACTION,
            )
            return ToolOutcome(
                provider="hacker_news",
                candidates=[
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
                        },
                    )
                    for index, item in enumerate(documents, start=1)
                    if item.url
                ],
                receipts=[("discovery", {"query": decision.query, "url": item.url, "title": item.title, "provider": "hacker_news"}) for item in documents],
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
            )
        if decision.action_type in {"canonical_fetch", "browser_fetch"}:
            result = next((item for item in candidates if self._candidate_id(item) == decision.candidate_id), None)
            if result is None:
                return ToolOutcome(provider="policy", warning="Selected discovery candidate no longer exists.")
            fetched = self.browser.fetch(result.url) if decision.action_type == "browser_fetch" else self.fetcher.fetch(result.url)
            if isinstance(fetched, FetchFailure):
                return ToolOutcome(
                    provider="browser" if decision.action_type == "browser_fetch" else "canonical",
                    receipts=[("retrieval_failure", fetched.model_dump(mode="json"))],
                    warning=f"{fetched.error_type}: {fetched.message}",
                    retryable=fetched.retryable,
                )
            document = self.normalizer.normalize(fetched, plan, result)
            document.metadata = {
                **(document.metadata or {}),
                "research_retrieval_lane": decision.retrieval_lane,
                "research_action_summary": decision.action_summary,
                "retrieval_transport": decision.action_type,
            }
            return ToolOutcome(
                provider="browser" if decision.action_type == "browser_fetch" else "canonical",
                documents=[document],
                receipts=[("retrieval", {
                    "url": fetched.url, "final_url": fetched.final_url, "status_code": fetched.status_code,
                    "content_type": fetched.content_type, "fetched_at": fetched.fetched_at.isoformat(),
                    "document_id": document.id, "parser_version": self.settings.DOCUMENT_PARSER_VERSION,
                })],
            )
        return ToolOutcome(provider="assessment")

    def _internal_search(self, query: str) -> list[Document]:
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
