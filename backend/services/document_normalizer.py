from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import re
from urllib.parse import urlparse

from models.document import Document
from models.investigation import InvestigationPlan, RawPage, SearchResult

_BLOG_DOMAINS = {
    "substack.com", "medium.com", "wordpress.com", "blogspot.com",
    "tumblr.com", "ghost.io", "beehiiv.com",
}
_NATIONAL_DOMAINS = {
    "nytimes.com", "washingtonpost.com", "reuters.com", "apnews.com",
    "cnn.com", "foxnews.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com",
    "bbc.com", "bbc.co.uk", "politico.com", "thehill.com", "axios.com",
    "theguardian.com", "bloomberg.com", "wsj.com", "ft.com",
    "npr.org", "pbs.org", "vox.com", "slate.com", "salon.com",
    "nationalreview.com", "theatlantic.com", "newyorker.com",
}
_LOCAL_KEYWORDS = {
    "gazette", "herald", "tribune", "daily", "observer", "register",
    "dispatch", "courier", "chronicle", "journal", "bulletin", "sentinel",
    "beacon", "pilot", "record", "times-", "-times", "patch.com",
}
_COMMENTARY_DOMAINS = {
    "reason.com", "thedispatch.com", "commondreams.org", "motherjones.com",
    "jacobin.com", "federalist.com", "breitbart.com", "dailykos.com",
}
_TAG_RE = re.compile(r"<[^>]+>")
_PHRASE_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]{2,}")


