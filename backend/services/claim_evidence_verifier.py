"""Versioned, fail-closed claim-to-evidence verification for A3.

The verifier intentionally keeps model output below deterministic policy.  A
semantic model may classify a span, but only a stored span from an independent,
usable source can make an affirmative report claim survive.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from statistics import mean
from typing import Literal

import tldextract

from agents.model_client import GeminiModelClient, GroqModelClient
from config import Settings, get_settings
from models.document import Document
from models.investigation import (
    ClaimCounterpointResult,
    ClaimDisposition,
    ClaimEvidenceVerification,
    ClaimEvidenceVerdict,
    ClaimVerificationRecord,
    ClaimVerificationResult,
    FinalReportClaim,
    FinalReportResult,
    InvestigationPlan,
    SourceIntelligence,
)
from services.embedding_service import EmbeddingService, get_embedding_service


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_WORD = re.compile(r"[a-z0-9]{3,}")
_TLD = tldextract.TLDExtract(suffix_list_urls=None)


class ClaimEvidenceVerifier:
    """Produce an A3 verification artifact without modifying legacy artifacts."""

    def __init__(self, *, settings: Settings | None = None, embeddings: EmbeddingService | None = None) -> None:
        self.settings = settings or get_settings()
        self.embeddings = embeddings or get_embedding_service()
        self._nli = None
        self._nli_unavailable = False
        self._embedding_unavailable = False

    def verify(
        self,
        investigation_id: str,
        plan: InvestigationPlan,
        documents: list[Document],
        report: FinalReportResult,
        counterpoints: ClaimCounterpointResult | None = None,
    ) -> ClaimVerificationResult:
        docs = {document.id: document for document in documents}
        intelligence = self._source_intelligence(documents)
        counter_by_claim = {pair.claim_id: pair for pair in (counterpoints.pairs if counterpoints else [])}
        records: list[ClaimVerificationRecord] = []
        provenance: set[str] = {f"embedding:{self.embeddings.model_name}", f"nli:{self.settings.CLAIM_VERIFIER_NLI_MODEL}"}
        limitations: list[str] = []

        for claim in report.key_claims:
            record, record_provenance, record_limitations = self._verify_claim(
                claim, docs, intelligence, counter_by_claim.get(claim.claim_id)
            )
            records.append(record)
            provenance.update(record_provenance)
            limitations.extend(record_limitations)

        if self._nli_unavailable:
            limitations.append(
                "The pinned local NLI model was unavailable; ambiguous and low-similarity evidence was withheld rather than affirmed."
            )
        confidence = mean(record.confidence_score for record in records) if records else 0.0
        return ClaimVerificationResult(
            investigation_id=investigation_id,
            verifier_version=self.settings.CLAIM_VERIFIER_VERSION,
            records=records,
            model_provenance=sorted(provenance),
            limitations=_dedupe(limitations),
            confidence_score=round(confidence, 3),
        )

    def _verify_claim(self, claim: FinalReportClaim, docs: dict[str, Document], intelligence: dict[str, SourceIntelligence], pair) -> tuple[ClaimVerificationRecord, set[str], list[str]]:
        provenance: set[str] = set()
        limitations: list[str] = []
        support_ids = [citation.document_id for citation in claim.citations]
        counter_ids = [citation.document_id for citation in claim.counter_citations]
        if pair is not None:
            support_ids.extend(pair.supporting_document_ids)
            counter_ids.extend(pair.counter_document_ids)

        support = self._evaluate_documents(claim.claim_id, claim.claim_text, "support", support_ids, docs, intelligence)
        counter = self._evaluate_documents(claim.claim_id, claim.claim_text, "counter", counter_ids, docs, intelligence)
        for item in [*support, *counter]:
            provenance.update(item.verifier_provenance)

        entailing = [item for item in support if item.nli_verdict == "entails" and item.confidence_score >= self.settings.CLAIM_VERIFIER_SUPPORT_THRESHOLD]
        contradicting = [item for item in counter if item.nli_verdict == "contradicts" and item.confidence_score >= self.settings.CLAIM_VERIFIER_SUPPORT_THRESHOLD]
        # A source can contradict a claim even if it was originally cited as support.
        contradicting.extend(item for item in support if item.nli_verdict == "contradicts" and item.confidence_score >= self.settings.CLAIM_VERIFIER_SUPPORT_THRESHOLD)
        support_groups = {item.source_intelligence.independence_group for item in entailing}
        counter_groups = {item.source_intelligence.independence_group for item in contradicting}

        reason_codes: list[str] = []
        if not support:
            reason_codes.append("missing_citable_evidence")
        if not entailing:
            reason_codes.append("no_verified_entailing_span")
        if len(support_groups) < len(entailing):
            reason_codes.append("syndication_not_counted_as_independent")
        if contradicting and entailing and support_groups != counter_groups:
            disposition: ClaimDisposition = "unresolved"
            reason_codes.append("independently_verified_conflict")
            summary = "Independent evidence supports competing interpretations; this claim is shown only as unresolved."
        elif contradicting:
            disposition = "contradicted"
            reason_codes.append("credible_contradictory_evidence")
            summary = "Credible contradictory evidence prevents this claim from being affirmed."
        elif entailing and support_groups:
            disposition = "supported"
            reason_codes.append("verified_entailing_span")
            summary = "A stored evidence span entails the claim and is counted from an independent source group."
        else:
            disposition = "withheld"
            summary = "The retrieved record does not contain a sufficiently verified entailing evidence span."

        if any(item.nli_verdict == "neutral" for item in [*support, *counter]):
            limitations.append(f"Claim '{claim.claim_id}' has neutral or unavailable semantic judgments that were not treated as support.")
        selected_support = sorted(entailing, key=lambda item: item.confidence_score, reverse=True)[:3]
        selected_counter = sorted(contradicting, key=lambda item: item.confidence_score, reverse=True)[:3]
        confidence_values = [item.confidence_score for item in [*selected_support, *selected_counter]]
        return (
            ClaimVerificationRecord(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                disposition=disposition,
                confidence_score=round(mean(confidence_values), 3) if confidence_values else 0.0,
                supporting_evidence=selected_support,
                contradicting_evidence=selected_counter,
                reason_codes=_dedupe(reason_codes),
                summary=summary,
            ),
            provenance,
            limitations,
        )

    def _evaluate_documents(self, claim_id: str, claim_text: str, side: Literal["support", "counter"], document_ids: list[str], docs: dict[str, Document], intelligence: dict[str, SourceIntelligence]) -> list[ClaimEvidenceVerification]:
        seen: set[str] = set()
        evaluations: list[ClaimEvidenceVerification] = []
        for doc_id in document_ids:
            if doc_id in seen or doc_id not in docs:
                continue
            seen.add(doc_id)
            document = docs[doc_id]
            for span, start, end in self._candidate_spans(claim_text, document):
                similarity = self._similarity(claim_text, span)
                verdict, nli_confidence, model_provenance = self._semantic_verdict(claim_text, span, similarity, side)
                confidence = min(0.99, (similarity * 0.55) + (nli_confidence * 0.45))
                if intelligence[doc_id].date_confidence < 0.5:
                    confidence *= 0.85
                evaluations.append(
                    ClaimEvidenceVerification(
                        claim_id=claim_id,
                        document_id=doc_id,
                        evidence_side=side,
                        evidence_span=span,
                        span_start=start,
                        span_end=end,
                        semantic_similarity=round(similarity, 3),
                        nli_verdict=verdict,
                        nli_confidence=round(nli_confidence, 3),
                        verifier_provenance=model_provenance,
                        source_intelligence=intelligence[doc_id],
                        confidence_score=round(confidence, 3),
                        reason_codes=self._reason_codes(similarity, verdict, intelligence[doc_id]),
                    )
                )
        return evaluations

    def _candidate_spans(self, claim: str, document: Document) -> list[tuple[str, int, int]]:
        text = document.text.strip() or document.snippet or document.title
        if not text:
            return []
        spans: list[tuple[str, int, int]] = []
        cursor = 0
        for sentence in _SENTENCE_BOUNDARY.split(text):
            start = text.find(sentence, cursor)
            cursor = start + len(sentence) if start >= 0 else cursor
            normalized = sentence.strip()
            if len(normalized) >= 24:
                spans.append((normalized[:900], max(start, 0), max(start, 0) + min(len(normalized), 900)))
        if not spans:
            spans = [(text[:900], 0, min(len(text), 900))]
        scored = sorted(
            enumerate(spans),
            key=lambda item: _token_overlap(claim, item[1][0]),
            reverse=True,
        )
        return [span for _index, span in scored[:self.settings.CLAIM_VERIFIER_MAX_SPANS_PER_DOCUMENT]]

    def _similarity(self, claim: str, span: str) -> float:
        if self._embedding_unavailable:
            return _token_overlap(claim, span)
        claim_embedding = self.embeddings.embed_query(claim)
        span_embedding = self.embeddings.embed_text(span)
        if not any(claim_embedding) or not any(span_embedding):
            self._embedding_unavailable = True
            return _token_overlap(claim, span)
        return self.embeddings.compute_similarity(claim_embedding, span_embedding)

    def _semantic_verdict(self, claim: str, span: str, similarity: float, side: Literal["support", "counter"]) -> tuple[ClaimEvidenceVerdict, float, list[str]]:
        local = self._local_nli(claim, span)
        if local is not None:
            verdict, confidence = local
            provenance = [f"local_nli:{self.settings.CLAIM_VERIFIER_NLI_MODEL}"]
        else:
            verdict, confidence = "neutral", 0.0
            provenance = ["local_nli:unavailable"]

        if self.settings.CLAIM_VERIFIER_ALLOW_HOSTED_JUDGE and self.settings.CLAIM_VERIFIER_AMBIGUOUS_LOW <= similarity < self.settings.CLAIM_VERIFIER_SUPPORT_THRESHOLD:
            hosted = self._hosted_judge(claim, span)
            if hosted is not None:
                verdict, confidence, provider = hosted
                provenance.append(f"hosted_second_opinion:{provider}")
        if verdict == "neutral" and local is not None and similarity >= self.settings.CLAIM_VERIFIER_SUPPORT_THRESHOLD:
            # Never infer contradiction from the retrieval lane. A strong local match can only be entailment when NLI agrees.
            return "neutral", confidence, provenance
        return verdict, confidence, provenance

    def _local_nli(self, claim: str, span: str) -> tuple[ClaimEvidenceVerdict, float] | None:
        if self._nli_unavailable:
            return None
        try:
            if self._nli is None:
                from sentence_transformers import CrossEncoder
                self._nli = CrossEncoder(
                    self.settings.CLAIM_VERIFIER_NLI_MODEL,
                    local_files_only=self.settings.CLAIM_VERIFIER_LOCAL_ONLY,
                )
            raw = self._nli.predict([(span, claim)])
            values = raw[0].tolist() if hasattr(raw[0], "tolist") else list(raw[0]) if hasattr(raw[0], "__iter__") else [float(raw[0])]
            if len(values) < 3:
                return None
            exp = [pow(2.718281828, float(value)) for value in values]
            total = sum(exp) or 1.0
            probabilities = [value / total for value in exp]
            label_index = max(range(len(probabilities)), key=probabilities.__getitem__)
            declared_label = str(getattr(self._nli.config, "id2label", {}).get(label_index, "neutral")).lower()
            if "entail" in declared_label:
                verdict: ClaimEvidenceVerdict = "entails"
            elif "contrad" in declared_label:
                verdict = "contradicts"
            else:
                verdict = "neutral"
            return verdict, float(probabilities[label_index])
        except Exception:
            self._nli_unavailable = True
            return None

    def _hosted_judge(self, claim: str, span: str) -> tuple[ClaimEvidenceVerdict, float, str] | None:
        client = None
        provider = ""
        try:
            if self.settings.GEMINI_API_KEY:
                client, provider = GeminiModelClient(), "gemini"
            elif self.settings.GROQ_API_KEY:
                client, provider = GroqModelClient(), "groq"
            if client is None:
                return None
            result = client.generate_json(
                "Classify only the relationship between the evidence and claim. Return JSON with verdict (entails, contradicts, neutral) and confidence (0..1).",
                f"Claim: {claim}\n\nEvidence span: {span}",
                "claim_evidence_verdict",
            )
            verdict = result.get("verdict")
            confidence = float(result.get("confidence", 0.0))
            if verdict not in {"entails", "contradicts", "neutral"} or not 0 <= confidence <= 1:
                return None
            return verdict, confidence, provider
        except Exception:
            return None

    def _source_intelligence(self, documents: list[Document]) -> dict[str, SourceIntelligence]:
        fingerprints: dict[str, str] = {}
        for document in documents:
            base = f"{_normalize(document.title)}:{_normalize((document.text or document.snippet or '')[:240])}"
            fingerprints[document.id] = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
        output: dict[str, SourceIntelligence] = {}
        for document in documents:
            domain = _domain(document.url)
            role, role_confidence, role_reasons = _source_role(document)
            group = document.duplicate_of_doc_id or fingerprints[document.id]
            if role == "reposting":
                group = document.duplicate_of_doc_id or fingerprints[document.id]
            date_confidence = 1.0 if document.published_at else (0.5 if document.collected_at else 0.0)
            reasons = [*role_reasons, "published_date_present" if document.published_at else "published_date_missing"]
            output[document.id] = SourceIntelligence(
                document_id=document.id,
                registrable_domain=domain,
                independence_group=group,
                source_role=role,
                role_confidence=role_confidence,
                date_confidence=date_confidence,
                reasons=reasons,
            )
        return output

    def _reason_codes(self, similarity: float, verdict: ClaimEvidenceVerdict, source: SourceIntelligence) -> list[str]:
        reasons = [f"nli_{verdict}"]
        reasons.append("semantic_match" if similarity >= self.settings.CLAIM_VERIFIER_SUPPORT_THRESHOLD else "semantic_match_below_threshold")
        if source.date_confidence < 0.5:
            reasons.append("weak_publication_date")
        if source.source_role == "reposting":
            reasons.append("reposting_not_independent")
        return reasons


def apply_claim_verification(report: FinalReportResult, verification: ClaimVerificationResult) -> FinalReportResult:
    """Attach decisions, remove unsafe claims, and generate bounded report language."""
    by_id = {record.claim_id: record for record in verification.records}
    retained: list[FinalReportClaim] = []
    for claim in report.key_claims:
        record = by_id.get(claim.claim_id)
        if record is None or record.disposition in {"withheld", "contradicted"}:
            continue
        caveats = list(claim.caveats)
        if record.disposition == "unresolved":
            caveats.append("Independent evidence supports competing interpretations; this is not a resolved conclusion.")
        retained.append(claim.model_copy(update={"verification": record, "caveats": _dedupe(caveats)}))
    supported = [claim.claim_text for claim in retained if claim.verification and claim.verification.disposition == "supported"]
    unresolved = [claim.claim_text for claim in retained if claim.verification and claim.verification.disposition == "unresolved"]
    executive = " ".join(supported) if supported else "The retrieved evidence did not support an affirmative public conclusion."
    if unresolved:
        executive = f"{executive} Competing evidence remains unresolved and is presented with its limitations."
    sections = report.sections.model_copy(update={
        "executive_summary": executive,
        "observed_facts": " ".join(claim.claim_text for claim in retained if claim.claim_type == "observed_fact") or "No observed fact met the A3 evidence threshold.",
        "reasonable_inferences": " ".join(claim.claim_text for claim in retained if claim.claim_type == "inference" and claim.verification and claim.verification.disposition == "supported") or "No inference met the A3 evidence threshold.",
        "counter_narrative_summary": " ".join(unresolved) if unresolved else report.sections.counter_narrative_summary,
        "limitations": " ".join(_dedupe([report.sections.limitations, *verification.limitations, "A3 verification only establishes support within the retrieved evidence packet."])),
    })
    limitations = _dedupe([*report.limitations, *verification.limitations])
    checks = list(report.recommended_human_checks)
    if unresolved:
        checks.append("Review the independently verified competing evidence before drawing a conclusion.")
    return report.model_copy(update={
        "key_claims": retained,
        "sections": sections,
        "report_summary": executive,
        "limitations": limitations,
        "recommended_human_checks": _dedupe(checks),
        "verifier_version": verification.verifier_version,
    })


def _source_role(document: Document) -> tuple[str, float, list[str]]:
    profile = document.source_profile
    if profile and profile.institution_kind == "official":
        return "official", 0.95, ["source_profile_official"]
    if profile and profile.content_form == "original_reporting":
        return "original_reporting", 0.85, ["source_profile_original_reporting"]
    if document.duplicate_of_doc_id or (profile and profile.content_form == "reposting"):
        return "reposting", 0.9, ["duplicate_or_reposting_signal"]
    if document.source_type == "speech_transcript":
        return "primary", 0.75, ["transcript_source_type"]
    if document.source_type == "commentary" or (profile and profile.content_form == "opinion"):
        return "commentary", 0.75, ["commentary_source_type"]
    if document.source_type == "forum" or (profile and profile.institution_kind == "community"):
        return "community", 0.75, ["community_source_type"]
    return "unknown", 0.35, ["insufficient_source_classification"]


def _domain(url: str) -> str | None:
    parsed = _TLD(url)
    value = ".".join(part for part in (parsed.domain, parsed.suffix) if part)
    return value or None


def _normalize(value: str) -> str:
    return " ".join(_WORD.findall(value.lower()))


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(_WORD.findall(left.lower()))
    right_tokens = set(_WORD.findall(right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
