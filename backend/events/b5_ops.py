"""Audited operator workflows. Rebuilds use snapshots, never hosted inference."""
from __future__ import annotations

import argparse
import json
from uuid import uuid4

from config import get_settings
from events.b5_worker import ProjectionHandlers
from models.document import Document
from services.b5_repository import ProjectionRepository, canonical_json, semantic_hash
from services.database import connect, is_postgres_database


def _document_snapshots(repository, snapshot):
    return [repository.get_snapshot(ref) for ref in snapshot["data"].get("document_snapshot_ids", [])]


def validate_input(snapshot):
    errors = []
    if semantic_hash(snapshot["data"]) != snapshot["semantic_hash"]:
        errors.append("snapshot_hash")
    if snapshot["kind"] == "document":
        data = snapshot["data"]
        document = Document.model_validate(data["document"])
        enrichment = data.get("enrichment") or {}
        for mention in enrichment.get("canonical_phrases", []) + enrichment.get("entities", []):
            evidence = mention.get("evidence") or {}
            start,end = evidence.get("start"),evidence.get("end")
            if not isinstance(start,int) or not isinstance(end,int) or not 0<=start<=end<=len(document.text) or document.text[start:end] != evidence.get("surface_form"):
                errors.append("mention_span")
        for reference in document.references:
            if reference.start is not None and (reference.end > len(document.text) or document.text[reference.start:reference.end] != reference.anchor_text):
                errors.append("reference_span")
        if data.get("withdrawn") and snapshot["eligible"]:
            errors.append("withdrawal_eligibility")
    return sorted(set(errors))


def reconcile(repository, targets, generation=None, *, repair=False, repair_limit=100):
    generation = generation or repository.manifest()["generation"]
    watermark = repository.high_watermark()
    current = repository.current_records()
    drift, repaired = [],0
    for target_name,adapter in targets.items():
        inventory = adapter.inventory(generation)
        relevant = [s for s in current if target_name=="neo4j" or s["kind"]=="document"]
        expected = {(s["domain_id"] if s["kind"]=="document" else "investigation:"+s["domain_id"]):s for s in relevant}
        for key,snapshot in expected.items():
            actual = inventory.get(key)
            reasons = validate_input(snapshot)
            if actual is None:
                reasons.append("missing")
            elif (actual.get("revision"),actual.get("semantic_hash"),bool(actual.get("eligible"))) != (snapshot["revision"],snapshot["semantic_hash"],snapshot["eligible"]):
                reasons.append("revision_hash_eligibility")
            validate = getattr(adapter,"validate_snapshot",None)
            documents = _document_snapshots(repository,snapshot)
            if any(item is None for item in documents):
                reasons.append("missing_recorded_document")
            if validate and actual:
                reasons.extend(validate(snapshot,generation,document_snapshots=documents))
            if reasons:
                record = {"target":target_name,"kind":snapshot["kind"],"domain_id":snapshot["domain_id"],"reasons":sorted(set(reasons))}
                if repair and repaired<repair_limit and not validate_input(snapshot) and "missing_recorded_document" not in reasons:
                    result = adapter.repair(snapshot,generation,document_snapshots=documents)
                    successful = result.get("status") in {"repaired","applied"} and not result.get("blocked")
                    repository.mark_delivery(target_name,snapshot,generation,error=None if successful else "RepairBlocked")
                    record["repaired"] = successful
                    repaired += 1
                drift.append(record)
        for key in sorted(set(inventory)-set(expected)):
            kind = "investigation" if key.startswith("investigation:") else "document"
            domain_id = key.removeprefix("investigation:") if kind=="investigation" else key
            record = {"target":target_name,"kind":kind,"domain_id":domain_id,"reasons":["unexpected"]}
            if repair and repaired<repair_limit:
                adapter.remove_unexpected(domain_id,generation,kind=kind)
                record["repaired"] = True
                repaired += 1
            drift.append(record)
    stable = repository.high_watermark()==watermark
    return {"generation":generation,"high_watermark":watermark,"complete":stable,"drift":drift,"repaired":repaired,
            "canonical_records":len(current)}