class DocumentNormalizer:
    def normalize(
        self,
        raw_page: RawPage,
        plan: InvestigationPlan,
        search_result: SearchResult,
    ) -> Document:
        domain = urlparse(raw_page.final_url or raw_page.url).netloc.lower()
        title = self._extract_title(raw_page.html) or search_result.title
        text = self._extract_text(raw_page.html) or search_result.snippet or title
        snippet = self._build_snippet(search_result.snippet or text)
        author = self._extract_meta(raw_page.html, "author", "article:author")
        page_published_at = self._extract_published_at(raw_page.html)
        provider_published_at = self._provider_published_at(search_result)
        published_at = page_published_at or provider_published_at
        source_type = self._classify_source(domain)
        source_name = domain or search_result.provider
        from services.source_references import extract_source_references
        references, reference_limitations = extract_source_references(raw_page.html, raw_page.final_url or raw_page.url, text)

        return Document(
            id=self._doc_id(raw_page.final_url or raw_page.url),
            source_id=f"domain:{domain}" if domain else None,
            source_name=source_name,
            source_type=source_type,
            url=raw_page.final_url or raw_page.url,
            title=title,
            author=author,
            published_at=published_at,
            collected_at=raw_page.fetched_at,
            text=text,
            snippet=snippet,
            language=self._extract_lang(raw_page.html),
            content_type=self._infer_content_type(source_type),
            geographic_scope=self._infer_geographic_scope(source_type, domain),
            entities=self._extract_entities(title, text, plan),
            phrases=self._extract_phrases(title, search_result, plan),
            references=references,
            metadata={
                "provider": search_result.provider,
                "search_query": search_result.query,
                "search_rank": search_result.rank,
                "provider_score": search_result.provider_score,
                "content_type_header": raw_page.content_type,
                "date_source": "page_meta" if page_published_at else "provider_metadata" if provider_published_at else "unknown",
                "source_native_id": search_result.metadata.get("source_native_id")
                or search_result.metadata.get("source_document_id"),
                "canonical_url": raw_page.final_url or raw_page.url,
                "publication_timestamp": published_at.isoformat() if published_at else None,
                "collection_timestamp": raw_page.fetched_at.isoformat(),
                "discovery_metadata": dict(search_result.metadata),
                "reference_extraction": "b5-links-v1",
                "reference_limitations": reference_limitations,
            },
        )

    def _provider_published_at(self, search_result: SearchResult) -> datetime | None:
        metadata = search_result.metadata or {}
        for key in ("published_at", "published_date", "publication_timestamp"):
            candidate = metadata.get(key)
            if not candidate:
                continue
            try:
                return self._to_utc(datetime.fromisoformat(str(candidate).replace("Z", "+00:00")))
            except ValueError:
                continue
        return None

    def _doc_id(self, url: str) -> str:
        return "web_" + hashlib.md5(url.encode()).hexdigest()[:12]

    def _extract_title(self, html_text: str) -> str | None:
        og = self._extract_meta(html_text, "og:title")
        if og:
            return og.strip()
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        if match:
            return html.unescape(match.group(1)).strip()
        return None

    def _extract_meta(self, html_text: str, *names: str) -> str | None:
        for name in names:
            match = re.search(
                rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\'](.*?)["\']',
                html_text,
                re.IGNORECASE | re.DOTALL,
            )
            if match:
                return html.unescape(match.group(1)).strip()
        return None

    def _extract_published_at(self, html_text: str) -> datetime | None:
        candidates = [
            self._extract_meta(html_text, "article:published_time", "pubdate", "date", "dc.date"),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                return self._to_utc(parsed)
            except ValueError:
                continue
        return None

    def _to_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _extract_lang(self, html_text: str) -> str | None:
        match = re.search(r"<html[^>]+lang=[\"']([a-zA-Z\-]+)[\"']", html_text, re.IGNORECASE)
        if match:
            return match.group(1).lower()
        return None

    def _extract_text(self, html_text: str) -> str:
        cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", html_text)
        cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)
        cleaned = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", cleaned)
        cleaned = _TAG_RE.sub(" ", cleaned)
        cleaned = html.unescape(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:20000]

    def _build_snippet(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()[:320]

    def _extract_entities(self, title: str, text: str, plan: InvestigationPlan) -> list[str]:
        entities: list[str] = []
        for token in re.findall(r"\b[A-Z][A-Za-z0-9\-]{2,}\b", title):
            if token not in entities:
                entities.append(token)
        for token in plan.entities:
            lowered = token.lower()
            if lowered not in {e.lower() for e in entities}:
                entities.append(token)
        return entities[:12]

    def _extract_phrases(self, title: str, search_result: SearchResult, plan: InvestigationPlan) -> list[str]:
        phrases: list[str] = []
        if plan.canonical_phrase and len(plan.canonical_phrase.split()) >= 2:
            phrases.append(plan.canonical_phrase.lower())
        words = _PHRASE_TOKEN_RE.findall(title.lower())
        for n in (2, 3):
            for index in range(len(words) - n + 1):
                candidate_words = words[index : index + n]
                if len(set(candidate_words)) == 1:
                    continue
                phrases.append(" ".join(candidate_words))
        deduped: list[str] = []
        seen: set[str] = set()
        for phrase in phrases:
            phrase = phrase.strip()
            if not phrase or phrase in seen:
                continue
            seen.add(phrase)
            deduped.append(phrase)
        return deduped[:8]

    def _classify_source(self, domain: str) -> str:
        if domain.endswith(".gov") or domain == "federalregister.gov":
            return "government_record"
        if any(domain.endswith(blog) or blog in domain for blog in _BLOG_DOMAINS):
            return "blog"
        if domain in _COMMENTARY_DOMAINS:
            return "commentary"
        if domain in _NATIONAL_DOMAINS:
            return "national_news"
        if any(keyword in domain for keyword in _LOCAL_KEYWORDS):
            return "local_news"
        return "national_news"

    def _infer_content_type(self, source_type: str) -> str:
        if source_type == "commentary":
            return "opinion"
        if source_type == "blog":
            return "analysis"
        if source_type == "government_record":
            return "public_record"
        return "article"

    def _infer_geographic_scope(self, source_type: str, domain: str) -> str:
        if source_type == "local_news":
            return "local"
        if source_type == "government_record":
            return "national"
        if source_type in {"national_news", "commentary"}:
            return "national" if domain.endswith(".com") or domain.endswith(".org") else "unknown"
        return "unknown"
