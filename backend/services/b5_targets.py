"""B5 Elasticsearch and Neo4j projection targets.

Both adapters are intentionally small boundaries around the graph builders.
They use explicit mappings and fixed Cypher statements; caller supplied data
is passed as parameters and is never interpolated into a query.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import httpx

from flink.contracts import canonical_json
from services.b5_graph import build_document_graph, build_investigation_graph


def _setting(settings: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = getattr(settings, name, None)
        if value not in (None, ""):
            return value
    return default


def _safe_generation(generation: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(generation or "live")).strip("-").lower()
    return value or "live"


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value))


class ElasticsearchTarget:
    """HTTP REST adapter with revision and semantic-hash guards."""

    INDEX_PREFIX = "rhetoriq-documents-b5"

    def __init__(self, settings: Any, *, transport: httpx.BaseTransport | None = None, client: Any = None) -> None:
        self.settings = settings
        self.transport = transport
        self._client = client
        self._owned_client = client is None
        self._initialized: set[str] = set()

    @property
    def base_url(self) -> str:
        return str(_setting(self.settings, "ELASTICSEARCH_URL", "ES_URL", default="")).rstrip("/")

    @property
    def index_prefix(self) -> str:
        return str(_setting(self.settings, "ELASTICSEARCH_INDEX_PREFIX", "ES_INDEX_PREFIX", default=self.INDEX_PREFIX)).strip("/")

    def _index(self, generation: str) -> str:
        return f"{self.index_prefix}-{_safe_generation(generation)}"

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.base_url:
                raise RuntimeError("Elasticsearch target is not configured")
            username = _setting(self.settings, "ELASTICSEARCH_USERNAME", "ES_USERNAME", default=None)
            password = _setting(self.settings, "ELASTICSEARCH_PASSWORD", "ES_PASSWORD", default=None)
            ca_cert = _setting(self.settings, "ELASTICSEARCH_CA_CERT", "ELASTICSEARCH_CA", "ES_CA_CERT", default=None)
            verify: str | bool = ca_cert if ca_cert else True
            auth = (str(username), str(password)) if username is not None and password is not None else None
            self._client = httpx.Client(base_url=self.base_url, auth=auth, verify=verify, transport=self.transport, timeout=float(_setting(self.settings, "B5_QUERY_TIMEOUT_SECONDS", default=3.0)), follow_redirects=False)
        return self._client

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._get_client().request(method, path, **kwargs)
        return response

    def _bulk(self,index: str,actions: list[dict[str,Any]]) -> None:
        body = bytearray()
        count = 0
        def flush():
            if not body:
                return
            response = self._request("POST",f"/{index}/_bulk",content=bytes(body),headers={"content-type":"application/x-ndjson"})
            response.raise_for_status()
            if self._json(response).get("errors"):
                raise RuntimeError("Elasticsearch bulk contains failed items")
            body.clear()
        for position in range(0,len(actions),2):
            pair = ("\n".join(json.dumps(item,ensure_ascii=False,separators=(",",":")) for item in actions[position:position+2])+"\n").encode("utf-8")
            if len(pair)>4*1024*1024:
                raise ValueError("Elasticsearch document exceeds bounded bulk size")
            if count>=100 or len(body)+len(pair)>4*1024*1024:
                flush()
                count = 0
            body.extend(pair)
            count += 1
        flush()

    @staticmethod
    def _json(response: Any) -> dict[str, Any]:
        try:
            value = response.json()
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def initialize(self, generation: str) -> dict[str, Any]:
        index = self._index(generation)
        if _safe_generation(generation) in self._initialized:
            return {"status": "ready", "index": index, "created": False}
        mappings = {
            "dynamic": "strict",
            "properties": {
                "kind": {"type": "keyword"},
                "operation": {"type": "keyword"},
                "withdrawn": {"type": "boolean"},
                "source_id": {"type": "keyword"},
                "source_name": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "domain_id": {"type": "keyword"},
                "generation": {"type": "keyword"},
                "revision": {"type": "long"},
                "semantic_hash": {"type": "keyword"},
                "eligible": {"type": "boolean"},
                "published_at": {"type": "date"},
                "collected_at": {"type": "date"},
                "source_type": {"type": "keyword"},
                "source_kind": {"type": "keyword"},
                "language": {"type": "keyword"},
                "event_time_quality": {"type": "keyword"},
                "phrases": {"type": "keyword"},
                "entities": {"type": "keyword"},
                "mentions": {
                    "type": "nested",
                    "properties": {
                        "kind": {"type": "keyword"},
                        "id": {"type": "keyword"},
                        "value": {"type": "keyword"},
                        "surface_form": {"type": "keyword"},
                        "start": {"type": "integer"},
                        "end": {"type": "integer"},
                    },
                },
                "text": {"type": "text"},
                "title": {"type": "text"},
                "url": {"type": "keyword", "index": False},
                "graph_json": {"type": "object", "enabled": False},
            }
        }
        if not self.base_url:
            return {"status": "unconfigured", "index": index}
        response = self._request("PUT", f"/{index}", json={"settings": {"number_of_shards": 1, "number_of_replicas": 0}, "mappings": mappings})
        if response.status_code == 400 and self._json(response).get("error", {}).get("type") != "resource_already_exists_exception":
            response.raise_for_status()
        if response.status_code not in {200, 201, 400}:
            response.raise_for_status()
        self._initialized.add(_safe_generation(generation))
        return {"status": "ready", "index": index, "created": response.status_code in {200, 201}}

    def _existing(self, index: str, document_id: str) -> dict[str, Any] | None:
        response = self._request("GET", f"/{index}/_doc/{_safe_id(document_id)}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            response.raise_for_status()
        payload = self._json(response)
        source = payload.get("_source")
        return source if isinstance(source, dict) else None

    @staticmethod
    def _mentions(graph: dict[str, Any]) -> list[dict[str, Any]]:
        mentions: list[dict[str, Any]] = []
        for edge in graph.get("edges", []):
            if not isinstance(edge, dict) or edge.get("relationship") not in {"mentions_phrase", "mentions_entity"}:
                continue
            evidence = edge.get("evidence") or {}
            item = {"kind": "phrase" if edge["relationship"] == "mentions_phrase" else "entity", "id": edge.get("target"), "value": evidence.get("phrase") or evidence.get("entity")}
            for key in ("surface_form", "start", "end"):
                if key in evidence:
                    item[key] = evidence[key]
            mentions.append(item)
        return sorted(mentions, key=lambda item: (str(item.get("kind")), str(item.get("id"))))

    def _source(self, snapshot: dict[str, Any], generation: str) -> tuple[str, dict[str, Any]]:
        graph = build_document_graph(snapshot)
        data = snapshot["data"]
        document = data["document"]
        document_id = str(document["id"])
        source = {
            "document_id": document_id,
            "domain_id": str(snapshot["domain_id"]),
            "generation": _safe_generation(generation),
            "kind": "document",
            "revision": int(snapshot["revision"]),
            "semantic_hash": str(snapshot["semantic_hash"]),
            "eligible": bool(snapshot["eligible"]),
            "operation": snapshot["operation"],
            "withdrawn": bool(data.get("withdrawn")) or snapshot["operation"] == "withdraw",
            "published_at": document.get("published_at"),
            "collected_at": document.get("collected_at"),
            "source_type": document.get("source_type"),
            "source_kind": data.get("source_kind"),
            "language": document.get("language"),
            "event_time_quality": (document.get("metadata") or {}).get("event_time_quality", "published_at" if document.get("published_at") else "unknown"),
            "source_id": document.get("source_id"),
            "source_name": document.get("source_name"),
            "title": document.get("title"),
            "text": document.get("text"),
            "url": document.get("url"),
            "phrases": sorted({_value for _value in [str(item.get("label")) for item in graph["nodes"] if item.get("kind") == "phrase"] if _value}),
            "entities": sorted({_value for _value in [str(item.get("label")) for item in graph["nodes"] if item.get("kind") == "entity"] if _value}),
            "mentions": self._mentions(graph),
            "graph_json": graph,
        }
        return document_id, source

    def apply(self, snapshot: dict[str, Any], generation: str, document_snapshots: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("Elasticsearch target is not configured")
        snapshots = [snapshot]
        if snapshot.get("kind") == "investigation":
            snapshots = list(document_snapshots or [])
        if not snapshots:
            return {"status": "skipped", "reason": "no_document_snapshots"}
        self.initialize(generation)
        index = self._index(generation)
        actions: list[dict[str, Any]] = []
        applied: list[str] = []
        skipped: list[str] = []
        for candidate in snapshots:
            if candidate.get("kind") != "document":
                continue
            document_id, source = self._source(candidate, generation)
            existing = self._existing(index, document_id) if self.base_url else None
            incoming_revision = int(candidate["revision"])
            if existing is not None:
                current_revision = int(existing.get("revision", -1))
                current_hash = str(existing.get("semantic_hash", ""))
                if incoming_revision < current_revision or (incoming_revision == current_revision and current_hash == str(candidate["semantic_hash"])):
                    skipped.append(document_id)
                    continue
                if incoming_revision == current_revision and current_hash != str(candidate["semantic_hash"]):
                    raise ValueError("Elasticsearch equal-revision hash conflict")
            actions.extend([{"index": {"_index": index, "_id": document_id, "version": incoming_revision, "version_type": "external"}}, source])
            applied.append(document_id)
        if actions and self.base_url:
            self._bulk(index,actions)
        return {"status": "applied", "applied": applied, "skipped": skipped, "index": index}

    def repair(self, snapshot: dict[str, Any], generation: str, document_snapshots: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Reconstruct owned records at the canonical revision.

        Repair uses ``external_gte`` so an operator can restore a missing or
        corrupted record at the same revision.  A newer record is still left
        untouched; callers must reconcile that collision explicitly.
        """

        snapshots = [snapshot] if snapshot.get("kind") == "document" else list(document_snapshots or [])
        if not snapshots:
            return {"status": "capability_error", "reason": "repair_requires_document_snapshots"}
        self.initialize(generation)
        index = self._index(generation)
        actions: list[dict[str, Any]] = []
        repaired: list[str] = []
        blocked: list[str] = []
        for candidate in snapshots:
            if candidate.get("kind") != "document":
                continue
            document_id, source = self._source(candidate, generation)
            existing = self._existing(index, document_id) if self.base_url else None
            if existing is not None and int(existing.get("revision", -1)) > int(candidate["revision"]):
                blocked.append(document_id)
                continue
            actions.extend([{"index": {"_index": index, "_id": document_id, "version": int(candidate["revision"]), "version_type": "external_gte"}}, source])
            repaired.append(document_id)
        if actions and self.base_url:
            self._bulk(index,actions)
        return {"status": "repaired", "repaired": repaired, "blocked": blocked, "index": index}

    def remove_unexpected(self, domain_id: str, generation: str, kind: str = "document", *, expected_ids: list[str] | None = None) -> dict[str, Any]:
        """Delete only explicitly identified records in one generation scope.

        Without canonical IDs this operation cannot safely determine what is
        unexpected, so it returns a capability error instead of broad deletion.
        """

        if kind not in {"document", "investigation"}:
            return {"status": "capability_error", "reason": "unsupported_kind"}
        if not self.base_url:
            return {"status": "unconfigured", "removed": 0}
        filters = [{"term": {"domain_id": str(domain_id)}}, {"term": {"generation": _safe_generation(generation)}}, {"term": {"kind": kind}}]
        body_query: dict[str, Any] = {"bool": {"filter": filters}}
        if expected_ids is not None:
            ids = [str(item) for item in expected_ids][:1000]
            body_query["bool"]["must_not"] = [{"terms": {"document_id": ids}}]
        body = {"query": body_query}
        response = self._request("POST", f"/{self._index(generation)}/_delete_by_query", json=body)
        response.raise_for_status()
        payload = self._json(response)
        return {"status": "removed", "removed": payload.get("deleted", 0), "response": payload}

    def validate_snapshot(self, snapshot: dict[str, Any], generation: str, document_snapshots: list[dict[str, Any]] | None = None) -> list[str]:
        """Return bounded, identifier-only drift findings for this projection."""

        candidates = [snapshot] if snapshot.get("kind") == "document" else list(document_snapshots or [])
        findings: list[str] = []
        index = self._index(generation)
        for candidate in candidates[:1000]:
            if candidate.get("kind") != "document":
                continue
            try:
                document_id, expected = self._source(candidate, generation)
            except Exception:
                findings.append(f"invalid:{candidate.get('snapshot_id', 'unknown')}")
                continue
            if not self.base_url:
                findings.append("unconfigured:elasticsearch")
                break
            actual = self._existing(index, document_id)
            if actual is None:
                findings.append(f"missing:{document_id}")
                continue
            for field in ("revision", "semantic_hash", "eligible", "generation", "kind", "document_id"):
                if actual.get(field) != expected.get(field):
                    findings.append(f"mismatch:{document_id}:{field}")
            # graph_json contains the validated B4 offsets, source links, and
            # edge evidence.  Comparing its canonical form catches corruption
            # even if an operator left the owner revision/hash untouched.
            if canonical_json(actual.get("graph_json", {})) != canonical_json(expected.get("graph_json", {})):
                findings.append(f"mismatch:{document_id}:graph_payload")
        return sorted(set(findings))

    def search(self, query: str, mode: str, document_ids: list[str] | None, filters: dict[str, Any], limit: int = 50, generation: str = "live") -> list[dict[str, Any]]:
        self.initialize(generation)
        if not self.base_url:
            return []
        limit = max(1, min(int(limit), 200))
        mode = str(mode or "exact").lower()
        if document_ids == []:
            return []
        if mode in {"exact", "phrase"}:
            must: dict[str, Any] = {"match_phrase": {"text": {"query": query, "slop": 0}}}
        elif mode in {"fulltext", "lexical", "semantic", "broad", "match", "hybrid"}:
            must = {"multi_match": {"query": query, "fields": ["text", "title", "phrases"]}}
        else:
            raise ValueError(f"unsupported Elasticsearch search mode: {mode}")
        clauses: list[dict[str, Any]] = [{"term": {"withdrawn": False}}]
        if not filters.get("include_leads", False):
            clauses.append({"term": {"eligible": True}})
        if document_ids:
            clauses.append({"terms": {"document_id": [str(item) for item in document_ids]}})
        allowed_terms = {"domain_id", "source_type", "source_kind", "source_id", "language", "generation"}
        for key, value in (filters or {}).items():
            if key in allowed_terms and value not in (None, "", []):
                clauses.append({"terms": {key: value} if isinstance(value, list) else {"term": {key: value}}})
        range_query: dict[str, Any] = {}
        for key, op in (("published_after", "gte"), ("published_before", "lte"), ("collected_after", "gte"), ("collected_before", "lte")):
            if (filters or {}).get(key):
                field = "published_at" if key.startswith("published") else "collected_at"
                clauses.append({"range": {field: {op: filters[key]}}})
        if not (filters or {}).get("include_unknown_dates", False):
            if (filters or {}).get("published_after") or (filters or {}).get("published_before"):
                clauses.append({"exists": {"field": "published_at"}})
                clauses.append({"bool": {"must_not": [{"term": {"event_time_quality": "collected_at_fallback"}}]}})
            if (filters or {}).get("collected_after") or (filters or {}).get("collected_before"):
                clauses.append({"exists": {"field": "collected_at"}})
        body = {"size": limit, "query": {"bool": {"must": [must], "filter": clauses}}, "sort": [{"_score": "desc"}, {"document_id": "asc"}], "_source": ["document_id", "revision", "semantic_hash", "domain_id", "title", "url", "published_at", "source_type", "source_kind", "language", "collected_at"]}
        response = self._request("POST", f"/{self._index(generation)}/_search", json=body)
        response.raise_for_status()
        hits = self._json(response).get("hits", {}).get("hits", [])
        result: list[dict[str, Any]] = []
        for hit in hits if isinstance(hits, list) else []:
            source = hit.get("_source") if isinstance(hit, dict) else {}
            if not isinstance(source, dict):
                source = {}
            result.append({"document_id": source.get("document_id") or hit.get("_id"), "score": hit.get("_score"), "revision": source.get("revision"), "semantic_hash": source.get("semantic_hash"), **{key: source[key] for key in ("domain_id", "title", "url", "published_at", "source_type", "source_kind") if key in source}})
        return result

    def inventory(self, generation: str) -> dict[str, dict[str, Any]]:
        self.initialize(generation)
        if not self.base_url:
            return {}
        body = {"size": 1000, "_source": ["domain_id", "kind", "revision", "semantic_hash", "eligible"], "query": {"match_all": {}}, "sort": [{"document_id": "asc"}]}
        hits = []
        while True:
            response = self._request("POST", f"/{self._index(generation)}/_search", json=body)
            response.raise_for_status()
            page = self._json(response).get("hits", {}).get("hits", [])
            hits.extend(page)
            if len(page) < 1000:
                break
            cursor = page[-1].get("sort")
            if not cursor or cursor == body.get("search_after"):
                raise RuntimeError("Elasticsearch inventory pagination did not advance")
            body["search_after"] = cursor
        result: dict[str, dict[str, Any]] = {}
        for hit in hits if isinstance(hits, list) else []:
            source = (hit or {}).get("_source", {})
            if not isinstance(source, dict) or not source.get("domain_id"):
                continue
            key = str(source["domain_id"])
            if source.get("kind") == "investigation":
                key = f"investigation:{key}"
            current = result.get(key)
            if current is None or int(source.get("revision", -1)) >= int(current.get("revision", -1)):
                result[key] = {key: source.get(key) for key in ()}  # explicit shape below
                result[key] = {"revision": source.get("revision"), "semantic_hash": source.get("semantic_hash"), "eligible": source.get("eligible")}
        return result

    def health(self) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "unconfigured", "target": "elasticsearch"}
        try:
            response = self._request("GET", "/")
            return {"status": "healthy" if response.status_code < 400 else "unhealthy", "target": "elasticsearch", "status_code": response.status_code, "details": self._json(response)}
        except Exception as exc:
            return {"status": "unhealthy", "target": "elasticsearch", "error": str(exc)}

    def close(self) -> None:
        if self._owned_client and self._client is not None:
            self._client.close()
            self._client = None


