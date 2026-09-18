"""Durable B5 projection inputs, canonical eligibility and generation control.

Every mutation and outbox enqueue shares a transaction. SQLite is only an
isolated development/test implementation of the same snapshot semantics.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from migrations.runner import MIGRATIONS_DIR, run_migrations
from models.document import Document
from models.events import (CORPUS_PROJECTIONS_TOPIC, INVESTIGATION_PROJECTIONS_TOPIC,
                           CorpusProjectionEvent, InvestigationProjectionEvent, ProjectionPayload)
from services.database import connect, ensure_parent_dir, is_postgres_database
from services.event_store import EventStore
from services.b5_versions import projection_methods

MINILM_REVISION = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
MINILM_IDENTITY = "sentence-transformers/all-MiniLM-L6-v2@" + MINILM_REVISION


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def semantic_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def evidence_hash(document: dict | Document) -> str:
    raw = document.model_dump(mode="json") if isinstance(document, Document) else document
    return semantic_hash({key: raw.get(key) for key in ("id", "title", "text", "url", "references", "published_at", "collected_at")})


def _date_key(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def _material(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _material(item) for key, item in value.items()
                if key not in {"cached", "projected_at", "research_run"}}
    if isinstance(value, list):
        return [_material(item) for item in value]
    return value


class ProjectionRepository:
    def __init__(self, target: str):
        self.target = target
        ensure_parent_dir(target)
        self.events = EventStore(target)
        if is_postgres_database(target):
            run_migrations(target)
        else:
            with connect(target) as db:
                db.executescript((MIGRATIONS_DIR / "008_b5_projections.sql").read_text(encoding="utf-8"))

    def _lock(self, db, kind: str, domain_id: str):
        if is_postgres_database(self.target):
            lock_id = int.from_bytes(hashlib.sha256(f"b5:{kind}:{domain_id}".encode()).digest()[:8], "big", signed=True)
            db.execute("SELECT pg_advisory_xact_lock(?)", (lock_id,))
        elif not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _decode(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["data"] = json.loads(result.pop("data_json"))
        result["eligible"] = bool(result["eligible"])
        return result

    def current(self, kind: str, domain_id: str, *, connection=None) -> dict | None:
        with nullcontext(connection) if connection is not None else connect(self.target) as db:
            return self._decode(db.execute(
                "SELECT s.* FROM b5_current c JOIN b5_snapshots s ON s.snapshot_id=c.snapshot_id "
                "WHERE c.kind=? AND c.domain_id=?", (kind, domain_id)).fetchone())

    def get_snapshot(self, snapshot_id: str, *, connection=None) -> dict | None:
        with nullcontext(connection) if connection is not None else connect(self.target) as db:
            return self._decode(db.execute("SELECT * FROM b5_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone())

    def get_snapshots(self,snapshot_ids: list[str]) -> list[dict]:
        results = []
        with connect(self.target) as db:
            for start in range(0,len(snapshot_ids),500):
                batch = list(dict.fromkeys(snapshot_ids[start:start+500]))
                if not batch:
                    continue
                rows = db.execute("SELECT * FROM b5_snapshots WHERE snapshot_id IN ("+",".join("?" for _ in batch)+")",batch).fetchall()
                results.extend(self._decode(row) for row in rows)
        ordered = {snapshot["snapshot_id"]:snapshot for snapshot in results}
        return [ordered[identity] for identity in snapshot_ids if identity in ordered]

    def documents(self, ids: list[str] | None = None, limit: int = 10000, *, after_id: str = "") -> list[dict]:
        if ids is not None and not ids:
            return []
        with connect(self.target) as db:
            query = "SELECT s.* FROM b5_current c JOIN b5_snapshots s ON s.snapshot_id=c.snapshot_id WHERE c.kind='document'"
            parameters: list[Any] = []
            if ids is not None:
                # Batch to respect SQLite's bind limit as well as bounded SQL size.
                if len(ids) > 500:
                    unique = sorted(set(ids))
                    results = []
                    for start in range(0, len(unique), 500):
                        results.extend(self.documents(unique[start:start+500], limit=500,after_id=after_id))
                    return results[:limit]
                query += " AND c.domain_id IN (" + ",".join("?" for _ in ids) + ")"
                parameters.extend(ids)
            if after_id:
                query += " AND c.domain_id>?"
                parameters.append(after_id)
            query += " ORDER BY c.domain_id LIMIT ?"
            parameters.append(min(max(0, limit), 100000))
            return [self._decode(row) for row in db.execute(query, parameters).fetchall()]

    def current_records(self, kind=None) -> list[dict]:
        with connect(self.target) as db:
            query = "SELECT s.* FROM b5_current c JOIN b5_snapshots s ON s.snapshot_id=c.snapshot_id"
            if kind:
                query += " WHERE c.kind=?"
            return [self._decode(row) for row in db.execute(query+" ORDER BY s.kind,s.domain_id", (kind,) if kind else ()).fetchall()]

    def delivery_observations(self, generation: str) -> list[dict]:
        """Read current document delivery identities in one consistent query."""
        with connect(self.target) as db:
            rows = db.execute(
                "SELECT s.domain_id,s.created_at,s.revision,s.semantic_hash,d.target,"
                "d.status,d.applied_at,d.revision AS applied_revision,d.semantic_hash AS applied_hash "
                "FROM b5_current c JOIN b5_snapshots s ON s.snapshot_id=c.snapshot_id "
                "LEFT JOIN b5_deliveries d ON d.kind=c.kind AND d.domain_id=c.domain_id AND d.generation=? "
                "WHERE c.kind='document'", (generation,)).fetchall()
            return [dict(row) for row in rows]

    def _write(self, db, *, kind, domain_id, data, source_event_id, eligible, operation="upsert") -> dict:
        self._lock(db, kind, domain_id)
        digest = semantic_hash(_material(data))
        old_operation = db.execute(
            "SELECT input_hash,snapshot_id FROM b5_source_operations WHERE kind=? AND domain_id=? AND source_event_id=?",
            (kind, domain_id, source_event_id)).fetchone()
        if old_operation is not None:
            if old_operation["input_hash"] != digest:
                raise ValueError("A projection operation identity was reused with different content")
            return self.get_snapshot(old_operation["snapshot_id"], connection=db)
        previous = self.current(kind, domain_id, connection=db)
        # Presentation-only changes and repeated bootstrap must not mint revisions.
        if previous and previous["semantic_hash"] == digest and previous["eligible"] == eligible:
            snapshot = previous
        else:
            revision = previous["revision"] + 1 if previous else 1
            sequence = db.execute("UPDATE b5_clock SET sequence_id=sequence_id+1 WHERE singleton=1 RETURNING sequence_id").fetchone()["sequence_id"]
            snapshot_id = "b5_" + semantic_hash([kind, domain_id, revision, digest])[:32]
            db.execute(
                "INSERT INTO b5_snapshots(snapshot_id,kind,domain_id,revision,sequence_id,semantic_hash,source_event_id,operation,eligible,data_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot_id, kind, domain_id, revision, sequence, digest, source_event_id, operation,
                 int(eligible), canonical_json(_material(data)), _now()))
            db.execute("INSERT INTO b5_current(kind,domain_id,snapshot_id) VALUES(?,?,?) "
                       "ON CONFLICT(kind,domain_id) DO UPDATE SET snapshot_id=excluded.snapshot_id", (kind, domain_id, snapshot_id))
            snapshot = self.get_snapshot(snapshot_id, connection=db)
            event_class = CorpusProjectionEvent if kind == "document" else InvestigationProjectionEvent
            topic = CORPUS_PROJECTIONS_TOPIC if kind == "document" else INVESTIGATION_PROJECTIONS_TOPIC
            event = event_class.create(
                ProjectionPayload(snapshot_id=snapshot_id, domain_id=domain_id, revision=revision,
                                  semantic_hash=digest, semantic_output_hash=digest, operation=operation,
                                  eligible=eligible, source_event_id=source_event_id,
                                  run_id=data.get("run_id"), terminal_decision=data.get("terminal_decision")),
                producer="b5-canonical", correlation_id=domain_id, partition_key=domain_id,
                event_id=f"evt_{snapshot_id}", causation_id=source_event_id)
            self.events.enqueue(topic, event, connection=db)
        db.execute("INSERT INTO b5_source_operations(kind,domain_id,source_event_id,input_hash,snapshot_id) VALUES(?,?,?,?,?)",
                   (kind, domain_id, source_event_id, digest, snapshot["snapshot_id"]))
        return snapshot

    def record_document(self, document: Document, *, source_event_id: str, enrichment=None,
                        processing=None, source_kind="research", connection=None, supersede=False) -> dict:
        document = Document.model_validate(document)
        doc = document.model_dump(mode="json", exclude={"embedding"})
        data = {"document": doc, "enrichment": enrichment.model_dump(mode="json") if hasattr(enrichment, "model_dump") else enrichment,
                "processing": processing.model_dump(mode="json") if hasattr(processing, "model_dump") else processing or {},
                "source_kind": source_kind, "withdrawn": False}
        data["model_identities"] = {"semantic": MINILM_IDENTITY}
        data["projection_methods"] = projection_methods()
        eligible = source_kind != "discovery" and (document.metadata or {}).get("acquisition_receipt_valid") is True
        with nullcontext(connection) if connection is not None else connect(self.target) as db:
            self._lock(db, "document", document.id)
            # Detect source-ID collisions even if current eligibility changed later.
            existing_op = db.execute("SELECT snapshot_id,input_hash FROM b5_source_operations WHERE kind='document' AND domain_id=? AND source_event_id=?",
                                     (document.id, source_event_id)).fetchone()
            if existing_op is not None:
                # Source digest is persisted separately from state-adjusted snapshot digest.
                incoming_digest = semantic_hash(data)
                if existing_op["input_hash"] != incoming_digest:
                    raise ValueError("Document event identity has different content")
                return self.get_snapshot(existing_op["snapshot_id"], connection=db)
            previous = self.current("document", document.id, connection=db)
            input_digest = semantic_hash(data)
            if previous:
                prior_doc = previous["data"]["document"]
                prior_receipted = previous["data"]["source_kind"] != "discovery" and (prior_doc.get("metadata") or {}).get("acquisition_receipt_valid") is True
                prior_date = _date_key(prior_doc.get("collected_at"))
                next_date = _date_key(doc.get("collected_at"))
                same_input = semantic_hash([doc.get("text"), doc.get("title"), doc.get("url")]) == semantic_hash([prior_doc.get("text"), prior_doc.get("title"), prior_doc.get("url")])
                stale = (prior_receipted and not eligible) or (prior_receipted == eligible and (next_date, source_event_id) < (prior_date, previous["source_event_id"]))
                if supersede and not same_input:
                    raise ValueError("Re-enrichment supersession requires the current input")
                if stale and not supersede:
                    db.execute("INSERT INTO b5_source_operations(kind,domain_id,source_event_id,input_hash,snapshot_id) VALUES('document',?,?,?,?)",
                               (document.id, source_event_id, input_digest, previous["snapshot_id"]))
                    return previous
                if previous["data"].get("withdrawn"):
                    data["withdrawn"] = True
                    eligible = False
            result = self._write(db, kind="document", domain_id=document.id, data=data,
                                 source_event_id=source_event_id, eligible=eligible)
            # Redelivery compares acquisition input, independent of withdrawal state.
            db.execute("UPDATE b5_source_operations SET input_hash=? WHERE kind='document' AND domain_id=? AND source_event_id=?",
                       (input_digest, document.id, source_event_id))
            self._sync_corpus(db, result)
            return result

    def _sync_corpus(self, db, snapshot):
        if not is_postgres_database(self.target):
            return
        from services.postgres_corpus import DEFAULT_MODEL, embedding_input_hash
        document = Document.model_validate(snapshot["data"]["document"])
        input_hash = embedding_input_hash(document)
        db.execute("INSERT INTO semantic_document_corpus(document_id,document_json,source_kind,source_provenance_json,citable,embedding_model,embedding_input_hash,embedding_status) "
                   "VALUES(?,?,?,?,?,?,?,'pending') ON CONFLICT(document_id) DO UPDATE SET "
                   "document_json=excluded.document_json,source_kind=excluded.source_kind,source_provenance_json=excluded.source_provenance_json,citable=excluded.citable,"
                   "embedding=CASE WHEN semantic_document_corpus.embedding_input_hash=excluded.embedding_input_hash AND semantic_document_corpus.embedding_model=excluded.embedding_model THEN semantic_document_corpus.embedding ELSE NULL END,"
                   "embedding_status=CASE WHEN semantic_document_corpus.embedding_input_hash=excluded.embedding_input_hash AND semantic_document_corpus.embedding_model=excluded.embedding_model THEN semantic_document_corpus.embedding_status ELSE 'pending' END,"
                   "embedding_model=excluded.embedding_model,embedding_input_hash=excluded.embedding_input_hash,updated_at=NOW()",
                   (document.id,document.model_dump_json(),snapshot["data"]["source_kind"],canonical_json(document.metadata or {}),
                    snapshot["eligible"],DEFAULT_MODEL,input_hash))

    def record_investigation(self, workspace, *, run_id: str, terminal_decision: str,
                             source_event_id: str, connection=None) -> dict:
        raw = workspace.model_dump(mode="json") if hasattr(workspace, "model_dump") else dict(workspace)
        domain_id = raw.get("investigation_id") or raw.get("id")
        if not domain_id:
            raise ValueError("Investigation snapshot requires investigation_id")
        with nullcontext(connection) if connection is not None else connect(self.target) as db:
            self._lock(db, "investigation", domain_id)
            source_digest = semantic_hash({"workspace":_material(raw),"run_id":run_id,"terminal_decision":terminal_decision})
            operation = db.execute("SELECT input_hash,snapshot_id FROM b5_source_operations WHERE kind='investigation' AND domain_id=? AND source_event_id=?",
                                   (domain_id,source_event_id)).fetchone()
            if operation:
                if operation["input_hash"] != source_digest:
                    raise ValueError("Investigation operation identity has different content")
                return self.get_snapshot(operation["snapshot_id"],connection=db)
            snapshot_ids = []
            historical_limitations = []
            for doc in raw.get("retrieved_documents", []):
                current = self.current("document", doc["id"], connection=db)
                if current and evidence_hash(current["data"]["document"]) != evidence_hash(doc):
                    rows = db.execute("SELECT * FROM b5_snapshots WHERE kind='document' AND domain_id=? ORDER BY revision DESC", (doc["id"],)).fetchall()
                    current = next((self._decode(row) for row in rows if evidence_hash(json.loads(row["data_json"])["document"]) == evidence_hash(doc)), None)
                if current is None:
                    if self.current("document",doc["id"],connection=db) is None:
                        document = Document.model_validate(doc)
                        current = self.record_document(document,source_event_id=f"investigation-evidence:{domain_id}:{run_id}:{evidence_hash(doc)}",
                                                       source_kind="retrieved",connection=db)
                    else:
                        historical_limitations.append(f"Historical document {doc['id']} is preserved in the workspace; no matching corpus snapshot exists.")
                if current:
                    snapshot_ids.append(current["snapshot_id"])
            if terminal_decision != "publish":
                # Existing runtime uses 'published'/'completed' as well as publish.
                if terminal_decision not in {"published", "completed"}:
                    raw["report"] = None
            data = {"workspace": _material(raw), "run_id": run_id, "terminal_decision": terminal_decision,
                    "document_snapshot_ids": snapshot_ids,"limitations":historical_limitations,
                    "projection_methods": projection_methods()}
            result = self._write(db, kind="investigation", domain_id=domain_id, data=data,
                                 source_event_id=source_event_id, eligible=raw.get("report") is not None)
            db.execute("UPDATE b5_source_operations SET input_hash=? WHERE kind='investigation' AND domain_id=? AND source_event_id=?",
                       (source_digest,domain_id,source_event_id))
            return result

    def withdraw(self, document_id: str, *, operation_id: str, reason: str, restore=False) -> dict:
        if not operation_id.strip() or not reason.strip():
            raise ValueError("Operation identity and reason are required")
        action = "restore" if restore else "withdraw"
        with connect(self.target) as db:
            self._lock(db, "document", document_id)
            audit = db.execute("SELECT * FROM b5_operator_audit WHERE operation_id=?", (operation_id,)).fetchone()
            if audit:
                if (audit["document_id"], audit["action"], audit["reason"]) != (document_id, action, reason):
                    raise ValueError("Operator identity was reused")
                return self.get_snapshot(audit["snapshot_id"], connection=db)
            previous = self.current("document", document_id, connection=db)
            if not previous:
                raise ValueError("Unknown document")
            data = json.loads(canonical_json(previous["data"]))
            data["withdrawn"] = not restore
            data["withdrawal"] = {"operation_id": operation_id, "reason": reason, "action": action}
            eligible = restore and data["source_kind"] != "discovery" and (data["document"].get("metadata") or {}).get("acquisition_receipt_valid") is True
            result = self._write(db, kind="document", domain_id=document_id, data=data,
                                 source_event_id=f"operator:{operation_id}", eligible=eligible, operation=action)
            db.execute("INSERT INTO b5_operator_audit(operation_id,document_id,action,reason,snapshot_id,created_at) VALUES(?,?,?,?,?,?)",
                       (operation_id, document_id, action, reason, result["snapshot_id"], _now()))
            self._sync_corpus(db, result)
            return result

    def manifest(self) -> dict:
        with connect(self.target) as db:
            return json.loads(db.execute("SELECT manifest_json FROM b5_manifest WHERE singleton=1").fetchone()["manifest_json"])

    def high_watermark(self) -> int:
        with connect(self.target) as db:
            return int(db.execute("SELECT sequence_id FROM b5_clock WHERE singleton=1").fetchone()["sequence_id"])

    def snapshots(self, *, after=0, through=None, limit=1000) -> list[dict]:
        with connect(self.target) as db:
            rows = db.execute("SELECT * FROM b5_snapshots WHERE sequence_id>? AND sequence_id<=? ORDER BY sequence_id LIMIT ?",
                              (after, through if through is not None else self.high_watermark(), min(limit, 10000))).fetchall()
            return [self._decode(row) for row in rows]

    def delivery(self, target, kind, domain_id, generation="live") -> dict | None:
        with connect(self.target) as db:
            row = db.execute("SELECT * FROM b5_deliveries WHERE target=? AND generation=? AND kind=? AND domain_id=?",
                             (target, generation, kind, domain_id)).fetchone()
            return dict(row) if row else None

    def coverage(self,snapshots: list[dict],generation: str) -> dict:
        ids = sorted({item["domain_id"] for item in snapshots})
        deliveries = {}
        with connect(self.target) as db:
            for start in range(0,len(ids),500):
                batch = ids[start:start+500]
                rows = db.execute("SELECT * FROM b5_deliveries WHERE generation=? AND domain_id IN ("+",".join("?" for _ in batch)+")",[generation,*batch]).fetchall()
                deliveries.update({(row["target"],row["kind"],row["domain_id"]):dict(row) for row in rows})
        result = {}
        for target in ("elasticsearch","neo4j","minilm"):
            eligible = [item for item in snapshots if target=="neo4j" or item["kind"]=="document"]
            applied = []
            for item in eligible:
                delivery = deliveries.get((target,item["kind"],item["domain_id"]))
                if delivery and delivery["status"]=="applied" and (delivery["revision"],delivery["semantic_hash"])==(item["revision"],item["semantic_hash"]):
                    applied.append(delivery)
            result[target] = {"total":len(eligible),"applied":len(applied),"pending":len(eligible)-len(applied),
                "last_applied_at":max((item["applied_at"] for item in applied),default=None)}
        return result

    def mark_delivery(self, target, snapshot, generation="live", *, error=None):
        with connect(self.target) as db:
            self._lock(db, f"delivery:{target}:{generation}:{snapshot['kind']}", snapshot["domain_id"])
            row = db.execute("SELECT revision,semantic_hash FROM b5_deliveries WHERE target=? AND generation=? AND kind=? AND domain_id=?",
                             (target, generation, snapshot["kind"], snapshot["domain_id"])).fetchone()
            if row and row["revision"] > snapshot["revision"]:
                return
            if row and row["revision"] == snapshot["revision"] and row["semantic_hash"] != snapshot["semantic_hash"]:
                raise ValueError("Delivery revision hash conflict")
            db.execute("INSERT INTO b5_deliveries(target,generation,kind,domain_id,revision,sequence_id,semantic_hash,eligible,status,applied_at,last_error) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(target,generation,kind,domain_id) DO UPDATE SET "
                       "revision=excluded.revision,sequence_id=excluded.sequence_id,semantic_hash=excluded.semantic_hash,eligible=excluded.eligible,"
                       "status=excluded.status,applied_at=excluded.applied_at,last_error=excluded.last_error",
                       (target, generation, snapshot["kind"], snapshot["domain_id"], snapshot["revision"], snapshot["sequence_id"],
                        snapshot["semantic_hash"], int(snapshot["eligible"]), "failed" if error else "applied", None if error else _now(), error))

    def cache_token(self, kind, domain_id, target, generation="live") -> str:
        with connect(self.target) as db:
            canonical = db.execute("SELECT COALESCE(MAX(s.sequence_id),0) AS n FROM b5_current c JOIN b5_snapshots s ON s.snapshot_id=c.snapshot_id "
                                   "WHERE c.kind=?" + (" AND c.domain_id=?" if domain_id else ""),
                                   (kind, domain_id) if domain_id else (kind,)).fetchone()["n"]
            documents = db.execute("SELECT COALESCE(MAX(s.sequence_id),0) AS n FROM b5_current c JOIN b5_snapshots s ON s.snapshot_id=c.snapshot_id WHERE c.kind='document'").fetchone()["n"]
            applied = db.execute("SELECT COALESCE(SUM(sequence_id),0) AS n,COUNT(*) AS count FROM b5_deliveries WHERE generation=? AND status='applied'",
                                 (generation,)).fetchone()
        return f"{generation}:{canonical}:{documents}:{applied['n']}:{applied['count']}"

    def embedding(self, model, input_hash) -> list[float] | None:
        with connect(self.target) as db:
            row = db.execute("SELECT embedding_json FROM b5_embedding_artifacts WHERE model=? AND input_hash=?", (model, input_hash)).fetchone()
        return json.loads(row["embedding_json"]) if row else None

    def save_embedding(self, model, input_hash, vector):
        from services.postgres_corpus import DEFAULT_MODEL, validate_embedding, _vector_literal
        if model not in {DEFAULT_MODEL, MINILM_IDENTITY}:
            raise ValueError("Recorded embedding must identify the pinned MiniLM space")
        vector = validate_embedding(vector, model_name=DEFAULT_MODEL, expected_model=DEFAULT_MODEL)
        _vector_literal(vector)
        digest = semantic_hash(vector)
        with connect(self.target) as db:
            db.execute("INSERT INTO b5_embedding_artifacts(model,input_hash,embedding_json,output_hash,created_at) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(model,input_hash) DO NOTHING", (model, input_hash, canonical_json(vector), digest, _now()))
            row = db.execute("SELECT output_hash FROM b5_embedding_artifacts WHERE model=? AND input_hash=?", (model, input_hash)).fetchone()
            if row["output_hash"] != digest:
                raise ValueError("Recorded embedding identity is immutable")

    def heartbeat(self, worker_id, target, state="ready", detail=None):
        with connect(self.target) as db:
            db.execute("INSERT INTO b5_worker_health(worker_id,target,state,heartbeat_at,detail) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(worker_id) DO UPDATE SET state=excluded.state,heartbeat_at=excluded.heartbeat_at,detail=excluded.detail",
                       (worker_id, target, state, _now(), detail))

    def status(self) -> dict:
        targets = {}
        now = datetime.now(timezone.utc)
        manifest = self.manifest()
        with connect(self.target) as db:
            counts = db.execute("SELECT kind,COUNT(*) AS n FROM b5_current GROUP BY kind").fetchall()
            workers = [dict(row) for row in db.execute("SELECT * FROM b5_worker_health").fetchall()]
            failures = db.execute("SELECT COUNT(*) AS n FROM b5_deliveries WHERE status='failed'").fetchone()["n"]
            embeddings = db.execute("SELECT COUNT(*) AS n FROM b5_embedding_artifacts").fetchone()["n"]
            for target in ("elasticsearch", "neo4j", "minilm"):
                rows = db.execute("SELECT s.sequence_id,s.created_at,d.status,d.applied_at FROM b5_current c JOIN b5_snapshots s ON c.snapshot_id=s.snapshot_id "
                    "LEFT JOIN b5_deliveries d ON d.target=? AND d.generation=? AND d.kind=s.kind AND d.domain_id=s.domain_id AND d.revision=s.revision AND d.semantic_hash=s.semantic_hash "
                    "WHERE s.kind='document' OR ?='neo4j'",(target,manifest["generation"],target)).fetchall()
                pending = [row for row in rows if row["status"] != "applied"]
                ages = [max(0,(now-datetime.fromisoformat(row["created_at"])).total_seconds()) for row in pending]
                applied = [row["applied_at"] for row in rows if row["status"] == "applied"]
                targets[target] = {"pending": len(pending), "total": len(rows), "oldest_pending_seconds": max(ages,default=0),
                    "applied": len(rows)-len(pending), "last_successful_application": max(applied,default=None)}
            validations = [dict(row) for row in db.execute("SELECT generation,status,validated_at,validation_json FROM b5_generations").fetchall()]
            for row in validations:
                raw_validation = row.pop("validation_json")
                row["validation"] = json.loads(raw_validation) if raw_validation else None
        return {"manifest": manifest, "high_watermark": self.high_watermark(), "counts": {row["kind"]:row["n"] for row in counts}, "targets": targets,
                "generations": validations,
                "workers": workers, "failed_deliveries": failures, "recorded_embeddings": embeddings}

    def create_generation(self, generation: str) -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", generation) or generation == self.manifest()["generation"]:
            raise ValueError("Use a new bounded lowercase generation name")
        watermark = self.high_watermark()
        with connect(self.target) as db:
            db.execute("INSERT INTO b5_generations(generation,status,high_watermark,created_at) VALUES(?,'building',?,?) "
                       "ON CONFLICT(generation) DO NOTHING", (generation, watermark, _now()))
        return {"generation":generation,"high_watermark":watermark}

    def activate_generation(self, generation: str, validation: dict):
        if not validation.get("complete") or validation.get("drift"):
            raise ValueError("Generation validation must pass before activation")
        with connect(self.target) as db:
            self._lock(db, "manifest", "active")
            db.execute("UPDATE b5_clock SET sequence_id=sequence_id WHERE singleton=1")
            row = db.execute("SELECT status FROM b5_generations WHERE generation=?", (generation,)).fetchone()
            if not row or row["status"] not in {"building","retired"}:
                raise ValueError("Unknown or already active generation")
            actual = db.execute("SELECT sequence_id FROM b5_clock WHERE singleton=1").fetchone()["sequence_id"]
            if validation.get("high_watermark") != actual:
                raise ValueError("Canonical data changed during validation; catch up and revalidate")
            prior = json.loads(db.execute("SELECT manifest_json FROM b5_manifest WHERE singleton=1").fetchone()["manifest_json"])
            manifest = {"generation":generation,"elasticsearch_index":f"rhetoriq-documents-b5-{generation}","previous_generation":prior["generation"]}
            db.execute("UPDATE b5_manifest SET manifest_json=? WHERE singleton=1", (canonical_json(manifest),))
            db.execute("UPDATE b5_generations SET status='retired' WHERE generation=?", (prior["generation"],))
            db.execute("UPDATE b5_generations SET status='active',validated_at=?,validation_json=? WHERE generation=?",
                       (_now(),canonical_json(validation),generation))

    def building_generations(self) -> list[str]:
        with connect(self.target) as db:
            return [row["generation"] for row in db.execute("SELECT generation FROM b5_generations WHERE status='building'").fetchall()]
