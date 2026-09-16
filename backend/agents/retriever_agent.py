from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import re

from config import get_settings
from demo_data import ALL_DOCUMENTS
from models.document import Document
from models.investigation import (
    CoverageSummary,
    DuplicateCandidate,
    InvestigationPlan,
    RetrievalDocumentAnnotation,
    RetrievalLane,
    RetrievalResult,
    RetrievalRound,
    SearchResult,
)
from services.document_normalizer import DocumentNormalizer
from services.document_store import live_store
from services.ingestion import get_merged_documents
from services.investigation_repository import InvestigationRepository
from services.page_fetcher import HttpPageFetcher
from services.redis_vector_store import get_redis_vector_store
from services.search_provider import SearchProvider, build_search_provider
from services.source_profile_enricher import SourceProfileEnricher

logger = logging.getLogger(__name__)

_COUNTER_SIGNAL_TERMS = {
    "however", "but", "despite", "critics", "supporters", "opponents",
    "denies", "refutes", "debunks", "fact check", "counter", "rebuttal",
}
_TIMELINE_SOURCE_TYPES = {"local_news", "forum", "speech_transcript", "blog"}
_SOURCE_DIVERSITY_OUTPUT = "source_diversity"
_COUNTER_OUTPUT = "counter_narratives"
_TIMELINE_OUTPUT = "timeline"
_GRAPH_OUTPUT = "graph"
_RECEIPTS_OUTPUT = "receipts"
_OFFICIAL_SOURCE_TYPES = {"official_statement", "speech_transcript", "government_record"}


@dataclass
class RetrievalPreview:
    documents: list[Document]
    coverage_summary: CoverageSummary
    warnings: list[str]
    search_rounds: list[RetrievalRound]


@dataclass
class _QuerySpec:
    lane: RetrievalLane
    query: str