_NEO_INIT_QUERIES = (
    "CREATE CONSTRAINT rhetoriq_projection_lock IF NOT EXISTS FOR (n:RhetoriqProjectionLock) REQUIRE (n.domain_id, n.generation) IS UNIQUE",
    "CREATE CONSTRAINT rhetoriq_graph_node IF NOT EXISTS FOR (n:RhetoriqGraphNode) REQUIRE n.projection_key IS UNIQUE",
    "CREATE INDEX rhetoriq_graph_node_scope IF NOT EXISTS FOR (n:RhetoriqGraphNode) ON (n.domain_id, n.generation)",
)
_NEO_LOCK_QUERY = """
MERGE (lock:RhetoriqProjectionLock {domain_id:$domain_id, generation:$generation})
ON CREATE SET lock.revision=-1, lock.semantic_hash='', lock.eligible=false
SET lock.write_lock=coalesce(lock.write_lock,0)+1
WITH lock
WHERE lock.revision < $revision OR (lock.revision = $revision AND lock.semantic_hash = $semantic_hash)
SET lock.revision=$revision, lock.semantic_hash=$semantic_hash, lock.eligible=$eligible, lock.operation=$operation
RETURN lock.revision AS revision, lock.semantic_hash AS semantic_hash
"""
_NEO_NODE_QUERY = """
MERGE (node:RhetoriqGraphNode {projection_key:$projection_key})
SET node += $properties
"""
_NEO_TYPED_NODE_QUERIES = {kind:_NEO_NODE_QUERY+f"\nSET node:{label}" for kind,label in {
    "document":"Document","source":"Source","phrase":"Phrase","entity":"Entity",
    "investigation":"Investigation","research_run":"ResearchRun","claim":"Claim",
    "evidence_span":"EvidenceSpan","receipt":"EvidenceSpan","enrichment":"EnrichmentArtifact",
    "reference_target":"ReferenceTarget"}.items()}
