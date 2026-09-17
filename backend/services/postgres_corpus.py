"""Neon/PostgreSQL semantic document corpus.

The corpus is deliberately additive. Existing investigation and discovery
tables remain the source of serialized artifacts; this table stores a
searchable copy keyed by the existing ``Document.id``. Vector literals are
cast by PostgreSQL instead of relying on a second ORM, which keeps the
psycopg3 adapter small and works with the pgvector extension shipped by Neon.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any

from models.document import Document
from services.database import is_postgres_database

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_DIMENSION = 384


class CorpusValidationError(ValueError):
    """Raised when an embedding cannot be stored in the configured corpus."""


@dataclass(frozen=True)
class CorpusSearchResult:
    document: Document
    score: float
    source_kind: str
    citable: bool
    embedding_input_hash: str | None = None
    embedding_model: str | None = None


@dataclass(frozen=True)
class BackfillResult:
    processed: int
    embedded: int
    unavailable: int
    next_after_id: str | None
    complete: bool
    unavailable_ids: tuple[str, ...] = ()


def document_embedding_text(document: Document) -> str:
    """Return the exact bounded text used by EmbeddingService for documents."""
    text = f"{document.title}. {document.snippet or document.text[:500]}"
    return " ".join(text[:2000].split())


def embedding_input_hash(document: Document) -> str:
    return hashlib.sha256(document_embedding_text(document).encode("utf-8")).hexdigest()


def validate_embedding(
    embedding: list[float] | tuple[float, ...] | None,
    *,
    model_name: str,
    expected_model: str,
    expected_dimension: int = DEFAULT_DIMENSION,
) -> list[float] | None:
    """Validate vector shape and model identity before a write or search."""
    if model_name != expected_model:
        raise CorpusValidationError(
            f"Embedding model mismatch: expected {expected_model!r}, got {model_name!r}."
        )
    if embedding is None:
        return None
    values = [float(value) for value in embedding]
    if len(values) != expected_dimension:
        raise CorpusValidationError(
            f"Embedding dimension mismatch: expected {expected_dimension}, got {len(values)}."
        )
    return values


def _vector_literal(values: list[float]) -> str:
    # pgvector accepts the textual form [0.1,0.2,...]. Avoid locale-sensitive
    # formatting and reject non-finite values before they reach SQL.
    import math

    if any(not math.isfinite(value) for value in values):
        raise CorpusValidationError("Embedding contains a non-finite value.")
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"


def _is_citable(document: Document, source_kind: str) -> bool:
    if source_kind == "discovery":
        return False
    return (document.metadata or {}).get("acquisition_receipt_valid") is True


def _provenance(document: Document, source_kind: str) -> dict[str, Any]:
    return {
        "source_kind": source_kind,
        "source_id": document.source_id,
        "source_name": document.source_name,
        "source_type": document.source_type,
        "url": document.url,
        "source_profile": document.source_profile.model_dump(mode="json") if document.source_profile else None,
        "metadata": document.metadata or {},
    }


def _embedding_service(service: Any | None = None) -> Any:
    if service is not None:
        return service
    from services.embedding_service import get_embedding_service

    return get_embedding_service()


class PostgresCorpusStore:
    """psycopg3 adapter for the additive semantic document corpus."""

    def __init__(
        self,
        database_url: str,
        *,
        embedding_service: Any | None = None,
        expected_dimension: int = DEFAULT_DIMENSION,
        expected_model: str = DEFAULT_MODEL,
    ) -> None:
        if not is_postgres_database(database_url):
            raise ValueError("PostgresCorpusStore requires a PostgreSQL database URL.")
        self.database_url = database_url
        self.embedding_service = embedding_service
        self.expected_dimension = expected_dimension
        self.expected_model = expected_model

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("PostgreSQL semantic retrieval requires psycopg.") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _resolve_model(self) -> str:
        service = _embedding_service(self.embedding_service)
        return str(getattr(service, "model_name", self.expected_model))

    def prepare_embedding(self, document: Document) -> tuple[list[float] | None, str, str]:
        service = _embedding_service(self.embedding_service)
        model_name = str(getattr(service, "model_name", self.expected_model))
        vector = document.embedding
        metadata = document.metadata or {}
        # A serialized Document does not otherwise identify how its vector
        # was produced. Recompute unless both provenance fields prove it is
        # the current model and the current bounded input.
        if vector is not None and (
            metadata.get("embedding_model") != model_name
            or metadata.get("embedding_input_hash") != embedding_input_hash(document)
        ):
            vector = None
        if vector is None:
            vector = service.embed_document(document)
        vector = validate_embedding(
            vector,
            model_name=model_name,
            expected_model=self.expected_model,
            expected_dimension=self.expected_dimension,
        )
        if vector is not None and not any(vector):
            return None, model_name, "unavailable"
        return vector, model_name, "ready" if vector is not None else "unavailable"

    def upsert_document(self, document: Document, *, source_kind: str, embed: bool = True) -> str:
        """Persist one document and best-effort embedding metadata.

        Returns ``ready``, ``pending``, or ``unavailable``. Validation errors
        are raised so callers can surface configuration mistakes; the
        repository-level ``sync_document`` keeps indexing best effort.
        """
        if embed:
            vector, model_name, status = self.prepare_embedding(document)
        else:
            vector, model_name, status = None, self.expected_model, "pending"
        citable = _is_citable(document, source_kind)
        provenance = json.dumps(_provenance(document, source_kind), default=str)
        vector_value = _vector_literal(vector) if vector is not None else None
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO semantic_document_corpus (
                        document_id, document_json, source_kind, source_provenance_json,
                        citable, embedding, embedding_model, embedding_input_hash,
                        embedding_status, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        document_json = CASE
                            WHEN semantic_document_corpus.citable AND NOT EXCLUDED.citable
                                THEN semantic_document_corpus.document_json
                            ELSE EXCLUDED.document_json END,
                        source_kind = CASE
                            WHEN semantic_document_corpus.citable AND NOT EXCLUDED.citable
                                THEN semantic_document_corpus.source_kind
                            ELSE EXCLUDED.source_kind END,
                        source_provenance_json = CASE
                            WHEN semantic_document_corpus.citable AND NOT EXCLUDED.citable
                                THEN semantic_document_corpus.source_provenance_json
                            ELSE EXCLUDED.source_provenance_json END,
                        citable = semantic_document_corpus.citable OR EXCLUDED.citable,
                        embedding = CASE
                            WHEN semantic_document_corpus.citable AND NOT EXCLUDED.citable
                                THEN semantic_document_corpus.embedding
                            WHEN semantic_document_corpus.embedding_status = 'ready'
                                 AND EXCLUDED.embedding_status IN ('unavailable', 'pending')
                                 AND semantic_document_corpus.embedding_model = EXCLUDED.embedding_model
                                 AND semantic_document_corpus.embedding_input_hash = EXCLUDED.embedding_input_hash
                                THEN semantic_document_corpus.embedding
                            ELSE EXCLUDED.embedding END,
                        embedding_model = CASE
                            WHEN semantic_document_corpus.citable AND NOT EXCLUDED.citable
                                THEN semantic_document_corpus.embedding_model
                            ELSE EXCLUDED.embedding_model END,
                        embedding_input_hash = CASE
                            WHEN semantic_document_corpus.citable AND NOT EXCLUDED.citable
                                THEN semantic_document_corpus.embedding_input_hash
                            ELSE EXCLUDED.embedding_input_hash END,
                        embedding_status = CASE
                            WHEN semantic_document_corpus.citable AND NOT EXCLUDED.citable
                                THEN semantic_document_corpus.embedding_status
                            WHEN semantic_document_corpus.embedding_status = 'ready'
                                 AND EXCLUDED.embedding_status IN ('unavailable', 'pending')
                                 AND semantic_document_corpus.embedding_model = EXCLUDED.embedding_model
                                 AND semantic_document_corpus.embedding_input_hash = EXCLUDED.embedding_input_hash
                                THEN semantic_document_corpus.embedding_status
                            ELSE EXCLUDED.embedding_status END,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        document.id,
                        document.model_dump_json(),
                        source_kind,
                        provenance,
                        citable,
                        vector_value,
                        model_name,
                        embedding_input_hash(document),
                        status,
                        now,
                    ),
                )
        return status

    def search(
        self,
        query_embedding: list[float],
        *,
        limit: int,
        model_name: str | None = None,
        document_ids: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[CorpusSearchResult]:
        model_name = model_name or self._resolve_model()
        vector = validate_embedding(
            query_embedding,
            model_name=model_name,
            expected_model=self.expected_model,
            expected_dimension=self.expected_dimension,
        )
        if vector is None or not any(vector):
            return []
        if document_ids is not None and not document_ids:
            return []
        conditions = ["embedding IS NOT NULL", "embedding_status = 'ready'", "embedding_model = %s"]
        parameters: list[Any] = [model_name]
        if document_ids is not None:
            conditions.append("document_id = ANY(%s)")
            parameters.append(document_ids)
        if filters is not None:
            if not filters.get("include_leads", False):
                conditions.append("citable = TRUE")
            for name in ("source_id", "source_type", "language"):
                if filters.get(name):
                    conditions.append(f"document_json::jsonb ->> '{name}' = %s")
                    parameters.append(filters[name])
            publication_filter = filters.get("published_after") or filters.get("published_before")
            if publication_filter and not filters.get("include_unknown_dates"):
                conditions.append("COALESCE(document_json::jsonb -> 'metadata' ->> 'event_time_quality', 'published_at') != 'collected_at_fallback'")
                conditions.append("document_json::jsonb ->> 'published_at' IS NOT NULL")
            for field in ("published", "collected"):
                for bound, operator in (("after", ">="), ("before", "<=")):
                    key = f"{field}_{bound}"
                    if filters.get(key):
                        expression = f"NULLIF(document_json::jsonb ->> '{field}_at', '')::timestamptz {operator} %s::timestamptz"
                        if field == "published" and filters.get("include_unknown_dates"):
                            expression = f"({expression} OR document_json::jsonb ->> 'published_at' IS NULL)"
                        conditions.append(expression)
                        parameters.append(filters[key])
        query = "SELECT document_json,source_kind,citable,embedding_input_hash,embedding_model,1-(embedding <=> %s::vector) AS similarity FROM semantic_document_corpus WHERE "
        query += " AND ".join(conditions) + " ORDER BY embedding <=> %s::vector,document_id LIMIT %s"
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    query,
                    [_vector_literal(vector), *parameters, _vector_literal(vector), max(0, int(limit))],
                )
                rows = cursor.fetchall()
        return [
            CorpusSearchResult(
                document=Document.model_validate_json(row["document_json"]),
                score=float(row["similarity"]),
                source_kind=str(row["source_kind"]),
                citable=bool(row["citable"]),
                embedding_input_hash=row.get("embedding_input_hash"),
                embedding_model=row.get("embedding_model"),
            )
            for row in rows
        ]

    def build_index(self) -> None:
        """Build the approximate cosine index after the initial backfill."""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_semantic_document_corpus_embedding_cosine
                    ON semantic_document_corpus USING hnsw (embedding vector_cosine_ops)
                    WHERE embedding IS NOT NULL
                    """
                )

    def backfill(
        self,
        *,
        batch_size: int,
        after_id: str | None = None,
        embedding_service: Any | None = None,
        retry_unavailable: bool = False,
    ) -> BackfillResult:
        """Backfill a bounded, ID-ordered batch; rerun with next_after_id."""
        source_query = """
            WITH candidates AS (
                SELECT doc_id AS document_id, document_json, 'research' AS source_kind
                FROM research_documents
                WHERE doc_id > %s
                UNION ALL
                SELECT doc_id AS document_id, document_json, 'retrieved' AS source_kind
                FROM retrieved_documents
                WHERE doc_id > %s
                UNION ALL
                SELECT doc_id AS document_id, document_json, 'discovery' AS source_kind
                FROM discovery_documents
                WHERE doc_id > %s
            )
            SELECT DISTINCT ON (document_id) document_id, document_json, source_kind
            FROM candidates
            ORDER BY document_id,
                CASE WHEN source_kind <> 'discovery'
                           AND (document_json::jsonb -> 'metadata' ->> 'acquisition_receipt_valid') = 'true'
                     THEN 1 ELSE 0 END DESC,
                CASE WHEN source_kind IN ('research', 'retrieved') THEN 1 ELSE 0 END DESC,
                source_kind
            LIMIT %s
        """
        retry_query = """
            SELECT document_id, document_json, source_kind
            FROM semantic_document_corpus
            WHERE embedding_status = 'unavailable' AND document_id > %s
            ORDER BY document_id
            LIMIT %s
        """
        key = after_id or ""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                if retry_unavailable:
                    cursor.execute(retry_query, (key, max(1, int(batch_size))))
                else:
                    cursor.execute(source_query, (key, key, key, max(1, int(batch_size))))
                rows = cursor.fetchall()

        grouped = {
            str(row["document_id"]): (str(row["source_kind"]), str(row["document_json"]))
            for row in rows
        }

        ids = sorted(grouped)
        if not ids:
            return BackfillResult(0, 0, 0, after_id, True)
        embedded = unavailable = 0
        unavailable_ids: list[str] = []
        if embedding_service is not None:
            self.embedding_service = embedding_service
        for document_id in ids:
            source_kind, document_json = grouped[document_id]
            try:
                status = self.upsert_document(
                    Document.model_validate_json(document_json),
                    source_kind=source_kind,
                )
                if status == "ready":
                    embedded += 1
                else:
                    unavailable += 1
            except Exception:
                # Backfill is resumable and must not lose the rest of a batch
                # because one malformed document or model call failed.
                unavailable += 1
                unavailable_ids.append(document_id)
                logger.exception("Semantic corpus backfill failed for %s", document_id)
        return BackfillResult(
            processed=len(ids),
            embedded=embedded,
            unavailable=unavailable,
            next_after_id=ids[-1],
            complete=len(grouped) < max(1, int(batch_size)),
            unavailable_ids=tuple(unavailable_ids),
        )


