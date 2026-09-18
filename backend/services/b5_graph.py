"""Deterministic B5 graph projections.

The graph contract deliberately uses plain dictionaries.  Snapshots are the
boundary between the event/persistence layers and this module, so this code
does not mutate persisted Pydantic models or depend on a database client.
"""

from __future__ import annotations

from datetime import datetime
from collections import deque
import re
from typing import Any, Iterable
from urllib.parse import urlparse

from flink.contracts import canonical_json, semantic_hash, stable_id, utc
from models.document import Document
from models.events import EnrichmentReceipt
from services.mutation_detection import MutationDetector
from services.b5_versions import (GRAPH_METHOD_VERSION, MUTATION_METHOD_VERSION,
                                 AMPLIFICATION_METHOD_VERSION, projection_methods)


MAX_PAIRWISE_DOCUMENTS = 200
MAX_PATH_DEPTH = 6


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"expected a mapping or Pydantic model, got {type(value)!r}")


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    supplied = snapshot.get("semantic_hash")
    if supplied:
        return str(supplied)
    return semantic_hash(snapshot)


def _snapshot_data(snapshot: dict[str, Any]) -> dict[str, Any]:
    data = snapshot.get("data")
    if not isinstance(data, dict):
        raise ValueError("graph snapshot data must be an object")
    if data.get("projection_methods", projection_methods()) != projection_methods():
        raise ValueError("Recorded projection-method versions are unsupported by this worker")
    return data