_NEO_EDGE_QUERY = """
MATCH (source:RhetoriqGraphNode {projection_key:$source_key})
MATCH (target:RhetoriqGraphNode {projection_key:$target_key})
MERGE (source)-[edge:RhetoriqProjected {projection_key:$projection_key}]->(target)
SET edge += $properties
"""
_NEO_REMOVE_QUERY = """
MATCH (:RhetoriqGraphNode)-[edge:RhetoriqProjected]->(:RhetoriqGraphNode)
WHERE edge.domain_id=$domain_id AND edge.generation=$generation AND edge.owned=true
  AND NOT edge.projection_key IN $edge_keys
DELETE edge
"""
_NEO_REMOVE_NODES_QUERY = """
MATCH (node:RhetoriqGraphNode)
WHERE node.domain_id=$domain_id AND node.generation=$generation AND node.owned=true
  AND NOT node.projection_key IN $node_keys
DETACH DELETE node
"""
_NEO_READ_QUERY = """
MATCH (node:RhetoriqGraphNode)
WHERE node.generation=$generation AND node.domain_id=$domain_id
  AND (node.investigation_id=$investigation_id OR node.document_id IN $document_ids OR $include_all_scope=true)
OPTIONAL MATCH (node)-[edge:RhetoriqProjected]->(target:RhetoriqGraphNode)
WHERE edge.generation=$generation
RETURN collect(DISTINCT node) AS nodes, collect(DISTINCT {source:node.projection_key, edge:edge, target:target}) AS edges
"""
_NEO_INVENTORY_QUERY = """
MATCH (lock:RhetoriqProjectionLock {generation:$generation})
RETURN lock.domain_id AS domain_id, lock.revision AS revision, lock.semantic_hash AS semantic_hash, lock.eligible AS eligible
"""