def sync_document(database_url: str, document: Document, *, source_kind: str) -> str | None:
    """Best-effort live indexing hook used by repositories."""
    if not is_postgres_database(database_url):
        return None
    try:
        from config import get_settings

        # Before rollout, persist the document without loading model weights on
        # the request path. The backfill fills pending vectors; enabled
        # deployments embed newly acquired documents as they arrive.
        return PostgresCorpusStore(database_url).upsert_document(
            document,
            source_kind=source_kind,
            embed=get_settings().ENABLE_POSTGRES_VECTOR_SEARCH,
        )
    except Exception:
        logger.exception("Semantic corpus indexing skipped for document %s", document.id)
        return "unavailable"


def _run_backfill(args: argparse.Namespace) -> int:
    from config import get_settings

    settings = get_settings()
    database_url = args.database_url or settings.DATABASE_URL
    if not database_url:
        raise SystemExit("A PostgreSQL URL is required (use --database-url or DATABASE_URL).")
    batch_size = args.batch_size if args.batch_size is not None else settings.POSTGRES_VECTOR_BACKFILL_BATCH_SIZE
    if batch_size < 1:
        raise SystemExit("Backfill batch size must be positive.")
    store = PostgresCorpusStore(database_url)
    after_id = args.after_id
    total_unavailable = 0
    while True:
        result = store.backfill(
            batch_size=batch_size,
            after_id=after_id,
            retry_unavailable=args.retry_unavailable,
        )
        print(json.dumps(result.__dict__, default=str))
        total_unavailable += result.unavailable
        if result.complete or result.next_after_id == after_id:
            return 1 if total_unavailable else 0
        after_id = result.next_after_id


def _build_index(args: argparse.Namespace) -> int:
    from config import get_settings

    database_url = args.database_url or get_settings().DATABASE_URL
    if not database_url:
        raise SystemExit("A PostgreSQL URL is required (use --database-url or DATABASE_URL).")
    PostgresCorpusStore(database_url).build_index()
    print(json.dumps({"index": "idx_semantic_document_corpus_embedding_cosine", "status": "ready"}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill the Neon semantic document corpus.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backfill = subparsers.add_parser("backfill")
    backfill.add_argument("--database-url")
    backfill.add_argument("--batch-size", type=int)
    backfill.add_argument("--after-id")
    backfill.add_argument("--retry-unavailable", action="store_true")
    backfill.set_defaults(func=_run_backfill)
    index = subparsers.add_parser("build-index")
    index.add_argument("--database-url")
    index.set_defaults(func=_build_index)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