def _validate_snapshot(snapshot: dict[str, Any], kind: str | None = None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise TypeError("graph snapshot must be a dictionary")
    required = {"snapshot_id", "kind", "domain_id", "revision", "semantic_hash", "operation", "eligible", "data"}
    missing = sorted(required - set(snapshot))
    if missing:
        raise ValueError(f"graph snapshot is missing fields: {', '.join(missing)}")
    if kind is not None and snapshot["kind"] != kind:
        raise ValueError(f"expected {kind} snapshot, got {snapshot['kind']!r}")
    if snapshot["kind"] not in {"document", "investigation"}:
        raise ValueError("graph snapshot kind must be document or investigation")
    if snapshot["operation"] not in {"upsert", "withdraw", "restore"}:
        raise ValueError("graph snapshot operation is invalid")
    if not isinstance(snapshot["revision"], int) or snapshot["revision"] < 0:
        raise ValueError("graph snapshot revision must be a non-negative integer")
    _snapshot_data(snapshot)
    return snapshot


def _label(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _source_id(document: dict[str, Any]) -> str:
    source = document.get("source_id")
    if not source:
        source = "domain:" + (urlparse(str(document.get("url") or "")).hostname or "unknown").lower()
    return f"source:{source}"


def _entity_id(entity: str) -> str:
    return f"entity:{_norm(entity)}"


def _external_id(target_url: str) -> str:
    return stable_id("external", _norm(target_url), length=20)


def _edge_id(source: str, target: str, relationship: str, *parts: Any) -> str:
    return stable_id("edge", source, target, relationship, *parts, length=24)


def _edge(
    source: str,
    target: str,
    relationship: str,
    *,
    evidence_class: str,
    method: str,
    snapshot_hash_value: str,
    evidence: dict[str, Any] | None = None,
    limitations: Iterable[str] = (),
    document_id: str | None = None,
    investigation_id: str | None = None,
    confidence: float | None = None,
    edge_id: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": edge_id or _edge_id(source, target, relationship, evidence or {}),
        "source": source,
        "target": target,
        "relationship": relationship,
        "evidence_class": evidence_class,
        "inferred": evidence_class == "inferred",
        "provenance": evidence_class,
        "method": method,
        "method_version": GRAPH_METHOD_VERSION,
        "evidence": evidence or {},
        "limitations": sorted({str(item) for item in limitations if item}),
        "snapshot_hash": snapshot_hash_value,
    }
    if document_id is not None:
        result["document_id"] = document_id
    if investigation_id is not None:
        result["investigation_id"] = investigation_id
    if confidence is not None:
        result["confidence"] = max(0.0, min(1.0, float(confidence)))
    return result


def _node(node_id: str, kind: str, label: str, **properties: Any) -> dict[str, Any]:
    result = {"id": node_id, "kind": kind, "label": label}
    result.update({key: value for key, value in properties.items() if value is not None})
    return result


def _timestamp_quality(document: dict[str, Any]) -> str:
    if (document.get("metadata") or {}).get("event_time_quality") == "collected_at_fallback":
        return "collected_at_fallback"
    if document.get("published_at"):
        return "published"
    if document.get("collected_at"):
        return "collected_only"
    return "missing"


def _valid_offset(text: str, start: Any, end: Any) -> bool:
    return (
        isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start <= end <= len(text)
        and text[start:end] != ""
    )


def _offset_evidence(text: str, item: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else item
    start, end = evidence.get("start"), evidence.get("end")
    if start is None and end is None:
        value = str(item.get("phrase") or item.get("value") or "")
        match = re.search(re.escape(value),text,re.IGNORECASE) if value else None
        if match:
            return {"surface_form":text[match.start():match.end()],"start":match.start(),"end":match.end()},[]
        return None,["Mention has no validated literal evidence in the normalized text."]
    if not _valid_offset(text, start, end):
        return None, ["B4 evidence offsets are outside the normalized document text."]
    surface_form = evidence.get("surface_form")
    if surface_form is not None and text[start:end] != str(surface_form):
        return None, ["B4 evidence span does not match the normalized document text."]
    return {"surface_form": evidence.get("surface_form") or text[start:end], "start": start, "end": end}, []


def _references(document: dict[str, Any]) -> list[dict[str, Any]]:
    raw = document.get("references")
    if not raw and isinstance(document.get("metadata"), dict):
        raw = document["metadata"].get("references")
    return [item for item in (raw or []) if isinstance(item, dict)]


def _reference_is_valid(item: dict[str, Any], text: str) -> tuple[bool, list[str]]:
    limitations: list[str] = []
    target = item.get("target_url")
    if not isinstance(target, str) or not target.strip():
        return False, ["Source link has no target_url."]
    if item.get("reference_kind") not in {"hyperlink", "structured"}:
        return False, ["Source link reference_kind is outside the B4 contract."]
    start, end = item.get("start"), item.get("end")
    if start is not None or end is not None:
        if not _valid_offset(text, start, end):
            return False, ["B4 source-link offsets are outside the normalized document text."]
        anchor_text = item.get("anchor_text")
        if anchor_text is not None and text[start:end] != str(anchor_text):
            return False, ["B4 source-link span does not match the normalized document text."]
    return True, limitations


def _document_payload(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    data = _snapshot_data(snapshot)
    raw_document = data.get("document")
    document = _as_dict(raw_document)
    # Validation catches malformed persisted payloads while preserving extra
    # B4 fields (such as references) from the original JSON mapping.
    Document.model_validate(document)
    enrichment_raw = data.get("enrichment")
    enrichment = _as_dict(enrichment_raw) if enrichment_raw is not None else None
    if enrichment is not None:
        EnrichmentReceipt.model_validate(enrichment)
    processing = data.get("processing") if isinstance(data.get("processing"), dict) else {}
    return document, enrichment, processing


def build_document_graph(snapshot: dict[str, Any], known_documents: dict[str, dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Build the observed graph for one normalized document snapshot.

    A withdrawn snapshot remains addressable through its document node but does
    not re-publish any of its relationships.  This lets targets retain a
    tombstone while removing searchable/graph-visible evidence safely.
    """

    snapshot = _validate_snapshot(snapshot, "document")
    document, enrichment, processing = _document_payload(snapshot)
    document_id = str(document["id"])
    snapshot_hash_value = _snapshot_hash(snapshot)
    withdrawn = bool(data_withdrawn := _snapshot_data(snapshot).get("withdrawn")) or snapshot["operation"] == "withdraw" or not snapshot["eligible"]

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    graph_limitations = list((document.get("metadata") or {}).get("reference_limitations") or [])
    node_ids: set[str] = set()

    def add_node(value: dict[str, Any]) -> None:
        if value["id"] not in node_ids:
            node_ids.add(value["id"])
            nodes.append(value)

    def add_edge(value: dict[str, Any]) -> None:
        if value["id"] not in {item["id"] for item in edges}:
            edges.append(value)

    published = document.get("published_at")
    add_node(
        _node(
            document_id,
            "document",
            _label(document.get("title"), document_id),
            document_id=document_id,
            source_name=document.get("source_name"),
            source_type=document.get("source_type"),
            url=document.get("url"),
            published_at=published,
            timestamp_quality=_timestamp_quality(document),
            withdrawn=withdrawn,
            eligible=bool(snapshot["eligible"]),
            revision=snapshot["revision"],
            semantic_hash=snapshot_hash_value,
            source_kind=_snapshot_data(snapshot).get("source_kind"),
        )
    )
    if withdrawn:
        return {"nodes": nodes, "edges": edges}

    source_id = _source_id(document)
    add_node(_node(source_id, "source", _label(document.get("source_name"), source_id), source_id=document.get("source_id"), url=document.get("url")))
    add_edge(_edge(source_id, document_id, "published", evidence_class="observed", method="document.source", snapshot_hash_value=snapshot_hash_value, document_id=document_id, evidence={"source_name": document.get("source_name"), "source_type": document.get("source_type")}))

    duplicate_id = document.get("duplicate_of_doc_id") or processing.get("duplicate_of_document_id")
    if duplicate_id:
        duplicate_id = str(duplicate_id)
        add_node(_node(duplicate_id, "document", f"Document {duplicate_id}", document_id=duplicate_id, placeholder=True))
        add_edge(_edge(document_id, duplicate_id, "exact_duplicate_of", evidence_class="observed", method="b4.duplicate_detection", snapshot_hash_value=snapshot_hash_value, document_id=document_id, evidence={"duplicate_of_document_id": duplicate_id}))

    text = str(document.get("text") or "")
    phrases: list[dict[str, Any]] = [{"phrase": phrase} for phrase in document.get("phrases") or [] if str(phrase).strip()]
    if enrichment:
        phrases.extend(
            {"phrase": item.get("phrase"), "evidence": item.get("evidence")}
            for item in enrichment.get("canonical_phrases") or []
            if isinstance(item, dict) and str(item.get("phrase") or "").strip()
        )
    seen_phrases: set[str] = set()
    for item in phrases:
        phrase = _norm(item.get("phrase"))
        if not phrase or (phrase in seen_phrases and not item.get("evidence")):
            continue
        seen_phrases.add(phrase)
        phrase_id = stable_id("phrase", phrase, length=16)
        evidence, limitations = _offset_evidence(text, item)
        if evidence is None:
            graph_limitations.extend(limitations)
            continue
        add_node(_node(phrase_id, "phrase", phrase, phrase=phrase))
        add_edge(_edge(document_id, phrase_id, "mentions_phrase", evidence_class="observed", method="document.phrases" if not item.get("evidence") else "b4.enrichment", snapshot_hash_value=snapshot_hash_value, document_id=document_id, evidence={**evidence,"phrase":phrase}, limitations=limitations))

    entities: list[dict[str, Any]] = [{"value": entity} for entity in document.get("entities") or [] if str(entity).strip()]
    if enrichment:
        entities.extend(
            {"value": item.get("value"), "evidence": item.get("evidence")}
            for item in enrichment.get("entities") or []
            if isinstance(item, dict) and str(item.get("value") or "").strip()
        )
    seen_entities: set[str] = set()
    for item in entities:
        entity = _norm(item.get("value"))
        if not entity or (entity in seen_entities and not item.get("evidence")):
            continue
        seen_entities.add(entity)
        entity_id = _entity_id(entity)
        evidence, limitations = _offset_evidence(text, item)
        if evidence is None:
            graph_limitations.extend(limitations)
            continue
        add_node(_node(entity_id, "entity", entity, entity=entity))
        add_edge(_edge(document_id, entity_id, "mentions_entity", evidence_class="observed", method="document.entities" if not item.get("evidence") else "b4.enrichment", snapshot_hash_value=snapshot_hash_value, document_id=document_id, evidence={**evidence,"entity":entity}, limitations=limitations))

    if enrichment:
        artifact_id = str(enrichment.get("artifact_id") or stable_id("enrichment", document_id, enrichment, length=16))
        enrichment_id = f"enrichment:{document_id}:{artifact_id}"
        add_node(_node(enrichment_id, "enrichment", artifact_id, document_id=document_id, provider=enrichment.get("provider"), model=enrichment.get("model"), output_hash=enrichment.get("output_hash")))
        add_edge(_edge(document_id, enrichment_id, "enriched_by", evidence_class="observed", method="b4.enrichment", snapshot_hash_value=snapshot_hash_value, document_id=document_id, evidence={"artifact_id": artifact_id, "output_hash": enrichment.get("output_hash")}))

    for reference in _references(document):
        valid, limitations = _reference_is_valid(reference, text)
        if not valid:
            graph_limitations.extend(limitations)
            continue
        target = str(reference["target_url"]).strip()
        acquired = (known_documents or {}).get(target)
        target_id = str(acquired["id"]) if acquired else _external_id(target)
        if acquired:
            add_node(_node(target_id,"document",str(acquired.get("title") or target),document_id=target_id,url=target,placeholder=True))
        else:
            add_node(_node(target_id, "reference_target", target, url=target, unresolved=True))
            limitations.append("Reference target was not resolved to an acquired canonical document in this snapshot scope.")
        evidence = {key: reference[key] for key in ("target_url", "anchor_text", "context", "start", "end", "extraction_version", "reference_kind") if key in reference}
        add_edge(_edge(document_id, target_id, "references", evidence_class="observed", method="b4.source_link", snapshot_hash_value=snapshot_hash_value, document_id=document_id, evidence=evidence, limitations=limitations))

    return {"nodes": nodes, "edges": edges,"limitations":sorted(set(graph_limitations))}


def _workspace_value(workspace: dict[str, Any], *path: str) -> Any:
    current: Any = workspace
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _document_snapshots_for_investigation(snapshot: dict[str, Any], document_snapshots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    data = _snapshot_data(snapshot)
    requested = {str(item) for item in data.get("document_snapshot_ids") or []}
    selected = [item for item in document_snapshots if isinstance(item, dict) and item.get("kind") == "document" and (not requested or str(item.get("snapshot_id")) in requested)]
    by_document: dict[str, dict[str, Any]] = {}
    limitations: list[str] = []
    for candidate in selected:
        try:
            document, _, _ = _document_payload(candidate)
        except (TypeError, ValueError) as exc:
            limitations.append(f"Skipped malformed document snapshot: {exc}")
            continue
        document_id = str(document["id"])
        prior = by_document.get(document_id)
        if prior is None or int(candidate.get("revision", -1)) > int(prior.get("revision", -1)) or (candidate.get("revision") == prior.get("revision") and str(candidate.get("semantic_hash")) > str(prior.get("semantic_hash"))):
            by_document[document_id] = candidate
    selected = sorted(by_document.values(), key=lambda item: (str(_snapshot_data(item).get("document", {}).get("id", "")), int(item.get("revision", 0)), str(item.get("snapshot_id", ""))))
    return selected, limitations


def _workspace_documents(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    docs = workspace.get("retrieved_documents")
    return [item for item in (docs or []) if isinstance(item, dict)]


def _workspace_investigation_id(snapshot: dict[str, Any], workspace: dict[str, Any]) -> str:
    return str(workspace.get("investigation_id") or snapshot.get("domain_id"))


def _add_investigation_evidence(
    edges: list[dict[str, Any]],
    *,
    claim_id: str,
    document_id: str,
    relationship: str,
    evidence_class: str,
    method: str,
    evidence: dict[str, Any],
    snapshot_hash_value: str,
    investigation_id: str,
    limitations: Iterable[str] = (),
    confidence: float | None = None,
) -> None:
    edges.append(_edge(claim_id, document_id, relationship, evidence_class=evidence_class, method=method, snapshot_hash_value=snapshot_hash_value, evidence=evidence, limitations=limitations, document_id=document_id, investigation_id=investigation_id, confidence=confidence))


def _claim_records(workspace: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    records: list[tuple[dict[str, Any], str]] = []
    report = workspace.get("report") if isinstance(workspace.get("report"), dict) else {}
    for claim in report.get("key_claims") or []:
        if isinstance(claim, dict):
            records.append((claim, "report"))
    analyst = workspace.get("analyst") if isinstance(workspace.get("analyst"), dict) else {}
    for claim in analyst.get("candidate_claims") or []:
        if isinstance(claim, dict) and not any(str(item.get("claim_id") or item.get("id")) == str(claim.get("claim_id") or claim.get("id")) for item, _ in records):
            records.append((claim, "analyst"))
    ledger = workspace.get("claim_ledger") if isinstance(workspace.get("claim_ledger"), dict) else {}
    for claim in ledger.get("entries") or []:
        if isinstance(claim, dict) and not any(str(item.get("claim_id") or item.get("id")) == str(claim.get("claim_id") or claim.get("id")) for item, _ in records):
            records.append((claim, "ledger"))
    return records


def _validate_claim_span(document: dict[str, Any], evidence: dict[str, Any]) -> tuple[bool, list[str]]:
    start, end = evidence.get("span_start"), evidence.get("span_end")
    if start is None and end is None:
        return True, []
    if not _valid_offset(str(document.get("text") or ""), start, end):
        return False, ["A3 evidence span offsets are outside the normalized document text."]
    return True, []


def build_investigation_graph(snapshot: dict[str, Any], document_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an investigation graph from a persisted workspace and documents."""

    snapshot = _validate_snapshot(snapshot, "investigation")
    data = _snapshot_data(snapshot)
    workspace = data.get("workspace")
    if not isinstance(workspace, dict):
        raise ValueError("investigation snapshot data.workspace must be an object")
    investigation_id = _workspace_investigation_id(snapshot, workspace)
    snapshot_hash_value = _snapshot_hash(snapshot)
    graph_nodes: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    edge_ids: set[str] = set()
    limitations: list[str] = []

    def add_node(value: dict[str, Any]) -> None:
        if value["id"] not in node_ids:
            node_ids.add(value["id"])
            graph_nodes.append(value)
        else:
            for index, current in enumerate(graph_nodes):
                if current.get("id") == value.get("id") and current.get("placeholder") and not value.get("placeholder"):
                    graph_nodes[index] = value
                    break

    def add_edge(value: dict[str, Any]) -> None:
        if value["id"] not in edge_ids:
            edge_ids.add(value["id"])
            graph_edges.append(value)

    investigation_node_id = f"investigation:{investigation_id}"
    add_node(_node(investigation_node_id, "investigation", _label(workspace.get("query_text"), investigation_id), investigation_id=investigation_id, run_id=data.get("run_id"), terminal_decision=data.get("terminal_decision"), revision=snapshot["revision"], semantic_hash=snapshot_hash_value))
    run_node_id = f"run:{investigation_id}:{data.get('run_id') or 'historical'}"
    add_node(_node(run_node_id,"research_run",str(data.get("run_id") or "Historical run"),investigation_id=investigation_id,terminal_decision=data.get("terminal_decision")))
    add_edge(_edge(investigation_node_id,run_node_id,"has_run",evidence_class="observed",method="research.terminal_snapshot",snapshot_hash_value=snapshot_hash_value,investigation_id=investigation_id,evidence={"terminal_decision":data.get("terminal_decision"),"publication_gate":data.get("terminal_decision")}))

    selected_snapshots, selection_limitations = _document_snapshots_for_investigation(snapshot, document_snapshots or [])
    known_documents = {str(item["data"]["document"].get("url")):item["data"]["document"] for item in selected_snapshots if item["eligible"] and not item["data"].get("withdrawn")}
    limitations.extend(selection_limitations)
    if len(selected_snapshots) > MAX_PAIRWISE_DOCUMENTS:
        limitations.append(f"Pairwise mutation and amplification analysis capped at {MAX_PAIRWISE_DOCUMENTS} documents.")
    graphs_by_doc: dict[str, dict[str, Any]] = {}
    docs: list[Document] = []
    docs_by_id: dict[str, dict[str, Any]] = {}
    for doc_snapshot in selected_snapshots:
        document, _, _ = _document_payload(doc_snapshot)
        document_id = str(document["id"])
        docs_by_id[document_id] = document
        try:
            docs.append(Document.model_validate(document))
        except Exception:
            continue
        child = build_document_graph(doc_snapshot,known_documents)
        limitations.extend(child.get("limitations",[]))
        graphs_by_doc[document_id] = child
        for node in child["nodes"]:
            if node["id"] in node_ids and node.get("kind")=="document" and not node.get("placeholder"):
                graph_nodes[:] = [node if prior["id"]==node["id"] else prior for prior in graph_nodes]
            add_node(node)
        for edge in child["edges"]:
            add_edge(edge)
        add_edge(_edge(investigation_node_id, document_id, "includes_document", evidence_class="contextual", method="investigation.document_snapshot", snapshot_hash_value=snapshot_hash_value, investigation_id=investigation_id, document_id=document_id, evidence={"snapshot_id": doc_snapshot.get("snapshot_id"), "revision": doc_snapshot.get("revision")}))

    # Preserve document references from workspace artifacts even when the
    # corresponding document snapshot was not hydrated by the caller.
    for document in _workspace_documents(workspace):
        document_id = str(document.get("id") or "")
        if not document_id or document_id in docs_by_id:
            continue
        add_node(_node(document_id, "document", f"Missing document {document_id}", document_id=document_id, placeholder=True, missing=True))
        limitations.append(f"Document {document_id} is referenced by the workspace but its document snapshot was not supplied.")

    def claim_node(claim: dict[str, Any]) -> str | None:
        raw_id = claim.get("claim_id") or claim.get("id")
        if not raw_id:
            return None
        claim_id = f"claim:{investigation_id}:{raw_id}"
        add_node(_node(claim_id, "claim", _label(claim.get("claim_text"), str(raw_id)), claim_id=str(raw_id), investigation_id=investigation_id, claim_type=claim.get("claim_type"), confidence=claim.get("confidence_score")))
        add_edge(_edge(investigation_node_id, claim_id, "contains_claim", evidence_class="observed", method="workspace.claim", snapshot_hash_value=snapshot_hash_value, investigation_id=investigation_id, evidence={"claim_id": str(raw_id)}))
        return claim_id

    for claim, record_kind in _claim_records(workspace):
        claim_id = claim_node(claim)
        if claim_id is None:
            limitations.append("Ignored a workspace claim without an id.")
            continue
        claim_type = str(claim.get("claim_type") or "")
        claim_class = "inferred" if claim_type in {"inference", "recommendation"} else "observed"
        for document_id in [*(claim.get("supporting_document_ids") or []), *(claim.get("counter_document_ids") or [])]:
            document_id = str(document_id)
            if document_id not in node_ids:
                add_node(_node(document_id, "document", f"Missing document {document_id}", document_id=document_id, placeholder=True, missing=True))
                limitations.append(f"Claim {claim.get('claim_id') or claim.get('id')} references missing document {document_id}.")
            _add_investigation_evidence(graph_edges, claim_id=claim_id, document_id=document_id, relationship="supported_by" if document_id in [str(item) for item in claim.get("supporting_document_ids") or []] else "countered_by", evidence_class=claim_class, method=f"workspace.{record_kind}", evidence={"claim_type": claim_type}, snapshot_hash_value=snapshot_hash_value, investigation_id=investigation_id, confidence=claim.get("confidence_score"))

        report_citations = [*(claim.get("citations") or []), *(claim.get("counter_citations") or [])]
        for citation in report_citations:
            if not isinstance(citation, dict) or not citation.get("document_id"):
                continue
            document_id = str(citation["document_id"])
            if document_id not in node_ids:
                add_node(_node(document_id, "document", f"Missing document {document_id}", document_id=document_id, placeholder=True, missing=True))
                limitations.append(f"Claim {claim.get('claim_id') or claim.get('id')} citation document {document_id} was not hydrated.")
            _add_investigation_evidence(graph_edges, claim_id=claim_id, document_id=document_id, relationship="cites", evidence_class="observed", method="a3.report_citation", evidence={key: citation[key] for key in citation if key in {"document_id", "source_name", "title", "url", "relevance_note"}}, snapshot_hash_value=snapshot_hash_value, investigation_id=investigation_id)

    verification = workspace.get("claim_verification") if isinstance(workspace.get("claim_verification"), dict) else {}
    for record in verification.get("records") or []:
        if not isinstance(record, dict):
            continue
        claim_id = claim_node({"claim_id": record.get("claim_id"), "claim_text": record.get("claim_text"), "claim_type": "verified"})
        if claim_id is None:
            continue
        for side, key, relationship in (("support", "supporting_evidence", "verified_by"), ("counter", "contradicting_evidence", "contradicted_by")):
            for evidence in record.get(key) or []:
                if not isinstance(evidence, dict) or not evidence.get("document_id"):
                    continue
                document_id = str(evidence["document_id"])
                document = docs_by_id.get(document_id)
                valid, span_limitations = _validate_claim_span(document or {}, evidence)
                if not valid:
                    limitations.extend(span_limitations)
                    continue
                if document_id not in node_ids:
                    add_node(_node(document_id, "document", f"Missing document {document_id}", document_id=document_id, placeholder=True, missing=True))
                verdict = evidence.get("nli_verdict")
                evidence_class = "inferred" if verdict in {"entails", "contradicts"} else "observed"
                evidence_node_id = stable_id("evidence", investigation_id, record.get("claim_id"), document_id, side, evidence.get("span_start"), evidence.get("span_end"), length=20)
                add_node(_node(evidence_node_id, "evidence", str(evidence.get("evidence_span") or "A3 evidence"), investigation_id=investigation_id, claim_id=str(record.get("claim_id")), document_id=document_id, evidence_side=side, span_start=evidence.get("span_start"), span_end=evidence.get("span_end"), nli_verdict=verdict, snapshot_hash=snapshot_hash_value))
                add_edge(_edge(claim_id, evidence_node_id, "has_evidence", evidence_class=evidence_class, method="a3.claim_evidence_verifier", snapshot_hash_value=snapshot_hash_value, evidence={"claim_id": record.get("claim_id"), "document_id": document_id, "evidence_side": side}, investigation_id=investigation_id, document_id=document_id, confidence=evidence.get("confidence_score")))
                add_edge(_edge(evidence_node_id, document_id, "evidence_from", evidence_class="observed", method="a3.claim_evidence_verifier", snapshot_hash_value=snapshot_hash_value, evidence={"span_start": evidence.get("span_start"), "span_end": evidence.get("span_end"), "nli_verdict": verdict}, investigation_id=investigation_id, document_id=document_id, confidence=evidence.get("confidence_score")))
                _add_investigation_evidence(graph_edges, claim_id=claim_id, document_id=document_id, relationship=relationship, evidence_class=evidence_class, method="a3.claim_evidence_verifier", evidence={key: evidence[key] for key in evidence if key not in {"source_intelligence"}}, snapshot_hash_value=snapshot_hash_value, investigation_id=investigation_id, limitations=span_limitations, confidence=evidence.get("confidence_score"))

    receipts = workspace.get("receipts") if isinstance(workspace.get("receipts"), dict) else {}
    for receipt_group, relationship in (("claim_receipts", "receipted_by"), ("counter_claim_receipts", "counter_receipted_by")):
        for receipt in receipts.get(receipt_group) or []:
            if not isinstance(receipt, dict) or not receipt.get("claim_id") or not receipt.get("document_id"):
                continue
            claim_id = claim_node({"claim_id": receipt.get("claim_id"), "claim_text": receipt.get("claim_text"), "claim_type": "receipt"})
            document_id = str(receipt["document_id"])
            if claim_id is None:
                continue
            if document_id not in node_ids:
                add_node(_node(document_id, "document", f"Missing document {document_id}", document_id=document_id, placeholder=True, missing=True))
            receipt_text = str(receipt.get("evidence_span") or "")
            receipt_limitations = []
            if receipt_text and document_id in docs_by_id and receipt_text.casefold() not in str(docs_by_id[document_id].get("text") or "").casefold():
                receipt_limitations.append("Receipt evidence span was not found verbatim in the normalized document text.")
            receipt_node_id = stable_id("receipt", investigation_id, receipt.get("claim_id"), document_id, receipt_text, length=20)
            add_node(_node(receipt_node_id, "receipt", receipt_text or "Receipt evidence", investigation_id=investigation_id, claim_id=str(receipt.get("claim_id")), document_id=document_id, verification_status=receipt.get("verification_status"), snapshot_hash=snapshot_hash_value))
            add_edge(_edge(claim_id, receipt_node_id, "has_receipt", evidence_class="observed", method="a3.receipts", snapshot_hash_value=snapshot_hash_value, evidence={"claim_id": receipt.get("claim_id"), "document_id": document_id}, investigation_id=investigation_id, document_id=document_id, limitations=receipt_limitations))
            add_edge(_edge(receipt_node_id, document_id, "receipt_from", evidence_class="observed", method="a3.receipts", snapshot_hash_value=snapshot_hash_value, evidence={"evidence_span": receipt_text, "verification_status": receipt.get("verification_status")}, investigation_id=investigation_id, document_id=document_id, limitations=receipt_limitations))
            _add_investigation_evidence(graph_edges, claim_id=claim_id, document_id=document_id, relationship=relationship, evidence_class="observed", method="a3.receipts", evidence={key: receipt[key] for key in receipt if key in {"document_id", "evidence_span", "support_reason", "matched_terms", "verification_status"}}, snapshot_hash_value=snapshot_hash_value, investigation_id=investigation_id, limitations=receipt_limitations, confidence=receipt.get("confidence_score"))

    # Existing deterministic mutation detection is retained as an explicit
    # inference.  It is bounded because this stage can otherwise become
    # quadratic for broad investigations.
    pair_docs = sorted(docs, key=lambda item: (utc(item.published_at) or datetime.max.replace(tzinfo=utc(datetime.now()) .tzinfo), item.id))[:MAX_PAIRWISE_DOCUMENTS]
    try:
        mutations = MutationDetector().detect_mutations(pair_docs)
    except Exception as exc:
        mutations = []
        limitations.append(f"Mutation detection unavailable: {exc}")
    for mutation in mutations:
        source = str(mutation.get("from_doc_id") or mutation.get("doc_a_id") or "")
        target = str(mutation.get("to_doc_id") or mutation.get("doc_b_id") or "")
        if source not in node_ids or target not in node_ids:
            continue
        edge_id = _edge_id(source, target, "mutation", mutation.get("from_phrase"), mutation.get("to_phrase"))
        edge = _edge(source, target, "mutation" if mutation.get("mutation_type") == "mutation" else "phrase_reuse", evidence_class="inferred", method="MutationDetector", snapshot_hash_value=snapshot_hash_value, evidence=dict(mutation), limitations=["Phrase similarity is a hypothesis and does not establish causal copying."], document_id=target, investigation_id=investigation_id, confidence=mutation.get("similarity_score"), edge_id=edge_id)
        edge["method_version"] = MUTATION_METHOD_VERSION
        add_edge(edge)

    timeline = workspace.get("timeline") if isinstance(workspace.get("timeline"), dict) else {}
    pair_doc_ids = {item.id for item in pair_docs}
    timeline_events = [item for item in timeline.get("timeline_events") or [] if isinstance(item, dict) and item.get("document_id") in node_ids and item.get("document_id") in pair_doc_ids]
    timeline_events.sort(key=lambda item: (str(item.get("timestamp") or ""), str(item.get("document_id"))))
    for earlier, later in zip(timeline_events, timeline_events[1:]):
        source, target = str(earlier["document_id"]), str(later["document_id"])
        event_type = str(later.get("event_type") or "")
        if event_type not in {"early_amplification", "broader_pickup"}:
            continue
        evidence = {"from_event": earlier.get("id"), "to_event": later.get("id"), "event_type": event_type, "from_timestamp": earlier.get("timestamp"), "to_timestamp": later.get("timestamp")}
        edge = _edge(source, target, "amplifies", evidence_class="inferred", method="timeline.amplification_hypothesis", snapshot_hash_value=snapshot_hash_value, evidence=evidence, limitations=["Timeline ordering supports an amplification hypothesis; it does not prove exposure or intent."], document_id=target, investigation_id=investigation_id)
        edge["method_version"] = AMPLIFICATION_METHOD_VERSION
        edge["confidence"] = 0.5
        add_edge(edge)

    # Contextual adjacency/overlap never count as propagation or support.
    bounded_docs = sorted(docs,key=lambda doc:doc.id)[:MAX_PAIRWISE_DOCUMENTS]
    dated = sorted([doc for doc in bounded_docs if doc.published_at],key=lambda doc:(str(doc.published_at),doc.id))
    for first,second in zip(dated,dated[1:]):
        add_edge(_edge(first.id,second.id,"temporal_adjacency",evidence_class="contextual",method="b5.temporal_context",snapshot_hash_value=snapshot_hash_value,investigation_id=investigation_id,evidence={"earlier":str(first.published_at),"later":str(second.published_at)},limitations=["Chronology does not establish propagation."]))
    entity_docs: dict[str,list[str]] = {}
    for document in bounded_docs:
        for entity in sorted(set(document.entities))[:20]:
            entity_docs.setdefault(_norm(entity),[]).append(document.id)
    for entity,ids in sorted(entity_docs.items()):
        for first,second in zip(ids,ids[1:]):
            add_edge(_edge(first,second,"entity_overlap",evidence_class="contextual",method="b5.entity_context",snapshot_hash_value=snapshot_hash_value,investigation_id=investigation_id,evidence={"entity":entity},limitations=["Shared entities do not establish agreement or propagation."]))

    # Determinism is useful for replay, bulk writes, and explainable paths.
    graph_nodes.sort(key=lambda item: (item.get("kind", ""), item.get("id", "")))
    graph_edges = list({str(item.get("id")): item for item in graph_edges}.values())
    graph_edges.sort(key=lambda item: item.get("id", ""))
    return {"nodes": graph_nodes, "edges": graph_edges, "limitations": sorted(set(limitations))}


_PATH_RELATIONSHIPS = {"exact_duplicate_of", "references", "mutation", "phrase_reuse", "amplifies"}


def provenance_paths(graph: dict[str, Any], from_document_id: str, to_document_id: str, max_depth: int = 4, include_inferred: bool = False, limit: int = 10, stats: dict[str,Any] | None = None) -> list[dict[str, Any]]:
    """Return bounded deterministic document provenance paths.

    Traversal intentionally ignores source/phrase/entity hubs so a popular
    source or phrase cannot turn the result into an arbitrary graph walk.
    """

    max_depth = max(1, min(int(max_depth), MAX_PATH_DEPTH))
    limit = max(0, min(int(limit), 10))
    if limit == 0:
        return []
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict) and edge.get("relationship") in _PATH_RELATIONSHIPS and edge.get("evidence_class") in ({"observed","inferred"} if include_inferred else {"observed"})]
    adjacency: dict[str, list[dict[str, Any]]] = {}
    document_ids = {str(node.get("id")) for node in graph.get("nodes", []) if isinstance(node, dict) and node.get("kind") == "document"}
    for edge in edges:
        if str(edge.get("source")) not in document_ids or str(edge.get("target")) not in document_ids:
            continue
        adjacency.setdefault(str(edge["source"]), []).append(edge)
    for value in adjacency.values():
        value.sort(key=lambda edge: (str(edge.get("target")), str(edge.get("relationship")), str(edge.get("id"))))
    paths: list[dict[str, Any]] = []
    reverse: dict[str,set[str]] = {}
    for source,neighbors in adjacency.items():
        for edge in neighbors:
            reverse.setdefault(str(edge["target"]),set()).add(source)
    distance = {str(to_document_id):0}
    frontier = {str(to_document_id)}
    for depth in range(1,max_depth+1):
        frontier = {source for target in frontier for source in reverse.get(target,set()) if source not in distance}
        distance.update({source:depth for source in frontier})
    queue = deque([(str(from_document_id), [], {str(from_document_id)})])
    expansions = 0
    truncated = False
    while queue and len(paths) < limit and expansions<10000:
        current, trail, visited = queue.popleft()
        expansions += 1
        if current not in distance or len(trail)+distance[current]>max_depth:
            continue
        if current == str(to_document_id):
            paths.append({"from_document_id": str(from_document_id), "to_document_id": str(to_document_id), "document_ids": [str(from_document_id), *[str(edge["target"]) for edge in trail]], "steps": [{"relationship": edge["relationship"], "source": edge["source"], "target": edge["target"], "evidence_class": edge["evidence_class"], "method": edge["method"], "method_version": edge["method_version"], "evidence": edge.get("evidence", {}), "limitations": edge.get("limitations", [])} for edge in trail], "explanation": " → ".join([str(from_document_id), *[f"{edge['relationship']} → {edge['target']}" for edge in trail]])})
            continue
        if len(trail) >= max_depth:
            continue
        for edge in adjacency.get(current, []):
            target = str(edge["target"])
            if target in visited:
                continue
            if target not in distance or len(trail)+1+distance[target]>max_depth:
                continue
            if len(queue)>=10000:
                truncated = True
                continue
            queue.append((target, [*trail, edge], {*visited, target}))
    if stats is not None:
        stats.update({"truncated":truncated or bool(queue),"expanded":expansions,"expansion_limit":10000})
    return paths
