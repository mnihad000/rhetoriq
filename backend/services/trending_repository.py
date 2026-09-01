from __future__ import annotations

import json
from datetime import datetime, timezone

from models.document import Document
from migrations.runner import run_migrations
from services.database import connect, ensure_parent_dir, is_postgres_database
from models.trending import (
    DiscoveryDocumentRecord,
    DiscoveryQuery,
    DiscoveryRunRecord,
    DiscoveryRunStats,
    PublishedTrendingSnapshot,
    TrendingTopic,
)


class TrendingRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_parent_dir()
        if is_postgres_database(db_path):
            run_migrations(db_path)
        else:
            self._init_schema()

    def create_run(
        self,
        run_id: str,
        *,
        is_reseed: bool,
        queries: list[DiscoveryQuery],
    ) -> DiscoveryRunRecord:
        started_at = datetime.now(timezone.utc)
        record = DiscoveryRunRecord(
            run_id=run_id,
            started_at=started_at,
            is_reseed=is_reseed,
            queries=queries,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO discovery_runs (
                    run_id, status, is_reseed, started_at, completed_at, stats_json,
                    warnings_json, error, queries_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    record.status,
                    1 if is_reseed else 0,
                    started_at.isoformat(),
                    None,
                    record.stats.model_dump_json(),
                    json.dumps([]),
                    None,
                    json.dumps([query.model_dump(mode="json") for query in queries]),
                ),
            )
        return record

    def complete_run(
        self,
        run_id: str,
        *,
        stats: DiscoveryRunStats,
        warnings: list[str],
        error: str | None = None,
    ) -> DiscoveryRunRecord | None:
        existing = self.get_run(run_id)
        if existing is None:
            return None
        completed_at = datetime.now(timezone.utc)
        status = "failed" if error else "completed"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE discovery_runs
                SET status = ?, completed_at = ?, stats_json = ?, warnings_json = ?, error = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    completed_at.isoformat(),
                    stats.model_dump_json(),
                    json.dumps(warnings),
                    error,
                    run_id,
                ),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> DiscoveryRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, status, is_reseed, started_at, completed_at, stats_json,
                       warnings_json, error, queries_json
                FROM discovery_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return DiscoveryRunRecord.model_validate(
            {
                "run_id": row["run_id"],
                "status": row["status"],
                "is_reseed": bool(row["is_reseed"]),
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "stats": json.loads(row["stats_json"]),
                "warnings": json.loads(row["warnings_json"]),
                "error": row["error"],
                "queries": json.loads(row["queries_json"]),
            }
        )

    def list_recent_runs(self, limit: int = 8) -> list[DiscoveryRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, status, is_reseed, started_at, completed_at, stats_json,
                       warnings_json, error, queries_json
                FROM discovery_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            DiscoveryRunRecord.model_validate(
                {
                    "run_id": row["run_id"],
                    "status": row["status"],
                    "is_reseed": bool(row["is_reseed"]),
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "stats": json.loads(row["stats_json"]),
                    "warnings": json.loads(row["warnings_json"]),
                    "error": row["error"],
                    "queries": json.loads(row["queries_json"]),
                }
            )
            for row in rows
        ]

    def save_discovery_document(
        self,
        run_id: str,
        document: Document,
        *,
        canonical_url: str,
        domain: str,
        provider: str,
        search_query: str,
    ) -> tuple[DiscoveryDocumentRecord, bool]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT doc_id, canonical_url, domain, document_json, providers_json, search_queries_json,
                       first_seen_at, latest_seen_at, first_run_id, latest_run_id, seen_run_ids_json
                FROM discovery_documents
                WHERE doc_id = ?
                """,
                (document.id,),
            ).fetchone()

            if existing is None:
                record = DiscoveryDocumentRecord(
                    doc_id=document.id,
                    canonical_url=canonical_url,
                    domain=domain,
                    document=document,
                    providers=[provider],
                    search_queries=[search_query],
                    first_seen_at=datetime.fromisoformat(now),
                    latest_seen_at=datetime.fromisoformat(now),
                    first_run_id=run_id,
                    latest_run_id=run_id,
                    seen_run_ids=[run_id],
                )
                conn.execute(
                    """
                    INSERT INTO discovery_documents (
                        doc_id, canonical_url, domain, document_json, providers_json, search_queries_json,
                        first_seen_at, latest_seen_at, first_run_id, latest_run_id, seen_run_ids_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.doc_id,
                        record.canonical_url,
                        record.domain,
                        record.document.model_dump_json(),
                        json.dumps(record.providers),
                        json.dumps(record.search_queries),
                        record.first_seen_at.isoformat(),
                        record.latest_seen_at.isoformat(),
                        record.first_run_id,
                        record.latest_run_id,
                        json.dumps(record.seen_run_ids),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO discovery_document_runs (
                        run_id, doc_id, provider, search_query, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (run_id, doc_id, provider, search_query) DO NOTHING
                    """,
                    (run_id, document.id, provider, search_query, now),
                )
                return record, True

            record = DiscoveryDocumentRecord.model_validate(
                {
                    "doc_id": existing["doc_id"],
                    "canonical_url": existing["canonical_url"],
                    "domain": existing["domain"],
                    "document": json.loads(existing["document_json"]),
                    "providers": json.loads(existing["providers_json"]),
                    "search_queries": json.loads(existing["search_queries_json"]),
                    "first_seen_at": existing["first_seen_at"],
                    "latest_seen_at": existing["latest_seen_at"],
                    "first_run_id": existing["first_run_id"],
                    "latest_run_id": existing["latest_run_id"],
                    "seen_run_ids": json.loads(existing["seen_run_ids_json"]),
                }
            )
            providers = list(dict.fromkeys([*record.providers, provider]))
            search_queries = list(dict.fromkeys([*record.search_queries, search_query]))
            seen_run_ids = list(dict.fromkeys([*record.seen_run_ids, run_id]))
            merged_document = record.document.model_copy(
                update={
                    "metadata": {
                        **(record.document.metadata or {}),
                        **(document.metadata or {}),
                    },
                    "snippet": document.snippet or record.document.snippet,
                    "text": document.text if len(document.text) >= len(record.document.text) else record.document.text,
                    "phrases": list(dict.fromkeys([*(record.document.phrases or []), *(document.phrases or [])]))[:20],
                    "entities": list(dict.fromkeys([*(record.document.entities or []), *(document.entities or [])]))[:20],
                    "published_at": document.published_at or record.document.published_at,
                    "collected_at": document.collected_at or record.document.collected_at,
                }
            )
            updated = DiscoveryDocumentRecord(
                doc_id=record.doc_id,
                canonical_url=record.canonical_url,
                domain=record.domain,
                document=merged_document,
                providers=providers,
                search_queries=search_queries,
                first_seen_at=record.first_seen_at,
                latest_seen_at=datetime.fromisoformat(now),
                first_run_id=record.first_run_id,
                latest_run_id=run_id,
                seen_run_ids=seen_run_ids,
            )
            conn.execute(
                """
                UPDATE discovery_documents
                SET document_json = ?, providers_json = ?, search_queries_json = ?, latest_seen_at = ?,
                    latest_run_id = ?, seen_run_ids_json = ?
                WHERE doc_id = ?
                """,
                (
                    updated.document.model_dump_json(),
                    json.dumps(updated.providers),
                    json.dumps(updated.search_queries),
                    updated.latest_seen_at.isoformat(),
                    updated.latest_run_id,
                    json.dumps(updated.seen_run_ids),
                    updated.doc_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO discovery_document_runs (
                    run_id, doc_id, provider, search_query, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (run_id, doc_id, provider, search_query) DO NOTHING
                """,
                (run_id, document.id, provider, search_query, now),
            )
            return updated, False

    def list_discovery_documents(self) -> list[DiscoveryDocumentRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, canonical_url, domain, document_json, providers_json, search_queries_json,
                       first_seen_at, latest_seen_at, first_run_id, latest_run_id, seen_run_ids_json
                FROM discovery_documents
                ORDER BY latest_seen_at DESC
                """
            ).fetchall()
        return [
            DiscoveryDocumentRecord.model_validate(
                {
                    "doc_id": row["doc_id"],
                    "canonical_url": row["canonical_url"],
                    "domain": row["domain"],
                    "document": json.loads(row["document_json"]),
                    "providers": json.loads(row["providers_json"]),
                    "search_queries": json.loads(row["search_queries_json"]),
                    "first_seen_at": row["first_seen_at"],
                    "latest_seen_at": row["latest_seen_at"],
                    "first_run_id": row["first_run_id"],
                    "latest_run_id": row["latest_run_id"],
                    "seen_run_ids": json.loads(row["seen_run_ids_json"]),
                }
            )
            for row in rows
        ]

    def list_document_runs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, doc_id, provider, search_query, created_at
                FROM discovery_document_runs
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def save_snapshot(self, snapshot: PublishedTrendingSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO published_trending_snapshots (
                    snapshot_id, state, generated_at, fresh_until, last_completed_run_at,
                    last_reseed_at, warning, snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.state,
                    snapshot.generated_at.isoformat(),
                    snapshot.fresh_until.isoformat(),
                    snapshot.last_completed_run_at.isoformat() if snapshot.last_completed_run_at else None,
                    snapshot.last_reseed_at.isoformat() if snapshot.last_reseed_at else None,
                    snapshot.warning,
                    snapshot.model_dump_json(),
                ),
            )
            conn.execute(
                "DELETE FROM topic_clusters WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            )
            for topic in snapshot.topics:
                conn.execute(
                    """
                    INSERT INTO topic_clusters (snapshot_id, topic_id, canonical_phrase, topic_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.snapshot_id,
                        topic.id,
                        topic.canonical_phrase,
                        topic.model_dump_json(),
                        snapshot.generated_at.isoformat(),
                    ),
                )

    def get_latest_snapshot(self) -> PublishedTrendingSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_json
                FROM published_trending_snapshots
                ORDER BY generated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return PublishedTrendingSnapshot.model_validate_json(row["snapshot_json"])

    def list_topics_for_snapshot(self, snapshot_id: str) -> list[TrendingTopic]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT topic_json
                FROM topic_clusters
                WHERE snapshot_id = ?
                ORDER BY canonical_phrase
                """,
                (snapshot_id,),
            ).fetchall()
        return [TrendingTopic.model_validate_json(row["topic_json"]) for row in rows]

    def get_last_completed_run(self) -> DiscoveryRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, status, is_reseed, started_at, completed_at, stats_json,
                       warnings_json, error, queries_json
                FROM discovery_runs
                WHERE status = 'completed'
                ORDER BY completed_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return DiscoveryRunRecord.model_validate(
            {
                "run_id": row["run_id"],
                "status": row["status"],
                "is_reseed": bool(row["is_reseed"]),
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "stats": json.loads(row["stats_json"]),
                "warnings": json.loads(row["warnings_json"]),
                "error": row["error"],
                "queries": json.loads(row["queries_json"]),
            }
        )

    def get_last_reseed_run(self) -> DiscoveryRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, status, is_reseed, started_at, completed_at, stats_json,
                       warnings_json, error, queries_json
                FROM discovery_runs
                WHERE status = 'completed' AND is_reseed = 1
                ORDER BY completed_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return DiscoveryRunRecord.model_validate(
            {
                "run_id": row["run_id"],
                "status": row["status"],
                "is_reseed": bool(row["is_reseed"]),
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "stats": json.loads(row["stats_json"]),
                "warnings": json.loads(row["warnings_json"]),
                "error": row["error"],
                "queries": json.loads(row["queries_json"]),
            }
        )

    def _connect(self):
        return connect(self._db_path)

    def _ensure_parent_dir(self) -> None:
        ensure_parent_dir(self._db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS discovery_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    is_reseed INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    stats_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    error TEXT,
                    queries_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discovery_documents (
                    doc_id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    providers_json TEXT NOT NULL,
                    search_queries_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    latest_seen_at TEXT NOT NULL,
                    first_run_id TEXT NOT NULL,
                    latest_run_id TEXT NOT NULL,
                    seen_run_ids_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discovery_document_runs (
                    run_id TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    search_query TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, doc_id, provider, search_query)
                );

                CREATE TABLE IF NOT EXISTS published_trending_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    fresh_until TEXT NOT NULL,
                    last_completed_run_at TEXT,
                    last_reseed_at TEXT,
                    warning TEXT,
                    snapshot_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS topic_clusters (
                    snapshot_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    canonical_phrase TEXT NOT NULL,
                    topic_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, topic_id)
                );
                """
            )