class Neo4jTarget:
    """Lazy official Neo4j driver adapter with generation scoped ownership."""

    def __init__(self, settings: Any, *, driver: Any = None) -> None:
        self.settings = settings
        self._driver = driver
        self._owned_driver = driver is None

    @property
    def url(self) -> str:
        return str(_setting(self.settings, "NEO4J_URL", default=""))

    def _get_driver(self) -> Any:
        if self._driver is None:
            if not self.url:
                raise RuntimeError("Neo4j target is not configured")
            from neo4j import GraphDatabase

            username = _setting(self.settings, "NEO4J_USERNAME", default="neo4j")
            password = _setting(self.settings, "NEO4J_PASSWORD", default="")
            kwargs: dict[str, Any] = {"auth": (username, password), "connection_timeout": float(_setting(self.settings, "B5_QUERY_TIMEOUT_SECONDS", default=3.0))}
            if self.url.startswith(("bolt://", "neo4j://")):
                kwargs["encrypted"] = True
            ca_cert = _setting(self.settings, "NEO4J_CA_CERT", default=None)
            if ca_cert and self.url.startswith(("bolt://", "neo4j://")):
                try:
                    from neo4j import TrustCustomCAs

                    kwargs["trusted_certificates"] = TrustCustomCAs(ca_cert)
                except ImportError:
                    pass
            self._driver = GraphDatabase.driver(self.url, **kwargs)
        return self._driver

    @staticmethod
    def _scope(snapshot: dict[str, Any]) -> str:
        return f"investigation:{snapshot['domain_id']}" if snapshot.get("kind") == "investigation" else str(snapshot["domain_id"])

    @staticmethod
    def _node_properties(node: dict[str, Any], domain_id: str, generation: str) -> dict[str, Any]:
        explicit = {key: node[key] for key in ("id", "kind", "label", "document_id", "investigation_id", "claim_id", "published_at", "timestamp_quality", "missing", "placeholder", "withdrawn", "eligible", "revision", "semantic_hash", "snapshot_hash", "url", "source_type", "source_id", "phrase", "entity", "evidence_side", "span_start", "span_end", "nli_verdict", "verification_status") if key in node}
        explicit.update({"domain_id": domain_id, "generation": generation, "owned": True, "payload_json": canonical_json(node)})
        return explicit

    @staticmethod
    def _edge_properties(edge: dict[str, Any], domain_id: str, generation: str) -> dict[str, Any]:
        explicit = {key: edge[key] for key in ("id", "relationship", "evidence_class", "method", "method_version", "document_id", "investigation_id", "confidence", "snapshot_hash") if key in edge}
        explicit.update({"domain_id": domain_id, "generation": generation, "owned": True, "payload_json": canonical_json(edge)})
        return explicit

    @staticmethod
    def _record_value(record: Any, key: str, default: Any = None) -> Any:
        if isinstance(record, dict):
            return record.get(key, default)
        try:
            return record[key]
        except Exception:
            return default

    @staticmethod
    def _run_tx(session: Any, callback: Callable[[Any], Any]) -> Any:
        if hasattr(session, "execute_write"):
            return session.execute_write(callback)
        if hasattr(session, "execute_write_transaction"):
            return session.execute_write_transaction(callback)
        transaction = session.begin_transaction()
        try:
            result = callback(transaction)
            transaction.commit()
            return result
        except Exception:
            transaction.rollback()
            raise

    def initialize(self, generation: str) -> dict[str, Any]:
        if not self.url and self._driver is None:
            return {"status": "unconfigured", "generation": _safe_generation(generation)}
        driver = self._get_driver()
        session = driver.session()
        try:
            for query in _NEO_INIT_QUERIES:
                session.run(query)
        finally:
            session.close()
        return {"status": "ready", "generation": _safe_generation(generation)}

    def apply(self, snapshot: dict[str, Any], generation: str, document_snapshots: list[dict[str, Any]] | None = None, *, _repair=False) -> dict[str, Any]:
        if snapshot.get("kind") == "investigation":
            graph = build_investigation_graph(snapshot, document_snapshots or [])
        else:
            graph = build_document_graph(snapshot)
        if not self.url and self._driver is None:
            return {"status": "unconfigured", "nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", [])), "limitations": graph.get("limitations", [])}
        generation = _safe_generation(generation)
        domain_id = self._scope(snapshot)
        node_keys = {str(node["id"]): f"{generation}:{domain_id}:node:{node['id']}" for node in graph.get("nodes", [])}
        edge_keys = [f"{generation}:{domain_id}:edge:{edge['id']}" for edge in graph.get("edges", [])]
        driver = self._get_driver()
        session = driver.session()

        def write(tx: Any) -> dict[str, Any]:
            query = _NEO_LOCK_QUERY.replace("lock.revision = $revision AND lock.semantic_hash = $semantic_hash","lock.revision = $revision") if _repair else _NEO_LOCK_QUERY
            lock = tx.run(query, domain_id=domain_id, generation=generation, revision=int(snapshot["revision"]), semantic_hash=str(snapshot["semantic_hash"]), eligible=bool(snapshot["eligible"]), operation=str(snapshot["operation"])).single()
            if lock is None:
                current = tx.run("MATCH (lock:RhetoriqProjectionLock {domain_id:$domain_id,generation:$generation}) RETURN lock.revision AS revision,lock.semantic_hash AS semantic_hash", domain_id=domain_id, generation=generation).single()
                if current and self._record_value(current, "revision") == int(snapshot["revision"]):
                    raise ValueError("Neo4j equal-revision hash conflict")
                return {"status": "stale", "nodes": 0, "edges": 0}
            for node in graph.get("nodes", []):
                # Every owned node carries the canonical snapshot hash, even
                # when the pure builder did not need that field on the node.
                node_payload = {**node, "snapshot_hash": str(snapshot["semantic_hash"])}
                tx.run(_NEO_TYPED_NODE_QUERIES.get(str(node.get("kind")),_NEO_NODE_QUERY), projection_key=node_keys[str(node["id"])], properties=self._node_properties(node_payload, domain_id, generation))
            for edge in graph.get("edges", []):
                source_key = node_keys.get(str(edge.get("source")))
                target_key = node_keys.get(str(edge.get("target")))
                if source_key is None or target_key is None:
                    continue
                tx.run(_NEO_EDGE_QUERY, source_key=source_key, target_key=target_key, projection_key=f"{generation}:{domain_id}:edge:{edge['id']}", properties=self._edge_properties(edge, domain_id, generation))
            tx.run(_NEO_REMOVE_QUERY, domain_id=domain_id, generation=generation, edge_keys=edge_keys)
            tx.run(_NEO_REMOVE_NODES_QUERY, domain_id=domain_id, generation=generation, node_keys=list(node_keys.values()))
            return {"status": "applied", "nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", [])), "limitations": graph.get("limitations", [])}

        try:
            return self._run_tx(session, write)
        finally:
            session.close()

    def repair(self, snapshot: dict[str, Any], generation: str, document_snapshots: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Re-upsert owned records at a matching canonical revision.

        Neo4j's lock query allows an equal revision only when its semantic hash
        matches, so this reuses the normal transaction while making the audit
        intent explicit to callers.
        """

        result = self.apply(snapshot, generation, document_snapshots,_repair=True)
        result["repair"] = True
        return result

    def remove_unexpected(self, domain_id: str, generation: str, kind: str = "document", *, expected_ids: list[str] | None = None) -> dict[str, Any]:
        """Bounded operator repair for Neo4j owned nodes and relationships."""

        if kind not in {"document", "investigation"}:
            return {"status": "capability_error", "reason": "unsupported_kind"}
        if not self.url and self._driver is None:
            return {"status": "unconfigured", "removed": 0}
        scope = f"investigation:{domain_id}" if kind == "investigation" else str(domain_id)
        generation = _safe_generation(generation)
        expected = [str(item) for item in expected_ids][:1000] if expected_ids is not None else []
        if domain_id in expected:
            return {"status":"blocked","removed":0}
        query = """
        MATCH (node:RhetoriqGraphNode)
        WHERE node.domain_id=$domain_id AND node.generation=$generation AND node.owned=true
        DETACH DELETE node
        """
        driver = self._get_driver()
        session = driver.session()
        try:
            result = session.run(query, domain_id=scope, generation=generation, kind=kind, expected_ids=expected, has_expected=expected_ids is not None)
            summary = getattr(result, "consume", lambda: None)()
            session.run("MATCH (lock:RhetoriqProjectionLock {domain_id:$domain_id,generation:$generation}) DELETE lock",domain_id=scope,generation=generation).consume()
            counters = getattr(summary, "counters", None)
            removed = getattr(counters, "nodes_deleted", 0) if counters is not None else 0
            return {"status": "removed", "removed": removed}
        finally:
            session.close()

    def validate_snapshot(self, snapshot: dict[str, Any], generation: str, document_snapshots: list[dict[str, Any]] | None = None) -> list[str]:
        """Compare owned node/edge payloads with a canonical graph snapshot."""

        if not self.url and self._driver is None:
            return ["unconfigured:neo4j"]
        if snapshot.get("kind") == "investigation":
            expected = build_investigation_graph(snapshot, document_snapshots or [])
            workspace = snapshot.get("data", {}).get("workspace", {})
            investigation_id = str(workspace.get("investigation_id") or snapshot.get("domain_id")) if isinstance(workspace, dict) else str(snapshot.get("domain_id"))
            actual = self.read_graph(investigation_id, [str(item.get("id")) for item in expected.get("nodes", []) if item.get("kind") == "document"], generation, domain_id=f"investigation:{snapshot.get('domain_id')}")
        else:
            expected = build_document_graph(snapshot)
            document_ids = [str(item.get("id")) for item in expected.get("nodes", []) if item.get("kind") == "document"]
            actual = self.read_graph(str(snapshot.get("domain_id")), document_ids, generation, domain_id=str(snapshot.get("domain_id")))
        findings: list[str] = []
        expected_nodes = {str(item.get("id")): item for item in expected.get("nodes", [])}
        actual_nodes = {str(item.get("id")): item for item in actual.get("nodes", [])}
        for node_id in sorted(set(expected_nodes) - set(actual_nodes)):
            findings.append(f"missing_node:{node_id}")
        for node_id in sorted(set(actual_nodes) - set(expected_nodes)):
            findings.append(f"unexpected_node:{node_id}")
        for node_id in sorted(set(expected_nodes) & set(actual_nodes)):
            expected_node = {**expected_nodes[node_id], "snapshot_hash": str(snapshot.get("semantic_hash"))}
            actual_node = dict(actual_nodes[node_id])
            actual_node.pop("snapshot_hash", None)
            expected_node.pop("snapshot_hash", None)
            if canonical_json(actual_node) != canonical_json(expected_node):
                findings.append(f"mismatch_node:{node_id}")
        def edge_key(edge: dict[str, Any]) -> str:
            return str(edge.get("id"))
        expected_edges = {edge_key(item): item for item in expected.get("edges", [])}
        actual_edges = {edge_key(item): item for item in actual.get("edges", [])}
        for edge_id in sorted(set(expected_edges) - set(actual_edges)):
            findings.append(f"missing_edge:{edge_id}")
        for edge_id in sorted(set(actual_edges) - set(expected_edges)):
            findings.append(f"unexpected_edge:{edge_id}")
        for edge_id in sorted(set(expected_edges) & set(actual_edges)):
            if canonical_json(actual_edges[edge_id]) != canonical_json(expected_edges[edge_id]):
                findings.append(f"mismatch_edge:{edge_id}")
        return sorted(set(findings))

    @staticmethod
    def _decode_node(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            props = value.get("properties", value)
        else:
            props = getattr(value, "_properties", None) or getattr(value, "properties", None)
        if not isinstance(props, dict):
            return None
        payload = props.get("payload_json")
        if isinstance(payload, str):
            try:
                decoded = json.loads(payload)
                if isinstance(decoded, dict):
                    return decoded
            except json.JSONDecodeError:
                pass
        return dict(props)

    @classmethod
    def _decode_edge(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict) and isinstance(value.get("edge"), dict):
            edge = value["edge"]
        else:
            edge = value
        if isinstance(edge, dict):
            props = edge.get("properties", edge)
        else:
            props = getattr(edge, "_properties", None) or getattr(edge, "properties", None)
        if not isinstance(props, dict):
            return None
        payload = props.get("payload_json")
        if isinstance(payload, str):
            try:
                decoded = json.loads(payload)
                if isinstance(decoded, dict):
                    return decoded
            except json.JSONDecodeError:
                pass
        return dict(props)

    def read_graph(self, investigation_id: str, document_ids: list[str], generation: str = "live", *, domain_id: str | None = None) -> dict[str, Any]:
        if not self.url and self._driver is None:
            return {"nodes": [], "edges": [], "limitations": ["Neo4j target is not configured."]}
        driver = self._get_driver()
        session = driver.session()
        try:
            scope = domain_id or f"investigation:{investigation_id}"
            result = session.run(_NEO_READ_QUERY, investigation_id=investigation_id, document_ids=[str(item) for item in document_ids], domain_id=scope, include_all_scope=True, generation=_safe_generation(generation))
            record = result.single()
            if record is None:
                return {"nodes": [], "edges": [], "limitations": []}
            raw_nodes = self._record_value(record, "nodes", []) or []
            raw_edges = self._record_value(record, "edges", []) or []
            nodes = [decoded for item in raw_nodes if (decoded := self._decode_node(item)) is not None]
            edges = [decoded for item in raw_edges if (decoded := self._decode_edge(item)) is not None and decoded.get("source") and decoded.get("target")]
            lock = session.run("MATCH (lock:RhetoriqProjectionLock {domain_id:$domain_id,generation:$generation}) RETURN lock.revision AS revision,lock.semantic_hash AS semantic_hash",domain_id=scope,generation=_safe_generation(generation)).single()
            return {"nodes": sorted(nodes, key=lambda item: str(item.get("id", ""))), "edges": sorted(edges, key=lambda item: str(item.get("id", ""))), "limitations": [],
                    "revision":self._record_value(lock,"revision") if lock else None,"semantic_hash":self._record_value(lock,"semantic_hash") if lock else None}
        finally:
            session.close()

    def related_document_ids(self, document_ids: list[str], generation: str = "live", limit: int = 20) -> list[str]:
        """Bounded context expansion over already projected document pairs."""
        if not document_ids:
            return []
        query = """
        MATCH (seed:RhetoriqGraphNode)-[edge:RhetoriqProjected]-(neighbor:RhetoriqGraphNode)
        WHERE seed.generation=$generation AND neighbor.generation=$generation
          AND seed.kind='document' AND neighbor.kind='document'
          AND seed.document_id IN $document_ids AND NOT neighbor.document_id IN $document_ids
          AND edge.relationship IN $relationships
        RETURN DISTINCT neighbor.document_id AS document_id ORDER BY document_id LIMIT $limit
        """
        session = self._get_driver().session()
        try:
            records = session.run(query,generation=_safe_generation(generation),document_ids=document_ids[:50],
                relationships=["references", "exact_duplicate_of", "phrase_reuse", "mutation", "amplifies", "temporal_adjacency", "entity_overlap"],limit=max(1,min(int(limit),20)))
            return [str(self._record_value(record,"document_id")) for record in records if self._record_value(record,"document_id")]
        finally:
            session.close()

    def inventory(self, generation: str) -> dict[str, dict[str, Any]]:
        if not self.url and self._driver is None:
            return {}
        driver = self._get_driver()
        session = driver.session()
        try:
            result = session.run(_NEO_INVENTORY_QUERY, generation=_safe_generation(generation))
            output: dict[str, dict[str, Any]] = {}
            for record in result:
                domain = self._record_value(record, "domain_id")
                if not domain:
                    continue
                output[str(domain)] = {"revision": self._record_value(record, "revision"), "semantic_hash": self._record_value(record, "semantic_hash"), "eligible": self._record_value(record, "eligible")}
            return output
        finally:
            session.close()

    def health(self) -> dict[str, Any]:
        if not self.url and self._driver is None:
            return {"status": "unconfigured", "target": "neo4j"}
        try:
            driver = self._get_driver()
            verifier = getattr(driver, "verify_connectivity", None)
            if verifier:
                verifier()
            return {"status": "healthy", "target": "neo4j"}
        except Exception as exc:
            return {"status": "unhealthy", "target": "neo4j", "error": str(exc)}

    def close(self) -> None:
        if self._owned_driver and self._driver is not None:
            self._driver.close()
            self._driver = None
