from __future__ import annotations

from urllib.parse import urlparse

from models.document import Document, SourceProfile

_DOMAIN_REGISTRY: dict[str, dict[str, str]] = {
    "reuters.com": {
        "institution_kind": "media",
        "content_form": "original_reporting",
        "ideology": "center",
        "classification_method": "registry",
        "classification_confidence": "high",
    },
    "apnews.com": {
        "institution_kind": "media",
        "content_form": "original_reporting",
        "ideology": "center",
        "classification_method": "registry",
        "classification_confidence": "high",
    },
    "statehouse.gov": {
        "institution_kind": "official",
        "content_form": "transcript",
        "ideology": "unknown",
        "classification_method": "registry",
        "classification_confidence": "high",
    },
    "whitehouse.gov": {
        "institution_kind": "official",
        "content_form": "transcript",
        "ideology": "unknown",
        "classification_method": "registry",
        "classification_confidence": "high",
    },
}


class SourceProfileEnricher:
    def enrich_documents(self, documents: list[Document]) -> list[Document]:
        return [self.enrich_document(document) for document in documents]

    def enrich_document(self, document: Document) -> Document:
        profile = self._classify(document)
        return document.model_copy(update={"source_profile": profile})

    def _classify(self, document: Document) -> SourceProfile:
        domain = self._extract_domain(document.url, document.source_name)
        registry_profile = self._lookup_registry(domain)
        if registry_profile is not None:
            return registry_profile

        institution_kind = "unknown"
        content_form = "unknown"
        confidence = "low"

        if document.source_type in {"speech_transcript", "government_record"}:
            institution_kind = "official"
            content_form = "transcript" if document.source_type == "speech_transcript" else "unknown"
            confidence = "high"
        elif document.source_type in {"national_news", "local_news"}:
            institution_kind = "media"
            content_form = "original_reporting"
            confidence = "high"
        elif document.source_type == "commentary":
            institution_kind = "media" if self._looks_like_media(domain) else "independent"
            content_form = "opinion"
            confidence = "medium"
        elif document.source_type == "blog":
            institution_kind = "independent"
            content_form = "opinion"
            confidence = "medium"
        elif document.source_type == "forum":
            institution_kind = "community"
            content_form = "community_post"
            confidence = "high"

        return SourceProfile(
            institution_kind=institution_kind,
            content_form=content_form,
            ideology="unknown",
            classification_method="heuristic" if institution_kind != "unknown" or content_form != "unknown" else "unknown",
            classification_confidence=confidence,
        )

    def _lookup_registry(self, domain: str) -> SourceProfile | None:
        record = _DOMAIN_REGISTRY.get(domain)
        if record is None:
            return None
        return SourceProfile.model_validate(record)

    def _extract_domain(self, url: str, source_name: str) -> str:
        parsed = urlparse(url)
        domain = parsed.netloc or source_name
        return domain.lower().removeprefix("www.")

    def _looks_like_media(self, domain: str) -> bool:
        return any(token in domain for token in ("news", "times", "post", "press", "desk", "gazette", "journal"))