def rebuild(repository, targets, generation, *, after=0, batch_size=100):
    repository.create_generation(generation)
    for adapter in targets.values():
        adapter.initialize(generation)
    watermark = repository.high_watermark()
    cursor = after
    count = 0
    while True:
        snapshots = repository.snapshots(after=cursor,through=watermark,limit=batch_size)
        if not snapshots:
            break
        for snapshot in snapshots:
            if validate_input(snapshot):
                raise ValueError("Recorded snapshot failed validation")
            documents = _document_snapshots(repository,snapshot)
            for name,adapter in targets.items():
                if name!="neo4j" and snapshot["kind"]!="document":
                    continue
                result = adapter.apply(snapshot,generation,document_snapshots=documents)
                if result.get("status") not in {"applied","stale","skipped"}:
                    raise RuntimeError("Rebuild target did not confirm application")
                repository.mark_delivery(name,snapshot,generation)
            cursor = snapshot["sequence_id"]
            count += 1
    return {"generation":generation,"processed":count,"next_after":cursor,"high_watermark":watermark}


def bootstrap(repository, *, after_id="", batch_size=100):
    """Bounded bootstrap; old HTML/reference coverage is never fabricated."""
    with connect(repository.target) as db:
        def exists(table):
            if is_postgres_database(repository.target):
                return db.execute("SELECT to_regclass(?) AS name",(table,)).fetchone()["name"] is not None
            return db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None
        tables = [table for table in ("research_documents","retrieved_documents","discovery_documents") if exists(table)]
        candidates = []
        for table in tables:
            kind = "discovery" if table=="discovery_documents" else "research" if table=="research_documents" else "retrieved"
            rows = db.execute(f"SELECT doc_id,document_json FROM {table} WHERE doc_id>? ORDER BY doc_id LIMIT ?",(after_id,batch_size)).fetchall()
            candidates.extend((row["doc_id"],kind,row["document_json"]) for row in rows)
        if exists("processed_document_lineage"):
            rows = db.execute("SELECT document_id,document_json,enrichment_receipt_json,event_json FROM processed_document_lineage "
                              "WHERE document_id>? ORDER BY document_id LIMIT ?",(after_id,batch_size)).fetchall()
            candidates.extend((row["document_id"],"ingestion",row["document_json"],row["enrichment_receipt_json"],row["event_json"]) for row in rows)
        if exists("semantic_document_corpus"):
            rows = db.execute("SELECT document_id,document_json,source_kind FROM semantic_document_corpus WHERE document_id>? ORDER BY document_id LIMIT ?",(after_id,batch_size)).fetchall()
            candidates.extend((row["document_id"],row["source_kind"],row["document_json"]) for row in rows)
    ids = sorted({item[0] for item in candidates})[:batch_size]
    selected = set(ids)
    processed = 0
    for item in sorted((x for x in candidates if x[0] in selected),key=lambda x:(x[0],x[1],x[2])):
        doc = Document.model_validate_json(item[2])
        if not doc.references and not (doc.metadata or {}).get("reference_extraction"):
            doc = doc.model_copy(update={"metadata":{**(doc.metadata or {}),"reference_extraction":"historical_unqualified",
                "reference_limitations":["Retained canonical HTML unavailable; historical source-link extraction is unqualified."]}})
        enrichment = json.loads(item[3]) if len(item)>3 and item[3] else None
        source_event_id = json.loads(item[4]).get("event_id") if len(item)>4 else None
        repository.record_document(doc,source_kind=item[1],enrichment=enrichment,
                                   source_event_id=f"bootstrap:{source_event_id or semantic_hash([item[1],item[2]])}")
        processed += 1
    return {"processed":processed,"next_after_id":ids[-1] if ids else after_id,"complete":not ids}