class RetrieverAgent:
    def __init__(
        self,
        repository: InvestigationRepository,
        search_provider: SearchProvider | None = None,
        page_fetcher: HttpPageFetcher | None = None,
        normalizer: DocumentNormalizer | None = None,
        source_profile_enricher: SourceProfileEnricher | None = None,
    ) -> None:
        self._settings = get_settings()
        self._repository = repository
        self._provider = search_provider
        if self._provider is None and not self._settings.DEMO_MODE:
            self._provider = build_search_provider()
        self._fetcher = page_fetcher or HttpPageFetcher()
        self._normalizer = normalizer or DocumentNormalizer()
        self._vector_store = get_redis_vector_store()
        self._source_profile_enricher = source_profile_enricher or SourceProfileEnricher()

    def retrieve(
        self,
        investigation_id: str,
        plan: InvestigationPlan,
        max_rounds: int | None = None,
        force_refresh: bool = False,
        *,
        lanes: list[RetrievalLane] | None = None,
        pass_number: int = 1,
        follow_up_queries: dict[RetrievalLane, list[str]] | None = None,
        prior_result: RetrievalResult | None = None,
        prior_documents: list[Document] | None = None,
    ) -> RetrievalResult:
        use_cached = (
            not force_refresh
            and lanes is None
            and follow_up_queries is None
            and prior_result is None
            and prior_documents is None
            and pass_number == 1
        )
        if use_cached:
            cached = self._repository.get_retrieval_result(investigation_id)
            if cached is not None:
                return cached.model_copy(update={"cached": True})

        active_lanes = lanes or list(plan.retrieval_lanes)
        if self._settings.DEMO_MODE:
            return self._retrieve_from_local_corpus(
                investigation_id=investigation_id,
                plan=plan,
                lanes=active_lanes,
                pass_number=pass_number,
                follow_up_queries=follow_up_queries or {},
                prior_result=prior_result,
                prior_documents=prior_documents,
            )

        effective_max_rounds = max_rounds or self._settings.RETRIEVER_MAX_ROUNDS
        all_documents = [doc.model_copy(deep=True) for doc in (prior_documents or [])]
        prior_annotations = {
            item.document_id: item for item in ((prior_result.document_annotations if prior_result else []) or [])
        }
        all_rounds = list(prior_result.search_rounds) if prior_result else []
        warnings = list(prior_result.warnings) if prior_result else []
        seen_urls = {self._normalize_url(doc.url) for doc in all_documents}
        seen_doc_ids = {doc.id for doc in all_documents}
        search_results_by_round: dict[int, list[dict]] = {}
        previous_doc_count = len(all_documents)
        start_round_number = len(all_rounds) + 1

        for step in range(effective_max_rounds):
            round_number = start_round_number + step
            query_specs = self._build_round_query_specs(
                plan,
                round_index=step + 1,
                documents=all_documents,
                lanes=active_lanes,
                follow_up_queries=follow_up_queries or {},
            )
            if not query_specs:
                break

            round_warnings: list[str] = []
            round_results = self._run_search_round(query_specs, plan, round_warnings)
            search_results_by_round[round_number] = [result.model_dump(mode="json") for _spec, result in round_results]

            fetched_pages = 0
            new_documents = 0
            accepted_documents = 0
            lane_for_doc_id: dict[str, tuple[RetrievalLane, str]] = {}

            for spec, result in round_results:
                normalized_url = self._normalize_url(result.url)
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)

                fetched = self._fetcher.fetch(result.url)
                if not hasattr(fetched, "html"):
                    round_warnings.append(f"{result.url}: {fetched.error_type}")
                    continue
                fetched_pages += 1

                document = self._normalizer.normalize(fetched, plan, result)
                if document.id in seen_doc_ids:
                    continue
                lane_for_doc_id[document.id] = (spec.lane, spec.query)
                seen_doc_ids.add(document.id)
                all_documents.append(document)
                accepted_documents += 1
                new_documents += 1

            scored_documents = self._score_documents(all_documents, plan)
            all_documents = [doc for doc, _ in scored_documents]
            all_documents = self._source_profile_enricher.enrich_documents(all_documents)
            scored_documents = [
                (all_documents[index], score) for index, (_doc, score) in enumerate(scored_documents)
            ]
            duplicates, duplicate_clusters = self._detect_duplicates(all_documents)
            annotations = self._build_document_annotations(
                scored_documents=scored_documents,
                previous_annotations=prior_annotations,
                lane_for_doc_id=lane_for_doc_id,
                pass_number=pass_number,
                duplicate_clusters=duplicate_clusters,
            )
            coverage = self._build_coverage_summary(all_documents, all_rounds, round_number, plan, annotations)
            lane_label = query_specs[0].lane if len({spec.lane for spec in query_specs}) == 1 else None
            round_obj = RetrievalRound(
                round_number=round_number,
                queries=[spec.query for spec in query_specs],
                provider=self._provider.name,
                lane=lane_label,
                pass_number=pass_number,
                discovered_results=len(round_results),
                fetched_pages=fetched_pages,
                accepted_documents=accepted_documents,
                new_documents=new_documents,
                warnings=round_warnings,
            )
            all_rounds.append(round_obj)
            warnings.extend(round_warnings)
            prior_annotations = {item.document_id: item for item in annotations}
            if self._should_stop(
                all_documents=all_documents,
                coverage=coverage,
                new_documents=new_documents,
                previous_doc_count=previous_doc_count,
                round_number=step + 1,
                max_rounds=effective_max_rounds,
                plan=plan,
                lanes=active_lanes,
            ):
                break
            previous_doc_count = len(all_documents)

        scored_documents = self._score_documents(all_documents, plan)
        all_documents = [doc for doc, _ in scored_documents]
        all_documents = self._source_profile_enricher.enrich_documents(all_documents)
        scored_documents = [(all_documents[index], score) for index, (_doc, score) in enumerate(scored_documents)]
        duplicates, duplicate_clusters = self._detect_duplicates(all_documents)
        annotations = self._build_document_annotations(
            scored_documents=scored_documents,
            previous_annotations=prior_annotations,
            lane_for_doc_id={},
            pass_number=pass_number,
            duplicate_clusters=duplicate_clusters,
        )
        coverage = self._build_coverage_summary(all_documents, all_rounds, len(all_rounds), plan, annotations)
        confidence = self._coverage_confidence(coverage)
        categorized = self._categorize_documents(scored_documents, plan)

        result = RetrievalResult(
            investigation_id=investigation_id,
            plan_snapshot=plan,
            retrieved_document_ids=[doc.id for doc in all_documents],
            high_relevance_document_ids=categorized["high_relevance"],
            main_narrative_document_ids=categorized["main_narrative"],
            counter_narrative_candidate_ids=categorized["counter_candidates"],
            context_document_ids=categorized["context"],
            document_annotations=annotations,
            possible_duplicate_pairs=duplicates,
            search_rounds=all_rounds,
            warnings=warnings,
            coverage_summary=coverage,
            evidence_coverage_confidence=confidence,
        )
        self._repository.save_retrieval_result(result, all_documents)
        for round_number, search_results in search_results_by_round.items():
            self._repository.save_search_results(investigation_id, round_number, search_results)
        live_store.save_batch(all_documents)

        if self._vector_store and all_documents:
            try:
                indexed = self._vector_store.add_documents_batch(all_documents[:20])
                logger.info("Indexed %d documents in Redis vector store", indexed)
            except Exception as exc:
                logger.warning("Redis document indexing failed: %s", exc)

        return result

    def expand_candidate(
        self,
        plan: InvestigationPlan,
        max_rounds: int | None = None,
    ) -> RetrievalPreview:
        if self._settings.DEMO_MODE:
            return self._preview_from_local_corpus(plan)

        effective_max_rounds = max_rounds or min(2, self._settings.RETRIEVER_MAX_ROUNDS)
        if self._provider is None:
            return RetrievalPreview(
                documents=[],
                coverage_summary=CoverageSummary(),
                warnings=["no_search_provider_configured"],
                search_rounds=[],
            )

        query_specs = self._build_round_query_specs(
            plan,
            round_index=1,
            documents=[],
            lanes=list(plan.retrieval_lanes),
            follow_up_queries={},
        )
        round_results = self._run_search_round(query_specs[: effective_max_rounds * 4], plan, [])
        documents: list[Document] = []
        seen_ids: set[str] = set()
        for spec, result in round_results:
            fetched = self._fetcher.fetch(result.url)
            if not hasattr(fetched, "html"):
                continue
            document = self._normalizer.normalize(fetched, plan, result)
            if document.id in seen_ids:
                continue
            seen_ids.add(document.id)
            documents.append(document)

        scored_documents = self._score_documents(documents, plan)
        selected_documents = [document for document, score in scored_documents if score > 0][:12]
        if not selected_documents and scored_documents:
            selected_documents = [document for document, _score in scored_documents[:8]]
        selected_documents = self._source_profile_enricher.enrich_documents(selected_documents)
        annotations = self._build_document_annotations(
            scored_documents=[(doc, doc.metadata.get("retrieval_score", 0.0)) for doc in selected_documents],
            previous_annotations={},
            lane_for_doc_id={},
            pass_number=1,
            duplicate_clusters={},
        )
        coverage = self._build_coverage_summary(selected_documents, [], 1, plan, annotations)
        return RetrievalPreview(
            documents=selected_documents,
            coverage_summary=coverage,
            warnings=[],
            search_rounds=[],
        )

    def _retrieve_from_local_corpus(
        self,
        investigation_id: str,
        plan: InvestigationPlan,
        *,
        lanes: list[RetrievalLane],
        pass_number: int,
        follow_up_queries: dict[RetrievalLane, list[str]],
        prior_result: RetrievalResult | None,
        prior_documents: list[Document] | None,
    ) -> RetrievalResult:
        corpus = [
            document.model_copy(deep=True)
            for document in get_merged_documents(ALL_DOCUMENTS)
        ]
        scored_documents = self._score_documents(corpus, plan)

        semantic_scores: dict[str, float] = {}
        if self._vector_store:
            try:
                queries = [plan.query_text] + list(plan.semantic_queries[:2])
                for q in queries:
                    for result in self._vector_store.semantic_search(q, limit=10):
                        doc_id = result.id.replace("rq:doc:", "")
                        semantic_scores[doc_id] = max(semantic_scores.get(doc_id, 0.0), result.score)
                if semantic_scores:
                    scored_documents = [
                        (doc, score + semantic_scores.get(doc.id, 0.0) * 3.0)
                        for doc, score in scored_documents
                    ]
                    scored_documents.sort(key=lambda item: item[1], reverse=True)
            except Exception as exc:
                logger.warning("Redis semantic search unavailable, using keyword scoring: %s", exc)

        matched_documents = [(document, score) for document, score in scored_documents if score > 0]
        if not matched_documents:
            matched_documents = scored_documents[:8]
            warnings = [
                "demo_mode_local_corpus:no strong lexical match found; using top local corpus documents",
            ]
        else:
            warnings = ["demo_mode_local_corpus:retrieval used the seeded/local document corpus"]

        selected_pairs = matched_documents[:12]
        selected_documents = [document for document, _score in selected_pairs]
        prior_doc_map = {doc.id: doc.model_copy(deep=True) for doc in (prior_documents or [])}
        for document in selected_documents:
            prior_doc_map.setdefault(document.id, document)
        merged_documents = list(prior_doc_map.values())
        merged_documents = self._source_profile_enricher.enrich_documents(merged_documents)
        duplicates, duplicate_clusters = self._detect_duplicates(merged_documents)
        query_specs = self._build_round_query_specs(
            plan,
            round_index=1,
            documents=prior_documents or [],
            lanes=lanes,
            follow_up_queries=follow_up_queries,
        )
        annotations = self._build_document_annotations(
            scored_documents=[(doc, doc.metadata.get("retrieval_score", 0.0)) for doc in merged_documents],
            previous_annotations={
                item.document_id: item for item in ((prior_result.document_annotations if prior_result else []) or [])
            },
            lane_for_doc_id={
                document.id: (
                    query_specs[index % len(query_specs)].lane if query_specs else lanes[0],
                    query_specs[index % len(query_specs)].query if query_specs else plan.query_text,
                )
                for index, document in enumerate(selected_documents)
            },
            pass_number=pass_number,
            duplicate_clusters=duplicate_clusters,
        )
        prior_rounds = list(prior_result.search_rounds) if prior_result else []
        next_round = len(prior_rounds) + 1
        round_obj = RetrievalRound(
            round_number=next_round,
            queries=[spec.query for spec in query_specs] or [plan.query_text],
            provider="local_demo",
            lane=query_specs[0].lane if len({spec.lane for spec in query_specs}) == 1 and query_specs else None,
            pass_number=pass_number,
            discovered_results=len(selected_pairs),
            fetched_pages=len(selected_pairs),
            accepted_documents=len(selected_pairs),
            new_documents=max(0, len(merged_documents) - len(prior_doc_map) + len(selected_documents) - len(selected_documents)),
            warnings=warnings,
        )
        coverage = self._build_coverage_summary(merged_documents, prior_rounds, next_round, plan, annotations)
        confidence = self._coverage_confidence(coverage)
        categorized = self._categorize_documents(
            [(doc, doc.metadata.get("retrieval_score", 0.0)) for doc in merged_documents],
            plan,
        )
        search_results = [
            SearchResult(
                query=(query_specs[index % len(query_specs)].query if query_specs else plan.query_text),
                title=document.title,
                url=document.url,
                snippet=document.snippet,
                rank=index,
                provider="local_demo",
                provider_score=score,
                metadata={
                    "source_name": document.source_name,
                    "retrieval_lane": query_specs[index % len(query_specs)].lane if query_specs else lanes[0],
                },
            ).model_dump(mode="json")
            for index, (document, score) in enumerate(selected_pairs, start=1)
        ]
        result = RetrievalResult(
            investigation_id=investigation_id,
            plan_snapshot=plan,
            retrieved_document_ids=[doc.id for doc in merged_documents],
            high_relevance_document_ids=categorized["high_relevance"],
            main_narrative_document_ids=categorized["main_narrative"],
            counter_narrative_candidate_ids=categorized["counter_candidates"],
            context_document_ids=categorized["context"],
            document_annotations=annotations,
            possible_duplicate_pairs=duplicates,
            search_rounds=[*prior_rounds, round_obj],
            warnings=[*(prior_result.warnings if prior_result else []), *warnings],
            coverage_summary=coverage,
            evidence_coverage_confidence=confidence,
        )
        self._repository.save_retrieval_result(result, merged_documents)
        self._repository.save_search_results(investigation_id, next_round, search_results)
        live_store.save_batch(merged_documents)
        return result

    def _preview_from_local_corpus(self, plan: InvestigationPlan) -> RetrievalPreview:
        corpus = [
            document.model_copy(deep=True)
            for document in get_merged_documents(ALL_DOCUMENTS)
        ]
        scored_documents = self._score_documents(corpus, plan)
        matched_documents = [
            (document, score) for document, score in scored_documents if score > 0
        ]
        if not matched_documents:
            matched_documents = scored_documents[:8]
            warnings = [
                "demo_mode_local_corpus:no strong lexical match found; using top local corpus documents",
            ]
        else:
            warnings = ["demo_mode_local_corpus:retrieval used the seeded/local document corpus"]

        selected_documents = [document for document, _score in matched_documents[:12]]
        selected_documents = self._source_profile_enricher.enrich_documents(selected_documents)
        annotations = self._build_document_annotations(
            scored_documents=[(doc, doc.metadata.get("retrieval_score", 0.0)) for doc in selected_documents],
            previous_annotations={},
            lane_for_doc_id={},
            pass_number=1,
            duplicate_clusters={},
        )
        coverage = self._build_coverage_summary(selected_documents, [], 1, plan, annotations)
        return RetrievalPreview(
            documents=selected_documents,
            coverage_summary=coverage,
            warnings=warnings,
            search_rounds=[],
        )

    def _build_round_queries(
        self,
        plan: InvestigationPlan,
        round_number: int,
        documents: list[Document],
        lane: RetrievalLane | None = None,
        follow_up_queries: dict[RetrievalLane, list[str]] | None = None,
    ) -> list[str]:
        queries = [spec.query for spec in self._build_round_query_specs(
            plan,
            round_index=round_number,
            documents=documents,
            lanes=[lane] if lane is not None else list(plan.retrieval_lanes),
            follow_up_queries=follow_up_queries or {},
        )]
        if lane is None and round_number == 2 and plan.canonical_phrase:
            if _COUNTER_OUTPUT in plan.requested_outputs:
                queries.extend(
                    [
                        f"{plan.canonical_phrase} counter narrative",
                        f"{plan.canonical_phrase} rebuttal response",
                    ]
                )
            if _SOURCE_DIVERSITY_OUTPUT in plan.requested_outputs:
                queries.extend(
                    [
                        f"{plan.canonical_phrase} official statement",
                        f"{plan.canonical_phrase} community response",
                    ]
                )
        return _dedupe(queries)

    def _build_round_query_specs(
        self,
        plan: InvestigationPlan,
        *,
        round_index: int,
        documents: list[Document],
        lanes: list[RetrievalLane],
        follow_up_queries: dict[RetrievalLane, list[str]],
    ) -> list[_QuerySpec]:
        specs: list[_QuerySpec] = []
        for lane in lanes:
            lane_queries = list(follow_up_queries.get(lane, []))
            if not lane_queries:
                lane_queries = self._queries_for_lane(plan, round_index, documents, lane)
            for query in lane_queries:
                specs.append(_QuerySpec(lane=lane, query=query))
        deduped: list[_QuerySpec] = []
        seen: set[tuple[str, str]] = set()
        for spec in specs:
            normalized = spec.query.strip().lower()
            if not normalized:
                continue
            key = (spec.lane, normalized)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(spec)
        return deduped

    def _queries_for_lane(
        self,
        plan: InvestigationPlan,
        round_index: int,
        documents: list[Document],
        lane: RetrievalLane,
    ) -> list[str]:
        phrase = plan.canonical_phrase or plan.topic
        if round_index == 1:
            lane_map = {
                "discovery": [*plan.search_queries[:2], plan.query_text],
                "corroboration": [f"{phrase} reporting", f"{phrase} independent coverage", *plan.semantic_queries[:1]],
                "contradiction": [f"{phrase} rebuttal", f"{phrase} fact check", f"{phrase} criticism"],
                "provenance": [f"{phrase} earliest mention", f"{phrase} first reported"],
                "official": [f"{phrase} official statement", f"{phrase} transcript", f"{phrase} press release"],
                "community": [f"{phrase} forum", f"{phrase} local discussion", f"{phrase} community response"],
            }
            return _dedupe(lane_map.get(lane, [plan.query_text]))

        queries: list[str] = []
        if lane == "provenance" and documents:
            oldest = min((doc.published_at for doc in documents if doc.published_at), default=None)
            if oldest is not None:
                queries.append(f"{phrase} before {oldest.date().isoformat()}")
        if lane == "official":
            scarce_type = self._find_scarce_requested_source_type(
                documents,
                ["government_record", "official_statement", "speech_transcript"],
            )
            if scarce_type:
                queries.append(f"{phrase} {scarce_type.replace('_', ' ')}")
        if lane == "community":
            queries.append(f"{phrase} forum post")
            queries.append(f"{phrase} local outlet")
        if lane == "contradiction":
            queries.append(f"{phrase} debunking")
            queries.append(f"{phrase} hostile interpretation")
        if lane == "corroboration":
            queries.append(f"{phrase} independent publisher")
        if lane == "discovery":
            queries.extend(plan.semantic_queries[:2])
        return _dedupe(queries or [f"{phrase} {lane.replace('_', ' ')}"])

    def _run_search_round(
        self,
        query_specs: list[_QuerySpec],
        plan: InvestigationPlan,
        warnings: list[str],
    ) -> list[tuple[_QuerySpec, SearchResult]]:
        if self._provider is None:
            raise RuntimeError("No search provider configured for retrieval.")
        results: list[tuple[_QuerySpec, SearchResult]] = []
        for spec in query_specs:
            source_types = self._source_types_for_lane(plan, spec.lane)
            try:
                for result in self._provider.search(
                    query=spec.query,
                    time_window=plan.time_window,
                    source_types=source_types,
                    limit=self._settings.RETRIEVER_MAX_RESULTS_PER_QUERY,
                ):
                    metadata = dict(result.metadata or {})
                    metadata["retrieval_lane"] = spec.lane
                    metadata["retrieval_query"] = spec.query
                    results.append((spec, result.model_copy(update={"metadata": metadata})))
            except Exception as exc:
                logger.warning("Search provider failed for query '%s': %s", spec.query, exc)
                warnings.append(f"search_failed:{spec.lane}:{spec.query}")
        deduped: list[tuple[_QuerySpec, SearchResult]] = []
        seen_urls: set[str] = set()
        for spec, result in sorted(results, key=lambda item: (item[1].rank, -(item[1].provider_score or 0.0))):
            normalized = self._normalize_url(result.url)
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            deduped.append((spec, result))
        return deduped

    def _source_types_for_lane(self, plan: InvestigationPlan, lane: RetrievalLane) -> list[str]:
        requested = list(plan.target_source_types)
        if lane == "official":
            return _dedupe([*requested, "government_record", "official_statement", "speech_transcript"])
        if lane == "community":
            return _dedupe([*requested, "forum", "blog", "local_news", "community_post"])
        if lane == "provenance":
            return _dedupe([*requested, "blog", "local_news", "speech_transcript"])
        return requested

    def _score_documents(self, documents: list[Document], plan: InvestigationPlan) -> list[tuple[Document, float]]:
        source_counts = Counter(doc.source_name for doc in documents)
        source_type_counts = Counter(doc.source_type for doc in documents)
        scored: list[tuple[Document, float]] = []
        plan_terms = {term.lower() for term in plan.entities}
        phrase = (plan.canonical_phrase or "").lower()
        target_source_types = {source_type.lower() for source_type in plan.target_source_types}
        for doc in documents:
            score = 0.0
            haystack = " ".join(filter(None, [doc.title, doc.snippet or "", doc.text])).lower()
            reason_tags: list[str] = []
            if phrase and phrase in haystack:
                score += 5.0
                reason_tags.append("exact_phrase")
            overlap = sum(1 for term in plan_terms if term in haystack)
            if overlap:
                score += min(overlap * 1.2, 4.0)
                reason_tags.append("query_overlap")
            contradiction_signal = 1.0 if any(signal in haystack for signal in _COUNTER_SIGNAL_TERMS) else 0.0
            if contradiction_signal:
                score += 1.5
                reason_tags.append("counter_signal")
            if doc.published_at is not None:
                score += 0.5
                reason_tags.append("dated")
                if plan.intent in {"origin", "spread"} or _TIMELINE_OUTPUT in plan.requested_outputs:
                    score += 0.4
                    reason_tags.append("timeline_priority")
            uniqueness_score = 1.0 if source_counts[doc.source_name] == 1 else max(0.1, 1 / source_counts[doc.source_name])
            if source_counts[doc.source_name] == 1:
                score += 0.8
                reason_tags.append("source_diversity")
            if doc.source_type in _TIMELINE_SOURCE_TYPES:
                score += 0.5
                reason_tags.append("timeline_useful")
            if doc.source_type.lower() in target_source_types:
                score += 0.8
                reason_tags.append("requested_source_type")
                if _SOURCE_DIVERSITY_OUTPUT in plan.requested_outputs and source_type_counts[doc.source_type] <= 1:
                    score += 0.6
                    reason_tags.append("scarce_source_type")
            if _RECEIPTS_OUTPUT in plan.requested_outputs and doc.snippet:
                score += 0.4
                reason_tags.append("receipt_snippet")
            if _GRAPH_OUTPUT in plan.requested_outputs and len(doc.entities) >= 2:
                score += 0.4
                reason_tags.append("graph_entities")
            if doc.duplicate_of_doc_id:
                score -= 2.0
                reason_tags.append("duplicate_penalty")

            metadata = dict(doc.metadata or {})
            metadata["retrieval_score"] = round(score, 3)
            metadata["retrieval_reason_tags"] = reason_tags
            metadata["contradiction_signal"] = contradiction_signal
            metadata["source_uniqueness_score"] = round(uniqueness_score, 3)
            metadata["primary_source_likelihood"] = round(self._primary_source_likelihood(doc), 3)
            metadata["date_confidence"] = self._date_confidence(doc)
            metadata["quality_band"] = self._quality_band(doc, score)
            metadata["provenance_hint"] = self._provenance_hint(doc)
            doc.metadata = metadata
            scored.append((doc, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored

    def _categorize_documents(
        self,
        scored_documents: list[tuple[Document, float]],
        plan: InvestigationPlan,
    ) -> dict[str, list[str]]:
        top_score = scored_documents[0][1] if scored_documents else 0.0
        high_cutoff = max(4.5, top_score * 0.65) if top_score else 4.5
        high_relevance: list[str] = []
        main_narrative: list[str] = []
        counter_candidates: list[str] = []
        context: list[str] = []
        phrase = (plan.canonical_phrase or "").lower()
        for doc, score in scored_documents:
            haystack = " ".join(filter(None, [doc.title, doc.snippet or "", doc.text])).lower()
            if score >= high_cutoff:
                high_relevance.append(doc.id)
            if phrase and phrase in haystack:
                main_narrative.append(doc.id)
            elif _COUNTER_OUTPUT in plan.requested_outputs and any(signal in haystack for signal in _COUNTER_SIGNAL_TERMS):
                counter_candidates.append(doc.id)
            else:
                context.append(doc.id)
        return {
            "high_relevance": high_relevance,
            "main_narrative": main_narrative,
            "counter_candidates": counter_candidates,
            "context": context,
        }

    def _build_document_annotations(
        self,
        *,
        scored_documents: list[tuple[Document, float]],
        previous_annotations: dict[str, RetrievalDocumentAnnotation],
        lane_for_doc_id: dict[str, tuple[RetrievalLane, str]],
        pass_number: int,
        duplicate_clusters: dict[str, list[str]],
    ) -> list[RetrievalDocumentAnnotation]:
        cluster_for_doc = {
            doc_id: cluster_id for cluster_id, doc_ids in duplicate_clusters.items() for doc_id in doc_ids
        }
        annotations: list[RetrievalDocumentAnnotation] = []
        for doc, score in scored_documents:
            previous = previous_annotations.get(doc.id)
            lane, query = lane_for_doc_id.get(
                doc.id,
                (
                    previous.retrieval_lane if previous is not None else "discovery",
                    previous.retrieval_query if previous is not None else doc.title,
                ),
            )
            metadata = dict(doc.metadata or {})
            annotations.append(
                RetrievalDocumentAnnotation(
                    document_id=doc.id,
                    retrieval_lane=lane,
                    retrieval_query=query,
                    pass_number=max(pass_number, previous.pass_number if previous is not None else pass_number),
                    relevance_score=min(1.0, max(0.0, round(score / 8.0, 3))),
                    contradiction_signal=float(metadata.get("contradiction_signal", 0.0)),
                    source_uniqueness_score=float(metadata.get("source_uniqueness_score", 0.0)),
                    primary_source_likelihood=float(metadata.get("primary_source_likelihood", 0.0)),
                    date_confidence=metadata.get("date_confidence", "low"),
                    quality_band=metadata.get("quality_band", "tier_d"),
                    duplicate_cluster_id=cluster_for_doc.get(doc.id),
                    upstream_origin_hint=metadata.get("upstream_origin_hint"),
                    provenance_hint=metadata.get("provenance_hint"),
                    independence_penalty=(0.35 if doc.duplicate_of_doc_id else 0.0),
                )
            )
        return annotations

    def _build_coverage_summary(
        self,
        documents: list[Document],
        rounds: list[RetrievalRound],
        round_number: int,
        plan: InvestigationPlan,
        annotations: list[RetrievalDocumentAnnotation],
    ) -> CoverageSummary:
        source_types = Counter(doc.source_type for doc in documents)
        phrase = (plan.canonical_phrase or "").lower()
        exact_phrase_hits = 0
        counter_hits = 0
        timeline_hits = 0
        lane_distribution = Counter(annotation.retrieval_lane for annotation in annotations)
        for doc in documents:
            haystack = " ".join(filter(None, [doc.title, doc.snippet or "", doc.text])).lower()
            if phrase and phrase in haystack:
                exact_phrase_hits += 1
            if any(signal in haystack for signal in _COUNTER_SIGNAL_TERMS):
                counter_hits += 1
            if doc.published_at is not None:
                timeline_hits += 1
        return CoverageSummary(
            total_documents=len(documents),
            unique_sources=len({doc.source_name for doc in documents}),
            source_type_distribution=dict(source_types),
            lane_distribution=dict(lane_distribution),
            has_counter_narrative_candidates=counter_hits > 0,
            has_timeline_coverage=timeline_hits >= max(2, len(documents) // 2) if documents else False,
            has_official_source=any(doc.source_type in _OFFICIAL_SOURCE_TYPES for doc in documents),
            exact_phrase_hits=exact_phrase_hits,
            search_rounds_completed=round_number if documents or rounds else 0,
        )

    def _coverage_confidence(self, coverage: CoverageSummary) -> str:
        if (
            coverage.total_documents >= 8
            and coverage.unique_sources >= 5
            and coverage.exact_phrase_hits >= 2
            and coverage.has_timeline_coverage
        ):
            return "high"
        if coverage.total_documents >= 4 and coverage.unique_sources >= 3:
            return "medium"
        return "low"

    def _should_stop(
        self,
        *,
        all_documents: list[Document],
        coverage: CoverageSummary,
        new_documents: int,
        previous_doc_count: int,
        round_number: int,
        max_rounds: int,
        plan: InvestigationPlan,
        lanes: list[RetrievalLane],
    ) -> bool:
        if round_number >= max_rounds:
            return True
        enough_counter = coverage.has_counter_narrative_candidates if "contradiction" in lanes else True
        enough_timeline = coverage.has_timeline_coverage if ("provenance" in lanes or _TIMELINE_OUTPUT in plan.requested_outputs) else True
        enough_official = coverage.has_official_source if "official" in lanes else True
        enough_diversity = (
            len(coverage.source_type_distribution) >= min(3, len(plan.target_source_types))
            if _SOURCE_DIVERSITY_OUTPUT in plan.requested_outputs
            else True
        )
        if coverage.total_documents >= 8 and coverage.unique_sources >= 5 and enough_timeline and enough_counter and enough_diversity and enough_official:
            return True
        if new_documents == 0:
            return True
        if previous_doc_count and len(all_documents) - previous_doc_count <= 1:
            return True
        return False

    def _find_scarce_requested_source_type(
        self,
        documents: list[Document],
        target_source_types: list[str],
    ) -> str | None:
        if not target_source_types:
            return None
        current_counts = Counter(doc.source_type for doc in documents)
        for source_type in target_source_types:
            if current_counts[source_type] == 0:
                return source_type
        return None

    def _detect_duplicates(self, documents: list[Document]) -> tuple[list[DuplicateCandidate], dict[str, list[str]]]:
        duplicates: list[DuplicateCandidate] = []
        cluster_map: dict[str, list[str]] = defaultdict(list)
        cluster_index = 0
        for left_index, left in enumerate(documents):
            for right in documents[left_index + 1:]:
                similarity = self._doc_similarity(left, right)
                if similarity < 0.92:
                    continue
                shared_origin_hint = self._shared_origin_hint(left, right)
                reason = shared_origin_hint or (
                    "same_url" if self._normalize_url(left.url) == self._normalize_url(right.url) else "similar_title_text"
                )
                existing_cluster_id = next(
                    (cluster_id for cluster_id, doc_ids in cluster_map.items() if left.id in doc_ids or right.id in doc_ids),
                    None,
                )
                if existing_cluster_id is None:
                    cluster_index += 1
                    existing_cluster_id = f"dup_{cluster_index}"
                cluster_map[existing_cluster_id].extend([left.id, right.id])
                duplicates.append(
                    DuplicateCandidate(
                        left_doc_id=left.id,
                        right_doc_id=right.id,
                        similarity_score=round(similarity, 3),
                        reason=reason,
                        cluster_id=existing_cluster_id,
                        shared_origin_hint=shared_origin_hint,
                    )
                )
                if similarity >= 0.98:
                    right.duplicate_of_doc_id = left.id

        deduped_clusters = {
            cluster_id: list(dict.fromkeys(doc_ids))
            for cluster_id, doc_ids in cluster_map.items()
        }
        return duplicates, deduped_clusters

    def _shared_origin_hint(self, left: Document, right: Document) -> str | None:
        left_url = self._normalize_url(left.url)
        right_url = self._normalize_url(right.url)
        if left_url == right_url:
            return "same_url"
        if left.source_name == right.source_name:
            return "same_publisher"
        if left.metadata and right.metadata:
            left_upstream = str(left.metadata.get("upstream_origin_hint") or "").strip().lower()
            right_upstream = str(right.metadata.get("upstream_origin_hint") or "").strip().lower()
            if left_upstream and left_upstream == right_upstream:
                return "shared_upstream_hint"
        return None

    def _doc_similarity(self, left: Document, right: Document) -> float:
        if self._normalize_url(left.url) == self._normalize_url(right.url):
            return 1.0
        left_text = f"{left.title} {left.snippet or ''} {left.text[:800]}"
        right_text = f"{right.title} {right.snippet or ''} {right.text[:800]}"
        return SequenceMatcher(None, left_text.lower(), right_text.lower()).ratio()

    def _primary_source_likelihood(self, doc: Document) -> float:
        if doc.source_type in {"speech_transcript", "government_record"}:
            return 0.9
        if doc.source_name.endswith(".gov"):
            return 0.85
        if "press release" in doc.title.lower():
            return 0.8
        return 0.45 if doc.source_type in {"local_news", "national_news"} else 0.22

    def _date_confidence(self, doc: Document) -> str:
        if doc.published_at is None:
            return "low"
        if doc.source_type in {"speech_transcript", "government_record", "national_news", "local_news"}:
            return "high"
        return "medium"

    def _quality_band(self, doc: Document, score: float) -> str:
        primary = self._primary_source_likelihood(doc)
        if primary >= 0.8 or doc.source_type in {"speech_transcript"}:
            return "tier_a"
        if score >= 5.0:
            return "tier_b"
        if score >= 3.0:
            return "tier_c"
        return "tier_d"

    def _provenance_hint(self, doc: Document) -> str | None:
        lowered = f"{doc.title} {doc.snippet or ''}".lower()
        if "according to" in lowered:
            return "secondary_citation_language_detected"
        if doc.source_name.endswith(".gov"):
            return "official_domain_anchor"
        if doc.source_type == "blog":
            return "possible_early_commentary_source"
        return None

    def _normalize_url(self, url: str) -> str:
        return re.sub(r"/+$", "", url.strip().lower())


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        output.append(normalized)
    return output
