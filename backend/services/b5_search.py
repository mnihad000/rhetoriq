"""Canonical B5 retrieval service.

Search results are assembled from immutable projection snapshots.  External
indexes may improve ranking, but a target result is accepted only when its
revision and semantic hash still match the canonical snapshot.  This keeps a
late index update or a withdrawn document from becoming visible through the
search API.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
from typing import Any, Iterable
import unicodedata
from functools import wraps
import time

from models.document import Document
from services.b5_cache import B5Cache

logger = logging.getLogger(__name__)

_RRF_K = 60
_MAX_LANE = 50
_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100


def _measure_query(method):
    @wraps(method)
    def measured(self,*args,**kwargs):
        started = time.perf_counter()
        result = None
        try:
            result = method(self,*args,**kwargs)
            return result
        finally:
            lane = "cached" if isinstance(result,dict) and result.get("cached") else "uncached"
            key = method.__name__+":"+lane
            metrics = self.query_metrics.setdefault(key,{"calls":0,"errors":0,"total_latency_ms":0.0,"max_latency_ms":0.0})
            elapsed = (time.perf_counter()-started)*1000
            metrics["calls"] += 1
            metrics["errors"] += int(result is None)
            metrics["total_latency_ms"] += elapsed
            metrics["max_latency_ms"] = max(metrics["max_latency_ms"],elapsed)
    return measured


class SearchService:
    def __init__(
        self,
        target: str | None = None,
        settings: Any | None = None,
        repository: Any | None = None,
        elastic: Any | None = None,
        neo4j: Any | None = None,
        embedding_service: Any | None = None,
        cache: B5Cache | None = None,
    ) -> None:
        self.settings = settings
        self.query_metrics: dict[str,dict[str,int | float]] = {}
        if self.settings is None:
            try:
                from config import get_settings

                self.settings = get_settings()
            except Exception:
                self.settings = None
        self.target = target or getattr(self.settings, "B5_WORKER_TARGET", "elasticsearch")
        self.repository_target = target or getattr(self.settings, "persistence_target", None) or self.target
        self.repository = repository or self._default_projection_repository()
        self.elastic = elastic
        self.neo4j = neo4j
        if self.settings is not None and getattr(self.settings, "ENABLE_B5_RETRIEVAL", False):
            try:
                from services.b5_targets import ElasticsearchTarget

                self.elastic = self.elastic or ElasticsearchTarget(self.settings)
            except Exception:
                logger.debug("Unable to initialize Elasticsearch target", exc_info=True)
            try:
                from services.b5_targets import Neo4jTarget

                self.neo4j = self.neo4j or Neo4jTarget(self.settings)
            except Exception:
                logger.debug("Unable to initialize Neo4j target", exc_info=True)
        self.embedding_service = embedding_service
        if cache is not None:
            self.cache = cache
        elif self.settings is not None and getattr(self.settings, "ENABLE_B5_RETRIEVAL", False):
            self.cache = B5Cache(
                getattr(self.settings, "REDIS_URL", ""),
                ttl_seconds=getattr(self.settings, "B5_CACHE_TTL_SECONDS", 30),
                max_item_bytes=getattr(self.settings, "B5_CACHE_MAX_BYTES", 262144),
                password=getattr(self.settings, "REDIS_PASSWORD", ""),
                ca_cert=getattr(self.settings, "REDIS_CA_CERT", ""),
            )
        else:
            self.cache = None
        self.last_diagnostics: dict[str, Any] = {}
        self.warning: str | None = None

    # ------------------------------------------------------------------
    # Public search API
    # ------------------------------------------------------------------
    @_measure_query
    def search(
        self,
        query: str,
        mode: str = "hybrid",
        investigation_id: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        public_mode = str(mode or "hybrid").lower()
        if public_mode not in {"hybrid", "lexical", "semantic", "fulltext", "phrase"}:
            raise ValueError("mode must be hybrid, fulltext, phrase, or semantic")
        mode = "lexical" if public_mode in {"fulltext", "phrase"} else public_mode
        limit = min(_MAX_LIMIT, max(1, int(limit)))
        offset = max(0, int(offset))
        filters = dict(filters or {})

        context = self._canonical_context(investigation_id, filters)
        snapshots = context["snapshots"]
        docs = context["docs"]
        generation = context["generation"]
        pending = bool(context["pending"])
        limitations = list(context["limitations"])
        cache_kind = self._cache_kind(query, public_mode, filters)
        cache_revision, cache_hash = self._context_revision_hash(context)
        if use_cache and self.cache and not pending:
            try:
                cached = self.cache.get(
                    cache_kind, investigation_id, self.target, generation,
                    revision=cache_revision, semantic_hash=cache_hash,
                    validator=lambda value: self._cache_valid(value, context, query, public_mode),
                )
                if cached is not None:
                    self.last_diagnostics = {
                        "source": cached.get("source"),
                        "fallback_active": cached.get("fallback_active", False),
                        "pending": cached.get("pending", False),
                        "complete": cached.get("complete", True),
                        "limitations": cached.get("limitations", []),
                    }
                    return {**cached,"cached":True}
            except Exception:
                logger.debug("B5 cache read failed", exc_info=True)
        if not docs:
            return self._annotate_projection(self._search_response(
                investigation_id, query, mode, [], 0, offset, generation,
                source="empty" if not pending else "canonical",
                fallback_active=bool(pending or context["fallback"]),
                pending=pending,
                complete=not pending,
                limitations=limitations,
                snapshot_id=context.get("snapshot_id"),
            ),context,["minilm"] if public_mode=="semantic" else ["elasticsearch","minilm"] if public_mode=="hybrid" else ["elasticsearch"])

        by_id = {doc.id: doc for doc in docs}
        snap_by_id = {str(s.get("data", {}).get("document", {}).get("id") or s.get("snapshot_id")): s for s in snapshots}
        candidate_ids = set(by_id)
        provider_ids = None if context.get("global_provider") else list(candidate_ids)
        lane_results: dict[str, list[dict[str, Any]]] = {"lexical": [], "semantic": []}
        provider_unavailable = False
        target_degraded = False

        # A provider may return stale documents; validation below is mandatory.
        if mode in {"hybrid", "lexical"}:
            try:
                lane_results["lexical"] = self._elastic_search(
                    query, mode=public_mode, document_ids=provider_ids,
                    filters=filters, generation=generation,
                )
            except ValueError:
                raise
            except Exception as exc:
                provider_unavailable = True
                limitations.append("Lexical index unavailable; canonical lexical fallback used.")
                logger.debug("B5 lexical target unavailable: %s", exc)
            raw_lexical = lane_results["lexical"]
            if context.get("global_provider"):
                self._hydrate_target_rows(raw_lexical, context, filters)
                docs = context["docs"]
                snapshots = context["snapshots"]
                by_id = {doc.id: doc for doc in docs}
                snap_by_id = {str(s.get("data", {}).get("document", {}).get("id") or s.get("snapshot_id")): s for s in snapshots}
                candidate_ids = set(by_id)
            lane_results["lexical"] = self._validated_lane(raw_lexical, snap_by_id, candidate_ids)
            if raw_lexical and not lane_results["lexical"]:
                target_degraded = True
                provider_unavailable = True
                limitations.append("Lexical target returned only stale or ineligible results.")
            if not lane_results["lexical"] and provider_unavailable and not target_degraded:
                lane_results["lexical"] = self._local_lexical(query, docs, filters, mode=public_mode)

        if mode in {"hybrid", "semantic"}:
            try:
                lane_results["semantic"] = self._semantic_search(
                    query, document_ids=provider_ids, filters=filters,
                    generation=generation,
                )
            except ValueError:
                raise
            except Exception as exc:
                provider_unavailable = True
                limitations.append("Semantic index unavailable; semantic results omitted.")
                logger.debug("B5 semantic target unavailable: %s", exc)
            raw_semantic = lane_results["semantic"]
            if context.get("global_provider"):
                self._hydrate_target_rows(raw_semantic, context, filters)
                docs = context["docs"]
                snapshots = context["snapshots"]
                by_id = {doc.id: doc for doc in docs}
                snap_by_id = {str(s.get("data", {}).get("document", {}).get("id") or s.get("snapshot_id")): s for s in snapshots}
                candidate_ids = set(by_id)
            lane_results["semantic"] = self._validated_lane(raw_semantic, snap_by_id, candidate_ids)
            if raw_semantic and not lane_results["semantic"]:
                target_degraded = True
                provider_unavailable = True
                limitations.append("Semantic target returned only stale or ineligible results.")

        # A semantic-only outage still returns usable bounded canonical results.
        if mode == "semantic" and not lane_results["semantic"] and provider_unavailable:
            lane_results["semantic"] = self._local_lexical(query, docs, filters, mode="fulltext")
            limitations.append("Canonical text fallback used for semantic mode.")
            provider_unavailable = True

        fused = self._fuse(lane_results, by_id, snap_by_id, query)
        if public_mode == "phrase":
            # Elasticsearch's phrase match is a candidate generator; verify
            # the literal against the canonical text before exposing it.
            fused = [item for item in fused if item["spans"]]
        total = len(fused)
        page = fused[offset: offset + limit]
        source = "hybrid" if mode == "hybrid" and not provider_unavailable else (
            "elasticsearch" if mode == "lexical" and not provider_unavailable else "canonical"
        )
        fallback = provider_unavailable or context["fallback"]
        complete = bool(not pending and not provider_unavailable)
        result = self._search_response(
            investigation_id, query, public_mode, page, total, offset, generation,
            source=source, fallback_active=fallback, pending=pending,
            complete=complete, limitations=list(dict.fromkeys(limitations)),
            snapshot_id=context.get("snapshot_id"),
        )

        # Cache healthy, complete responses only.  Cache validation below still
        # checks the canonical context, so a hit can never resurrect a stale
        # revision or a withdrawn document.
        result = self._annotate_projection(result,context,["minilm"] if public_mode=="semantic" else ["elasticsearch","minilm"] if public_mode=="hybrid" else ["elasticsearch"])
        if use_cache and self.cache and not fallback and result["complete"] and offset == 0:
            cache_key_revision, cache_hash = self._context_revision_hash(context)
            try:
                if self._canonical_token_matches(context, investigation_id, generation):
                    self.cache.set(
                        cache_kind, investigation_id, self.target, generation, result,
                        revision=cache_key_revision, semantic_hash=cache_hash,
                    )
            except Exception:
                logger.debug("B5 cache write failed", exc_info=True)
        self.last_diagnostics = {
            "source": source,
            "fallback_active": fallback,
            "pending": pending,
            "complete": complete,
            "limitations": result["limitations"],
        }
        self.warning = "; ".join(result["limitations"]) if result["limitations"] else None
        return result

    def internal_search(self, query: str, limit: int = 8) -> list[Document]:
        """Search current canonical documents for internal research callers."""
        response = self.search(
            query,
            mode="hybrid",
            investigation_id=None,
            filters={"include_leads": True},
            limit=max(1, int(limit)),
            offset=0,
        )
        docs_by_id: dict[str, Document] = {}
        if self.repository is not None:
            try:
                result_ids = [str(row["document_id"]) for row in response.get("results", [])]
                exact_snapshots = self.repository.documents(ids=result_ids, limit=len(result_ids)) if result_ids else []
                docs_by_id = {
                    doc.id: doc
                    for doc in self._documents_from_snapshots(
                        exact_snapshots,
                        include_ineligible=True,
                    )
                }
            except Exception:
                docs_by_id = {}
        warnings = list(response.get("limitations", []))
        hydrated: list[Document] = []
        for row in response.get("results", []):
            document = docs_by_id.get(row["document_id"])
            if document is None:
                continue
            hydrated.append(document.model_copy(update={
                "metadata": {
                    **(document.metadata or {}),
                    "retrieval_score": row.get("score"),
                    "retrieval_contributions": row.get("contributions", {}),
                    "retrieval_spans": row.get("spans", []),
                    "retrieval_provider": response.get("source"),
                }
            }))

        # Related graph neighbors are context-only: they are bounded to the
        # top ranked IDs, hydrated from canonical current snapshots, and never
        # receive retrieval score or claim-support weight.
        related_lookup = getattr(self.neo4j, "related_document_ids", None) if self.neo4j is not None else None
        top_ids = [str(row["document_id"]) for row in response.get("results", [])]
        if related_lookup and top_ids and len(hydrated) < max(1, int(limit)):
            try:
                related_ids = [
                    str(item) for item in (
                        related_lookup(
                            top_ids,
                            generation=response.get("generation", self._generation()),
                            limit=20,
                        ) or []
                    )
                ]
            except Exception as exc:
                related_ids = []
                warnings.append("Neo4j related-document expansion unavailable; graph context omitted.")
                logger.debug("B5 related-document expansion unavailable: %s", exc)
            if related_ids and self.repository is not None:
                existing_ids = {doc.id for doc in hydrated}
                related_ids = [item for item in dict.fromkeys(related_ids) if item not in existing_ids][:20]
                try:
                    related_snapshots = list(self.repository.documents(ids=related_ids, limit=len(related_ids)) or [])
                    related_docs = self._documents_from_snapshots(related_snapshots, include_ineligible=True)
                    related_docs = self._apply_filters(related_docs, {"include_leads": True})
                    for document in related_docs:
                        if document.id in existing_ids or len(hydrated) >= max(1, int(limit)):
                            continue
                        hydrated.append(document.model_copy(update={
                            "metadata": {
                                **(document.metadata or {}),
                                "retrieval_score": 0.0,
                                "retrieval_contributions": {},
                                "retrieval_provider": "neo4j_related",
                                "retrieval_context_only": True,
                            }
                        }))
                        existing_ids.add(document.id)
                except Exception:
                    warnings.append("Canonical related-document hydration unavailable; graph context omitted.")
                    logger.debug("B5 related-document hydration unavailable", exc_info=True)
        self.warning = "; ".join(dict.fromkeys(warnings)) or None
        return hydrated[: max(1, int(limit))]

    def status(self) -> dict[str, Any]:
        """Return bounded component health for the B5 health endpoint."""
        components: dict[str, Any] = {}
        if self.repository is not None:
            try:
                components["canonical"] = self.repository.status()
            except Exception as exc:
                components["canonical"] = {"status": "unavailable", "detail": str(exc)[:180]}
        else:
            components["canonical"] = {"status": "unavailable", "detail": "Projection repository is not configured."}
        for name, target in (("elasticsearch", self.elastic), ("neo4j", self.neo4j)):
            if target is None:
                components[name] = {"status": "disabled"}
                continue
            try:
                health = target.health() if hasattr(target, "health") else {"status": "ready"}
                components[name] = health if isinstance(health, dict) else {"status": str(health)}
            except Exception as exc:
                components[name] = {"status": "unavailable", "detail": str(exc)[:180]}
        if self.cache is None:
            components["cache"] = {"status": "disabled", "metrics": {}}
        else:
            components["cache"] = {
                "status": "ready" if self.cache.available else "unavailable",
                "metrics": dict(self.cache.metrics),
                "ttl_seconds": self.cache.ttl_seconds,
                "max_item_bytes": self.cache.max_item_bytes,
            }
        states = [str(item.get("status", "unavailable")) for item in components.values() if isinstance(item, dict)]
        status = "ready" if states and all(item in {"ready", "healthy", "complete", "ok", "disabled"} for item in states) else "degraded"
        return {"status": status, "target": self.target, "components": components}

    # ------------------------------------------------------------------
    # Graph/provenance API
    # ------------------------------------------------------------------
    @_measure_query
    def graph(self, investigation_id: str, include_inferred: bool = True, relationships: Any = None, use_cache: bool = True) -> dict[str, Any]:
        context = self._canonical_context(investigation_id, {})
        docs = context["docs"]
        limitations = list(context["limitations"])
        relationships_key = json.dumps(relationships, sort_keys=True, default=str)
        graph_kind = "graph:" + hashlib.sha256(f"{include_inferred}\0{relationships_key}".encode()).hexdigest()[:24]
        cache_revision, cache_hash = self._context_revision_hash(context)
        if use_cache and self.cache and not context["pending"]:
            try:
                cached = self.cache.get(
                    graph_kind, investigation_id, self.target, context["generation"],
                    revision=cache_revision, semantic_hash=cache_hash,
                    validator=lambda value: self._graph_cache_valid(value, context),
                )
                if cached is not None:
                    return {**cached,"cached":True}
            except Exception:
                logger.debug("B5 graph cache read failed", exc_info=True)
        if not docs:
            if self.neo4j is not None:
                try:
                    payload = self.neo4j.read_graph(investigation_id, [], context["generation"])
                    target_limitations = list(payload.get("limitations", []))
                    unavailable = any("unavailable" in str(item).lower() or "not configured" in str(item).lower() for item in target_limitations)
                    if not unavailable:
                        return self._annotate_projection(self._graph_response(
                            investigation_id, [], [], context,
                            list(dict.fromkeys(limitations + target_limitations)),
                            source="neo4j", fallback_active=False, truncated=False,
                        ),context,["neo4j"])
                except Exception:
                    logger.debug("B5 empty graph target unavailable", exc_info=True)
            return self._annotate_projection(self._graph_response(
                investigation_id, [], [], context, limitations,
                source="canonical", fallback_active=True, truncated=False,
            ),context,["neo4j"])
        document_ids = [doc.id for doc in docs]
        nodes: list[Any] = []
        edges: list[Any] = []
        source = "canonical"
        fallback = bool(context["fallback"])
        target_attempted = False
        target_failed = False
        if self.neo4j is not None:
            target_attempted = True
            try:
                payload = self.neo4j.read_graph(investigation_id, document_ids, context["generation"])
                self._validate_graph_revision(payload,context)
                nodes, edges = self._validate_graph_payload(payload, set(document_ids), include_inferred, relationships)
                limitations.extend(payload.get("limitations", []))
                source = "neo4j"
                if any("unavailable" in str(item).lower() or "not configured" in str(item).lower() for item in payload.get("limitations", [])):
                    target_failed = True
            except Exception as exc:
                target_failed = True
                fallback = True
                limitations.append("Graph target unavailable; canonical graph fallback used.")
                logger.debug("B5 graph target unavailable: %s", exc)
        if (not target_attempted or target_failed) and not nodes and not edges:
            try:
                from services.b5_graph import build_investigation_graph

                if context.get("investigation_snapshot"):
                    payload = build_investigation_graph(context["investigation_snapshot"], context["snapshots"])
                    nodes, edges = self._validate_graph_payload(payload, set(document_ids), include_inferred, relationships)
                    limitations.extend(payload.get("limitations", []))
                else:
                    nodes, edges = self._canonical_graph(docs)
            except Exception as exc:
                logger.debug("B5 canonical graph builder unavailable: %s", exc)
                nodes, edges = self._canonical_graph(docs)
            if not include_inferred:
                edges = [edge for edge in edges if edge.get("evidence_class") != "inferred" and not edge.get("inferred", False)]
            fallback = True
        truncated = len(nodes) > 200 or len(edges) > 500
        if truncated:
            limitations.append("Graph response truncated to the bounded node and edge limits.")
        nodes = nodes[:200]
        visible = {str(node["id"]) for node in nodes}
        edges = [edge for edge in edges if str(edge.get("source")) in visible and str(edge.get("target")) in visible][:500]
        response = self._graph_response(
            investigation_id, nodes, edges, context, list(dict.fromkeys(limitations)),
            source=source, fallback_active=fallback, truncated=truncated,
        )
        response = self._annotate_projection(response,context,["neo4j"])
        if use_cache and self.cache and response["complete"] and not response["fallback_active"] and not response["pending"]:
            if self._canonical_token_matches(context, investigation_id, context["generation"]):
                self.cache.set(
                    graph_kind, investigation_id, self.target, context["generation"], response,
                    revision=cache_revision, semantic_hash=cache_hash,
                )
        return response

    @_measure_query
    def paths(
        self,
        investigation_id: str,
        from_document_id: str,
        to_document_id: str,
        max_depth: int = 4,
        include_inferred: bool = False,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        max_depth = min(6, max(1, int(max_depth)))
        context = self._canonical_context(investigation_id, {})
        docs = context["docs"]
        allowed = {doc.id for doc in docs}
        limitations = list(context["limitations"])
        paths_kind = "paths:" + hashlib.sha256(f"{from_document_id}\0{to_document_id}\0{max_depth}\0{include_inferred}".encode()).hexdigest()[:24]
        cache_revision, cache_hash = self._context_revision_hash(context)
        if use_cache and self.cache and not context["pending"]:
            try:
                cached = self.cache.get(
                    paths_kind, investigation_id, self.target, context["generation"],
                    revision=cache_revision, semantic_hash=cache_hash,
                    validator=lambda value: self._paths_cache_valid(value, context),
                )
                if cached is not None:
                    return {**cached,"cached":True}
            except Exception:
                logger.debug("B5 paths cache read failed", exc_info=True)
        if from_document_id not in allowed or to_document_id not in allowed:
            return self._annotate_projection(self._paths_response(
                investigation_id, [], context, limitations + ["Path endpoints are outside acquired evidence."],
                source="canonical", fallback_active=True,
            ),context,["neo4j"])
        source = "canonical"
        fallback = bool(context["fallback"])
        paths: list[Any] = []
        path_stats: dict[str,Any] = {}
        target_attempted = False
        target_failed = False
        if self.neo4j is not None:
            target_attempted = True
            try:
                payload = self.neo4j.read_graph(investigation_id, list(allowed), context["generation"])
                self._validate_graph_revision(payload,context)
                nodes, edges = self._validate_graph_payload(payload, allowed, include_inferred, None)
                limitations.extend(payload.get("limitations", []))
                if any("unavailable" in str(item).lower() or "not configured" in str(item).lower() for item in payload.get("limitations", [])):
                    target_failed = True
                try:
                    from services.b5_graph import provenance_paths

                    paths = provenance_paths(
                        {"nodes": nodes, "edges": edges},
                        from_document_id, to_document_id,
                        max_depth=max_depth, include_inferred=include_inferred, limit=10,stats=path_stats,
                    )
                except Exception:
                    paths = self._paths_from_edges(edges, from_document_id, to_document_id, max_depth)
                source = "neo4j"
            except Exception as exc:
                target_failed = True
                fallback = True
                limitations.append("Provenance target unavailable; canonical path fallback used.")
                logger.debug("B5 provenance target unavailable: %s", exc)
        if (not target_attempted or target_failed) and not paths:
            try:
                from services.b5_graph import build_investigation_graph, provenance_paths

                if context.get("investigation_snapshot"):
                    graph = build_investigation_graph(context["investigation_snapshot"], context["snapshots"])
                    graph_nodes,graph_edges = self._validate_graph_payload(graph,allowed,include_inferred,None)
                    paths = provenance_paths(
                        {"nodes":graph_nodes,"edges":graph_edges}, from_document_id, to_document_id,
                        max_depth=max_depth, include_inferred=include_inferred, limit=10,stats=path_stats,
                    )
                    limitations.extend(graph.get("limitations", []))
                else:
                    _, edges = self._canonical_graph(docs)
                    paths = self._paths_from_edges(edges, from_document_id, to_document_id, max_depth)
            except Exception as exc:
                logger.debug("B5 canonical provenance builder unavailable: %s", exc)
                _, edges = self._canonical_graph(docs)
                paths = self._paths_from_edges(edges, from_document_id, to_document_id, max_depth)
            fallback = True
        response = self._paths_response(
            investigation_id, paths[:10], context,
            list(dict.fromkeys(limitations)), source=source,
            fallback_active=fallback,
        )
        response["truncated"] = bool(path_stats.get("truncated"))
        response["traversal"] = path_stats
        if response["truncated"]:
            response["limitations"].append("Path count or traversal work limit reached.")
        response = self._annotate_projection(response,context,["neo4j"])
        if use_cache and self.cache and response["complete"] and not response["fallback_active"] and not response["pending"]:
            if self._canonical_token_matches(context, investigation_id, context["generation"]):
                self.cache.set(
                    paths_kind, investigation_id, self.target, context["generation"], response,
                    revision=cache_revision, semantic_hash=cache_hash,
                )
        return response

    # ------------------------------------------------------------------
    # Canonical state and target adapters
    # ------------------------------------------------------------------
    def _canonical_context(self, investigation_id: str | None, filters: dict[str, Any]) -> dict[str, Any]:
        if not investigation_id:
            docs = []
            snapshots: list[dict[str, Any]] = []
            if self.repository is not None:
                try:
                    snapshots = list(self.repository.documents(ids=None, limit=500) or [])
                    docs = self._documents_from_snapshots(
                        snapshots,
                        include_ineligible=bool(filters.get("include_leads")),
                    )
                except Exception:
                    pass
            docs = self._apply_filters(docs, filters)
            return {
                "snapshots": snapshots, "docs": docs, "generation": self._generation(),
                "snapshot_id": None, "pending": False, "fallback": True,
                "limitations": ["Canonical fallback scan is bounded to 500 documents."],
                "global_provider": True,
            }

        generation = self._generation()
        captured_token = (
            self._read_cache_token("investigation", investigation_id, generation),
            self._read_cache_token("document", None, generation),
        )
        workspace = self._workspace(investigation_id)
        if workspace is None:
            raise KeyError(investigation_id)
        investigation_snapshot = None
        if self.repository is not None:
            try:
                investigation_snapshot = self.repository.current("investigation", investigation_id)
            except Exception:
                investigation_snapshot = None
        if investigation_snapshot:
            snapshot_id = investigation_snapshot.get("snapshot_id")
        else:
            snapshot_id = None
        ids = self._investigation_document_ids(investigation_snapshot, workspace)
        snapshots: list[dict[str, Any]] = []
        if self.repository is not None:
            try:
                if ids and hasattr(self.repository, "get_snapshot"):
                    snapshots = self.repository.get_snapshots(ids) if hasattr(self.repository,"get_snapshots") else [
                        snapshot for snapshot in
                        (self.repository.get_snapshot(snapshot_id) for snapshot_id in ids)
                        if snapshot is not None
                    ]
                if not snapshots:
                    snapshots = list(self.repository.documents(ids=ids, limit=10000) or []) if ids else []
            except Exception as exc:
                logger.debug("B5 document projection unavailable: %s", exc)
        if investigation_snapshot and snapshots and self.repository is not None and hasattr(self.repository, "current"):
            # Investigation evidence is pinned for graph provenance, but a
            # later canonical withdrawal or ineligibility decision removes it
            # from every user-visible surface.
            current_snapshots: list[dict[str, Any]] = []
            current_by_id = {self._snapshot_document_id(snapshot):snapshot for snapshot in self.repository.documents(ids=[self._snapshot_document_id(item) for item in snapshots],limit=len(snapshots))}
            for snapshot in snapshots:
                document_id = self._snapshot_document_id(snapshot)
                try:
                    current = current_by_id.get(document_id)
                except Exception:
                    current = None
                if current is None:
                    continue
                current_data = current.get("data") or {}
                if (current.get("eligible") is False and not filters.get("include_leads")) or current_data.get("withdrawn") or current.get("operation") in {"withdraw", "withdrawal"}:
                    continue
                current_snapshots.append(snapshot)
            snapshots = current_snapshots
        docs = self._documents_from_snapshots(
            snapshots,
            include_ineligible=bool(filters.get("include_leads")),
        )
        if not docs and not investigation_snapshot and getattr(workspace, "retrieved_documents", None):
            # A running workspace can precede its immutable projection.  The
            # fallback remains explicitly pending and is bounded to workspace
            # documents, never to the demo corpus.
            docs = list(workspace.retrieved_documents)
            if self.repository is not None and hasattr(self.repository,"current"):
                permitted = []
                for document in docs:
                    current = self.repository.current("document",document.id)
                    if current and (current["data"].get("withdrawn") or (not current["eligible"] and not filters.get("include_leads"))):
                        continue
                    permitted.append(document)
                docs = permitted
        docs = self._apply_filters(docs, filters)
        valid_ids = {doc.id for doc in docs}
        snapshots = [s for s in snapshots if self._snapshot_document_id(s) in valid_ids]
        status = self._field(workspace, "status", "")
        pending = investigation_snapshot is None and status in {"planning_completed", "queued", "running"}
        if investigation_snapshot is None and not snapshots and not docs:
            pending = True
        limitations: list[str] = []
        if pending:
            limitations.append("Canonical investigation snapshot is pending.")
        return {
            "snapshots": snapshots,
            "docs": docs,
            "generation": generation,
            "snapshot_id": snapshot_id,
            "pending": pending,
            "fallback": not bool(investigation_snapshot),
            "limitations": limitations,
            "workspace": workspace,
            "investigation_snapshot": investigation_snapshot,
            "cache_token": captured_token,
            "global_provider": False,
        }

    def _workspace(self, investigation_id: str) -> Any | None:
        if self.repository is not None:
            getter = getattr(self.repository, "get_investigation_workspace", None)
            if getter:
                try:
                    return getter(investigation_id)
                except Exception:
                    pass
        try:
            from config import get_settings
            from services.investigation_repository import InvestigationRepository

            settings = self.settings or get_settings()
            return InvestigationRepository(settings.persistence_target).get_investigation_workspace(investigation_id)
        except Exception:
            return None

    def _hydrate_target_rows(self, rows: list[dict[str, Any]], context: dict[str, Any], filters: dict[str, Any]) -> None:
        """Hydrate only target IDs, preserving the 500-document outage bound."""
        wanted = {
            str(row.get("document_id") or row.get("id"))
            for row in rows
            if row.get("document_id") or row.get("id")
        }
        known = {doc.id for doc in context.get("docs", [])}
        missing = sorted(wanted - known)
        if not missing or self.repository is None:
            return
        try:
            extra = list(self.repository.documents(ids=missing, limit=len(missing)) or [])
        except Exception:
            return
        snapshots = context.setdefault("snapshots", [])
        existing_snapshots = {str(item.get("snapshot_id")) for item in snapshots if isinstance(item, dict)}
        snapshots.extend(item for item in extra if str(item.get("snapshot_id")) not in existing_snapshots)
        docs = self._documents_from_snapshots(
            extra,
            include_ineligible=bool(filters.get("include_leads")),
        )
        docs_by_id = {doc.id: doc for doc in context.get("docs", [])}
        docs_by_id.update({doc.id: doc for doc in self._apply_filters(docs, filters)})
        context["docs"] = list(docs_by_id.values())

    def _default_projection_repository(self) -> Any | None:
        try:
            from services.b5_repository import ProjectionRepository

            candidate = self.repository_target
            if candidate in {"elasticsearch", "neo4j", "minilm", "canonical", None}:
                candidate = getattr(self.settings, "persistence_target", None) if self.settings is not None else None
            return ProjectionRepository(candidate or self.target)
        except Exception:
            return None

    def _generation(self) -> str | int:
        if self.repository is not None:
            try:
                manifest = self.repository.manifest() or {}
                return manifest.get("generation", "current")
            except Exception:
                pass
        return getattr(self.settings, "B5_GENERATION", "current") if self.settings is not None else "current"

    @staticmethod
    def _investigation_document_ids(snapshot: Any, workspace: Any) -> list[str]:
        data = snapshot.get("data", {}) if isinstance(snapshot, dict) else {}
        ids = data.get("document_snapshot_ids") or data.get("document_ids") or []
        if ids:
            return [str(item) for item in ids]
        retrieval = SearchService._field(workspace, "retrieval", None)
        if retrieval is not None:
            return [str(item) for item in SearchService._field(retrieval, "retrieved_document_ids", [])]
        return [str(doc.id) for doc in SearchService._field(workspace, "retrieved_documents", [])]

    @staticmethod
    def _snapshot_document_id(snapshot: Any) -> str | None:
        if not isinstance(snapshot, dict):
            return None
        data = snapshot.get("data") or {}
        document = data.get("document") if isinstance(data, dict) else None
        return str((document or {}).get("id") or snapshot.get("snapshot_id") or "") or None

    @staticmethod
    def _documents_from_snapshots(snapshots: Iterable[Any], *, include_ineligible: bool = False) -> list[Document]:
        docs: list[Document] = []
        for snapshot in snapshots or []:
            if isinstance(snapshot, Document):
                docs.append(snapshot)
                continue
            if not isinstance(snapshot, dict):
                continue
            data = snapshot.get("data") or {}
            if data.get("withdrawn") or snapshot.get("operation") in {"withdraw", "withdrawal"}:
                continue
            if snapshot.get("eligible") is False and not include_ineligible:
                continue
            raw = data.get("document") or snapshot.get("document")
            if not raw:
                continue
            try:
                document = raw if isinstance(raw, Document) else Document.model_validate(raw)
                if include_ineligible and snapshot.get("eligible") is False:
                    document = document.model_copy(update={
                        "metadata": {
                            **(document.metadata or {}),
                            "acquisition_receipt_valid": False,
                            "source_kind": data.get("source_kind", "discovery"),
                            "lead": True,
                        }
                    })
                docs.append(document)
            except Exception:
                continue
        return list({doc.id: doc for doc in docs}.values())

    def _elastic_search(self, query: str, *, mode: str, document_ids: list[str] | None, filters: dict[str, Any], generation: Any) -> list[dict[str, Any]]:
        if self.elastic is None:
            raise RuntimeError("Elasticsearch target is not configured")
        result = self.elastic.search(query,mode=mode,document_ids=document_ids,filters=filters,limit=_MAX_LANE,generation=generation)
        return [self._as_mapping(item) for item in (result or [])]

    def _semantic_search(self, query: str, *, document_ids: list[str] | None, filters: dict[str, Any], generation: Any) -> list[dict[str, Any]]:
        if self.repository is not None:
            store = getattr(self.repository, "semantic_store", None)
        else:
            store = None
        if store is None:
            store = getattr(self, "semantic", None)
        if store is None and self.settings is not None and getattr(self.settings, "ENABLE_POSTGRES_VECTOR_SEARCH", False):
            database_url = getattr(self.settings, "DATABASE_URL", "")
            if database_url:
                try:
                    from services.postgres_corpus import PostgresCorpusStore

                    store = PostgresCorpusStore(database_url, embedding_service=self.embedding_service)
                except Exception:
                    logger.debug("Unable to initialize semantic corpus store", exc_info=True)
        if store is None:
            raise RuntimeError("MiniLM corpus is not configured")
        if self.embedding_service is None:
            try:
                from services.embedding_service import get_embedding_service

                self.embedding_service = get_embedding_service()
            except Exception:
                raise RuntimeError("MiniLM query inference unavailable")
        model = getattr(self.embedding_service, "model_name", None)
        expected_model = "sentence-transformers/all-MiniLM-L6-v2"
        accepted_models = {expected_model, "all-MiniLM-L6-v2"}
        if model and str(model) not in accepted_models:
            raise ValueError(f"Embedding model mismatch: expected {expected_model!r}, got {model!r}.")
        vector = self.embedding_service.embed_query(query)
        from services.postgres_corpus import validate_embedding,_vector_literal,embedding_input_hash
        vector = validate_embedding(vector,model_name=expected_model,expected_model=expected_model)
        if vector is None or not any(vector):
            raise RuntimeError("MiniLM query inference unavailable")
        _vector_literal(vector)
        rows = store.search(
            vector,
            limit=_MAX_LANE,
            model_name=expected_model,
            document_ids=document_ids,
            filters=filters,
        )
        normalized: list[dict[str, Any]] = []
        for item in rows or []:
            mapped = self._as_mapping(item)
            document = mapped.get("document")
            if document is not None:
                from services.b5_repository import evidence_hash
                raw = document.model_dump(mode="json") if hasattr(document,"model_dump") else document
                material = Document.model_validate(raw)
                recorded_hash = mapped.get("embedding_input_hash")
                if recorded_hash is not None and recorded_hash != embedding_input_hash(material):
                    continue
                document_id = getattr(document, "id", None) or (document.get("id") if isinstance(document, dict) else None)
                mapped = {"document_id": document_id, "score": mapped.get("score", 0.0), "revision": mapped.get("revision"), "semantic_hash": mapped.get("semantic_hash"),"evidence_hash":evidence_hash(raw)}
            normalized.append(mapped)
        return normalized

    @staticmethod
    def _validated_lane(rows: list[dict[str, Any]], snapshots: dict[str, Any], candidate_ids: set[str]) -> list[dict[str, Any]]:
        valid: list[dict[str, Any]] = []
        for row in rows[:_MAX_LANE]:
            doc_id = str(row.get("document_id") or row.get("id") or "")
            snapshot = snapshots.get(doc_id)
            if not doc_id or doc_id not in candidate_ids or not snapshot:
                continue
            revision = row.get("revision")
            semantic_hash = row.get("semantic_hash")
            if revision is not None and str(revision) != str(snapshot.get("revision")):
                continue
            if semantic_hash is not None and str(semantic_hash) != str(snapshot.get("semantic_hash")):
                continue
            if row.get("evidence_hash"):
                from services.b5_repository import evidence_hash
                if row["evidence_hash"] != evidence_hash(snapshot["data"]["document"]):
                    continue
            valid.append({"document_id": doc_id, "score": float(row.get("score", 0.0) or 0.0), "revision": snapshot.get("revision"), "semantic_hash": snapshot.get("semantic_hash")})
        return valid

    # ------------------------------------------------------------------
    # Ranking, filters and exact phrase spans
    # ------------------------------------------------------------------
    def _local_lexical(self, query: str, docs: list[Document], filters: dict[str, Any], *, mode: str = "phrase") -> list[dict[str, Any]]:
        needles = self._query_phrases(query)
        rows: list[dict[str, Any]] = []
        for doc in self._apply_filters(docs, filters):
            haystack = f"{doc.title} {doc.snippet or ''} {doc.text}"
            normalized = self._normalize_with_map(haystack)[0]
            if mode in {"fulltext", "hybrid"}:
                terms = [self._normalize_with_map(term)[0] for term in re.findall(r"\w+", query, flags=re.UNICODE)]
                score = sum(normalized.count(term) for term in terms if term)
            else:
                score = sum(normalized.count(self._normalize_with_map(needle)[0]) for needle in needles)
            if score:
                rows.append({"document_id": doc.id, "score": float(score)})
        rows.sort(key=lambda row: (-row["score"], row["document_id"]))
        return rows[:_MAX_LANE]

    @staticmethod
    def _apply_filters(docs: list[Document], filters: dict[str, Any]) -> list[Document]:
        def date_value(value: Any) -> datetime | None:
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            if value:
                try:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    return None
            return None

        unknown_dates = bool(filters.get("include_unknown_dates", False))
        include_leads = bool(filters.get("include_leads", False))
        published_after = date_value(filters.get("published_after"))
        published_before = date_value(filters.get("published_before"))
        collected_after = date_value(filters.get("collected_after"))
        collected_before = date_value(filters.get("collected_before"))
        source_id = filters.get("source_id")
        source_type = filters.get("source_type")
        language = filters.get("language")
        result: list[Document] = []
        for doc in docs:
            published_at = doc.published_at
            collected_at = doc.collected_at
            if published_at is not None and published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            if collected_at is not None and collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=timezone.utc)
            metadata = doc.metadata or {}
            if (published_after or published_before) and metadata.get("event_time_quality") == "collected_at_fallback" and not unknown_dates:
                continue
            if not include_leads and (metadata.get("source_kind") == "discovery" or metadata.get("lead") is True or metadata.get("citable") is False):
                continue
            if source_id and doc.source_id != source_id:
                continue
            if source_type and doc.source_type != source_type:
                continue
            if language and doc.language != language:
                continue
            if published_after and (published_at is None and not unknown_dates or published_at and published_at < published_after):
                continue
            if published_before and (published_at is None and not unknown_dates or published_at and published_at > published_before):
                continue
            if collected_after and (collected_at is None and not unknown_dates or collected_at and collected_at < collected_after):
                continue
            if collected_before and (collected_at is None and not unknown_dates or collected_at and collected_at > collected_before):
                continue
            result.append(doc)
        return result

    @staticmethod
    def _query_phrases(query: str) -> list[str]:
        phrases = [item for item in re.findall(r"[\"']([^\"']+)[\"']", query) if item.strip()]
        return phrases or [query]

    @staticmethod
    def _normalize_with_map(text: str) -> tuple[str, list[int], list[int]]:
        chars: list[str] = []
        starts: list[int] = []
        ends: list[int] = []
        pending_space: tuple[int, int] | None = None
        # Normalize composing sequences together and retain their original span.
        # This also handles Hangul Jamo, whose combining class is zero.
        clusters = []
        index = 0
        while index < len(text):
            end = index + 1
            while end < len(text) and (unicodedata.combining(text[end]) or
                    unicodedata.normalize("NFC", text[index:end + 1]) != unicodedata.normalize("NFC", text[index:end]) + unicodedata.normalize("NFC", text[end])):
                end += 1
            clusters.append((index, end, text[index:end]))
            index = end
        for index, end, char in clusters:
            if char.isspace():
                if chars and pending_space is None:
                    pending_space = (index, index + 1)
                continue
            folded = unicodedata.normalize("NFC", char).casefold()
            if not folded:
                continue
            if pending_space is not None:
                chars.append(" ")
                starts.append(pending_space[0])
                ends.append(pending_space[1])
                pending_space = None
            for folded_char in folded:
                chars.append(folded_char)
                starts.append(index)
                ends.append(end)
        return "".join(chars), starts, ends

    @classmethod
    def _spans(cls, text: str, query: str) -> list[dict[str, Any]]:
        normalized, starts, ends = cls._normalize_with_map(text)
        spans: list[dict[str, Any]] = []
        for phrase in cls._query_phrases(query):
            needle = cls._normalize_with_map(phrase)[0]
            if not needle:
                continue
            cursor = 0
            while True:
                found = normalized.find(needle, cursor)
                if found < 0:
                    break
                end_pos = found + len(needle)
                start = starts[found]
                end = ends[end_pos - 1]
                spans.append({"start": start, "end": end, "text": text[start:end]})
                cursor = end_pos
        unique = {(item["start"], item["end"]): item for item in spans}
        return [unique[key] for key in sorted(unique)]

    @classmethod
    def _fuse(cls, lanes: dict[str, list[dict[str, Any]]], docs: dict[str, Document], snapshots: dict[str, Any], query: str) -> list[dict[str, Any]]:
        contributions: dict[str, dict[str, float]] = defaultdict(dict)
        for lane, rows in lanes.items():
            for rank, row in enumerate(rows[:_MAX_LANE], 1):
                doc_id = str(row["document_id"])
                contributions[doc_id][lane] = round(1.0 / (_RRF_K + rank), 8)
        fused: list[dict[str, Any]] = []
        for doc_id, lane_scores in contributions.items():
            doc = docs.get(doc_id)
            if doc is None:
                continue
            snapshot = snapshots.get(doc_id, {})
            fused.append({
                "document_id": doc_id,
                "title": doc.title,
                "source_name": doc.source_name,
                "url": doc.url,
                "citable": cls._citable(doc, snapshot),
                "score": round(sum(lane_scores.values()), 8),
                "contributions": lane_scores,
                "spans": cls._spans(doc.text, query),
                "revision": snapshot.get("revision"),
                "semantic_hash": snapshot.get("semantic_hash"),
            })
        fused.sort(key=lambda item: (-item["score"], item["document_id"]))
        return fused

    @staticmethod
    def _citable(doc: Document, snapshot: dict[str, Any]) -> bool:
        data = snapshot.get("data") or {}
        if data.get("source_kind") == "discovery" or snapshot.get("eligible") is False or data.get("withdrawn"):
            return False
        metadata = doc.metadata or {}
        return metadata.get("acquisition_receipt_valid") is True

    # ------------------------------------------------------------------
    # Graph helpers and response formatting
    # ------------------------------------------------------------------
    @staticmethod
    def _canonical_graph(docs: list[Document]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        nodes = [
            {
                "id": doc.id, "kind": "document", "label": doc.source_name,
                "document_id": doc.id, "title": doc.title, "url": doc.url,
                "source_type": doc.source_type,
            }
            for doc in docs
        ]
        edges: list[dict[str, Any]] = []
        ordered = sorted(docs,key=lambda doc:(doc.published_at.replace(tzinfo=timezone.utc) if doc.published_at and not doc.published_at.tzinfo else doc.published_at) or datetime.max.replace(tzinfo=timezone.utc))
        for left, right in zip(ordered, ordered[1:]):
            edges.append({
                "id": f"canonical:{left.id}:{right.id}",
                "source": left.id, "target": right.id,
                "relationship": "temporal_sequence", "evidence_class": "inferred",
                "method": "canonical.timeline", "method_version": "b5-search-v1",
                "document_id": right.id, "investigation_id": None,
                "confidence": 0.3, "evidence": {}, "limitations": [
                    "Temporal ordering is a bounded fallback and does not establish causality."
                ], "snapshot_hash": "",
            })
        return nodes, edges

    @staticmethod
    def _validate_graph_revision(payload: dict[str,Any],context: dict[str,Any]) -> None:
        snapshot = context.get("investigation_snapshot")
        if snapshot and "revision" in payload and (payload.get("revision"),payload.get("semantic_hash")) != (snapshot["revision"],snapshot["semantic_hash"]):
            context["pending"] = True
            raise RuntimeError("Neo4j investigation projection is pending or stale")

    def _annotate_projection(self,response: dict[str,Any],context: dict[str,Any],targets: list[str]) -> dict[str,Any]:
        if self.repository is not None and hasattr(self.repository,"coverage"):
            snapshots = list(context.get("snapshots",[]))
            if "neo4j" in targets and context.get("investigation_snapshot"):
                snapshots.append(context["investigation_snapshot"])
            coverage = self.repository.coverage(snapshots,context["generation"])
            response["coverage"] = {target:coverage[target] for target in targets}
            if any(coverage[target]["pending"] for target in targets):
                response["pending"] = True
                response["complete"] = False
                response["limitations"] = list(dict.fromkeys([*response["limitations"],"Required projection coverage has not reached the current canonical revision."]))
        response["freshness"] = {"state":"stale" if any("stale" in str(item).lower() for item in response["limitations"]) else "pending" if response["pending"] else "fallback" if response["fallback_active"] else "current",
            "generation":context["generation"],"snapshot_id":context.get("snapshot_id")}
        if response["fallback_active"]:
            response["complete"] = False
        return response

    @staticmethod
    def _validate_graph_payload(payload: Any, allowed: set[str], include_inferred: bool, relationships: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        payload = payload or {}
        nodes = [SearchService._as_mapping(node) for node in payload.get("nodes", [])]
        edges = [SearchService._as_mapping(edge) for edge in payload.get("edges", [])]
        blocked = {str(node.get("id")) for node in nodes if
            (node.get("kind")=="document" and str(node.get("document_id") or node.get("id")) not in allowed)
            or (node.get("document_id") and str(node["document_id"]) not in allowed)}
        nodes = [node for node in nodes if str(node.get("id")) not in blocked]
        edges = [edge for edge in edges if str(edge.get("source")) not in blocked and str(edge.get("target")) not in blocked
            and (not edge.get("document_id") or str(edge["document_id"]) in allowed)]
        # Keep graph hubs (sources, entities, claims) only when an edge ties
        # them to an acquired document.  This prevents a target from leaking
        # another investigation's graph while preserving useful provenance
        # context around the bounded document set.
        edges = [
            edge for edge in edges
            if (
                str(edge.get("source")) in allowed
                or str(edge.get("target")) in allowed
                or str(edge.get("document_id")) in allowed
            )
        ]
        referenced = allowed | {
            str(endpoint)
            for edge in edges
            for endpoint in (edge.get("source"), edge.get("target"))
            if endpoint is not None
        }
        nodes = [node for node in nodes if str(node.get("id")) in referenced]
        if not include_inferred:
            edges = [
                edge for edge in edges
                if not edge.get("inferred", False)
                and edge.get("provenance") != "inferred"
                and edge.get("evidence_class") != "inferred"
            ]
        if relationships:
            raw_relationships = relationships if isinstance(relationships, (list, tuple, set)) else [relationships]
            wanted = {item.strip() for value in raw_relationships for item in str(value).split(",") if item.strip()}
            edges = [edge for edge in edges if edge.get("type", edge.get("edge_type", edge.get("relationship"))) in wanted]
        return nodes, edges

    @staticmethod
    def _paths_from_edges(edges: list[dict[str, Any]], start: str, goal: str, max_depth: int) -> list[list[str]]:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            if edge.get("relationship") not in {"references","exact_duplicate_of","mutation","phrase_reuse","amplifies"} or edge.get("evidence_class") != "observed":
                continue
            adjacency[str(edge.get("source"))].append(str(edge.get("target")))
        found: list[list[str]] = []
        stack: list[tuple[str, list[str]]] = [(start, [start])]
        while stack and len(found) < 10:
            current, path = stack.pop(0)
            if current == goal:
                found.append(path)
                continue
            if len(path) - 1 >= max_depth:
                continue
            for child in adjacency.get(current, []):
                if child not in path:
                    stack.append((child, path + [child]))
        return found

    @staticmethod
    def _search_response(investigation_id: str | None, query: str, mode: str, results: list[dict[str, Any]], total: int, offset: int, generation: Any, *, source: str, fallback_active: bool, pending: bool, complete: bool, limitations: list[str], snapshot_id: Any = None) -> dict[str, Any]:
        response = {
            "investigation_id": investigation_id,
            "query": query,
            "mode": mode,
            "results": results,
            "total": total,
            "next_offset": offset + len(results) if offset + len(results) < total else None,
            "cached": False,
            "source": source,
            "fallback_active": fallback_active,
            "pending": pending,
            "complete": complete,
            "limitations": limitations,
            "generation": generation,
        }
        if snapshot_id is not None:
            response["snapshot_id"] = snapshot_id
        return response

    @staticmethod
    def _graph_response(investigation_id: str, nodes: list[Any], edges: list[Any], context: dict[str, Any], limitations: list[str], *, source: str = "canonical", fallback_active: bool = True, truncated: bool = False) -> dict[str, Any]:
        response = {
            "investigation_id": investigation_id, "nodes": nodes, "edges": edges,
            "source": source, "fallback_active": fallback_active,
            "pending": context["pending"], "complete": not context["pending"],
            "limitations": limitations, "truncated": truncated,
            "generation": context["generation"],
            "cached": False,
        }
        if context.get("snapshot_id") is not None:
            response["snapshot_id"] = context["snapshot_id"]
        return response

    @staticmethod
    def _paths_response(investigation_id: str, paths: list[Any], context: dict[str, Any], limitations: list[str], *, source: str = "canonical", fallback_active: bool = True) -> dict[str, Any]:
        response = {
            "investigation_id": investigation_id, "paths": paths,
            "source": source, "fallback_active": fallback_active,
            "pending": context["pending"], "complete": not context["pending"],
            "limitations": limitations, "truncated": len(paths) > 10,
            "generation": context["generation"],
            "cached": False,
        }
        if context.get("snapshot_id") is not None:
            response["snapshot_id"] = context["snapshot_id"]
        return response

    @staticmethod
    def _as_mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return {}

    @staticmethod
    def _field(value: Any, name: str, default: Any) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    @staticmethod
    def _context_revision_hash(context: dict[str, Any]) -> tuple[Any, str | None]:
        hashes = [str(s.get("semantic_hash")) for s in context.get("snapshots", [])]
        cache_token = context.get("cache_token")
        revision = cache_token if cache_token is not None else context.get("generation")
        return (revision, ":".join(hashes) if hashes else None)

    def _read_cache_token(self, kind: str, domain_id: str | None, generation: Any) -> Any:
        if self.repository is None or not domain_id:
            return None
        try:
            return self.repository.cache_token(kind, domain_id, self.target, generation)
        except Exception:
            return None

    def _canonical_token_matches(self, context: dict[str, Any], domain_id: str | None, generation: Any) -> bool:
        captured = context.get("cache_token")
        if captured is None:
            return True
        current = (
            self._read_cache_token("investigation", domain_id, generation),
            self._read_cache_token("document", None, generation),
        )
        return current == captured

    @staticmethod
    def _cache_kind(query: str, mode: str, filters: dict[str, Any]) -> str:
        encoded = json.dumps(filters, sort_keys=True, default=str, separators=(",", ":"))
        digest = hashlib.sha256(f"{mode}\0{query}\0{encoded}".encode("utf-8")).hexdigest()[:24]
        return f"search:{digest}"

    @staticmethod
    def _cache_valid(value: dict[str, Any], context: dict[str, Any], query: str, mode: str) -> bool:
        if value.get("query") != query or value.get("mode") != mode:
            return False
        if value.get("pending") or value.get("fallback_active") or not value.get("complete"):
            return False
        if value.get("generation") != context.get("generation"):
            return False
        current = {
            SearchService._snapshot_document_id(snapshot): (snapshot.get("revision"), snapshot.get("semantic_hash"))
            for snapshot in context.get("snapshots", [])
        }
        for row in value.get("results", []):
            key = str(row.get("document_id"))
            if key not in current:
                return False
            if (row.get("revision"), row.get("semantic_hash")) != current[key]:
                return False
        return True

    @staticmethod
    def _graph_cache_valid(value: dict[str, Any], context: dict[str, Any]) -> bool:
        return bool(
            value.get("generation") == context.get("generation")
            and value.get("complete")
            and not value.get("pending")
            and not value.get("fallback_active")
            and isinstance(value.get("nodes"), list)
            and isinstance(value.get("edges"), list)
        )

    @staticmethod
    def _paths_cache_valid(value: dict[str, Any], context: dict[str, Any]) -> bool:
        return bool(
            value.get("generation") == context.get("generation")
            and value.get("complete")
            and not value.get("pending")
            and not value.get("fallback_active")
            and isinstance(value.get("paths"), list)
        )