def bootstrap_investigations(repository, *, after_id="", batch_size=100):
    from services.investigation_repository import InvestigationRepository
    from services.research_repository import ResearchRepository
    inv = InvestigationRepository(repository.target)
    research = ResearchRepository(repository.target)
    with connect(repository.target) as db:
        ids = [row["investigation_id"] for row in db.execute("SELECT investigation_id FROM investigations WHERE investigation_id>? ORDER BY investigation_id LIMIT ?",
                                                          (after_id,batch_size)).fetchall()]
    for domain_id in ids:
        workspace = inv.get_investigation_workspace(domain_id)
        run = research.get_latest_run(domain_id)
        if workspace is None or run and run.status in {"queued","running"}:
            continue
        decision = run.terminal_decision if run else "published" if workspace.report else "insufficient_evidence"
        repository.record_investigation(workspace,run_id=run.run_id if run else f"historical:{domain_id}",terminal_decision=decision,
                                        source_event_id=f"bootstrap-investigation:{domain_id}:{semantic_hash(workspace.model_dump(mode='json',exclude={'research_run'}))}")
    return {"processed":len(ids),"next_after_id":ids[-1] if ids else after_id,"complete":not ids}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["withdraw","restore","bootstrap","bootstrap-investigations","check","repair","rebuild","activate","rollback","semantic-replay","status"])
    parser.add_argument("--document-id")
    parser.add_argument("--operation-id")
    parser.add_argument("--reason")
    parser.add_argument("--generation")
    parser.add_argument("--after",type=int,default=0)
    parser.add_argument("--after-id",default="")
    parser.add_argument("--batch-size",type=int,default=100)
    parser.add_argument("--repair-limit",type=int,default=100)
    args = parser.parse_args()
    if not 1<=args.batch_size<=10000 or not 1<=args.repair_limit<=1000:
        parser.error("Batch and repair limits are bounded positive values")
    settings = get_settings()
    repository = ProjectionRepository(settings.persistence_target)
    if args.action in {"withdraw","restore"}:
        if not all((args.document_id,args.operation_id,args.reason)):
            parser.error("Withdrawal/restore require document ID, operation ID, and reason")
        result = repository.withdraw(args.document_id,operation_id=args.operation_id,reason=args.reason,restore=args.action=="restore")
        print(canonical_json({key:result[key] for key in ("snapshot_id","revision","operation","eligible")}))
        return
    if args.action=="status":
        result = repository.status()
    elif args.action=="bootstrap":
        result = bootstrap(repository,after_id=args.after_id,batch_size=args.batch_size)
    elif args.action=="bootstrap-investigations":
        result = bootstrap_investigations(repository,after_id=args.after_id,batch_size=args.batch_size)
    elif args.action=="semantic-replay":
        handler = ProjectionHandlers("minilm",settings=settings,repository=repository,recorded_only=True)
        result = {"processed":0}
        batch = repository.documents(limit=args.batch_size,after_id=args.after_id)
        for snapshot in batch:
            handler.apply(snapshot,repository.manifest()["generation"])
            result["processed"] += 1
        result.update({"next_after_id":batch[-1]["domain_id"] if batch else args.after_id,"complete":not batch})
    else:
        from services.b5_targets import ElasticsearchTarget, Neo4jTarget
        targets = {"elasticsearch":ElasticsearchTarget(settings),"neo4j":Neo4jTarget(settings)}
        generation = args.generation or repository.manifest()["generation"]
        job_id = args.operation_id or f"b5_{args.action}_{uuid4().hex}"
        repository.events.create_replay_job(job_id,f"b5:{args.action}",correlation_id=generation,partition=None,start_offset=args.after,end_offset=repository.high_watermark())
        try:
            if args.action=="rebuild":
                if not args.generation:
                    parser.error("Rebuild requires an isolated --generation")
                result = rebuild(repository,targets,args.generation,after=args.after,batch_size=args.batch_size)
            else:
                if args.action=="rollback":
                    generation = repository.manifest().get("previous_generation")
                    if not generation:
                        raise ValueError("No previous generation")
                result = reconcile(repository,targets,generation,repair=args.action=="repair",repair_limit=args.repair_limit)
                if args.action in {"activate","rollback"}:
                    repository.activate_generation(generation,result)
            # Existing replay audit table supplies a persistent admin audit trail.
            repository.events.finish_replay_job(job_id,processed_count=result.get("processed",result.get("repaired",0)),skipped_count=len(result.get("drift",[])))
        except BaseException as exc:
            repository.events.finish_replay_job(job_id,processed_count=0,skipped_count=0,error=type(exc).__name__)
            raise
        finally:
            for adapter in targets.values():
                if hasattr(adapter,"close"):
                    adapter.close()
    print(canonical_json(result))
    if args.action=="check" and (result.get("drift") or not result.get("complete")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
