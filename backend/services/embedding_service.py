"""
Embedding Service for RhetoriQ - Generate semantic embeddings for documents and queries.

Uses sentence-transformers (all-MiniLM-L6-v2) for fast, high-quality 384-dim embeddings.
This enables semantic similarity search, phrase mutation detection, and claim-to-evidence matching.

Redis Sponsor Track: This service powers semantic vector search beyond simple caching.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
import re
from typing import Any
import hashlib
import math

import numpy as np

from config import get_settings
from models.document import Document

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_MODEL_NAME_ALIASES = {
    # The legacy project setting used Sentence Transformers' short name.
    # Persist the canonical Hugging Face identifier so pgvector metadata and
    # query validation use one stable model identity.
    "all-MiniLM-L6-v2": DEFAULT_EMBEDDING_MODEL_NAME,
}


def canonical_embedding_model_name(model_name: str) -> str:
    """Return the stable model identifier used in persisted embeddings."""
    normalized = (model_name or "").strip()
    return _MODEL_NAME_ALIASES.get(normalized, normalized)


def normalize_embedding_text(text: str) -> str:
    """Normalize text before embedding/cache lookup."""
    return " ".join((text or "").split())


def safe_model_name(model_name: str) -> str:
    """Make a model name safe for Redis keys."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", model_name).strip("_") or "unknown_model"


def embedding_cache_key(model_name: str, text: str) -> str:
    normalized = normalize_embedding_text(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"embedding:{safe_model_name(model_name)}:{digest}"


class RedisEmbeddingCache:
    """
    Exact-text embedding cache.

    Stores generated vectors only. It does not store or hydrate model weights.
    """

    def __init__(
        self,
        redis_url: str | None,
        *,
        ttl_seconds: int = 86400,
        redis_client: Any | None = None,
    ) -> None:
        self.redis_url = redis_url or ""
        self.ttl_seconds = ttl_seconds
        self._redis = redis_client
        self._available: bool | None = None

    @property
    def enabled(self) -> bool:
        if not self.redis_url and self._redis is None:
            return False
        if self._available is False:
            return False
        try:
            self._client().ping()
            self._available = True
            return True
        except Exception as exc:
            logger.warning("Embedding cache unavailable: %s", exc)
            self._available = False
            return False

    def get(self, key: str) -> list[float] | None:
        if not self.enabled:
            return None
        try:
            raw = self._client().get(key)
            return _decode_cached_embedding(raw)
        except Exception as exc:
            logger.warning("Embedding cache get failed: %s", exc)
            self._available = False
            return None

    def mget(self, keys: list[str]) -> list[list[float] | None]:
        if not keys:
            return []
        if not self.enabled:
            return [None] * len(keys)
        try:
            values = self._client().mget(keys)
            return [_decode_cached_embedding(value) for value in values]
        except Exception as exc:
            logger.warning("Embedding cache mget failed: %s", exc)
            self._available = False
            return [None] * len(keys)

    def set(self, key: str, embedding: list[float]) -> None:
        if not self.enabled:
            return
        try:
            self._client().setex(key, self.ttl_seconds, json.dumps(embedding))
        except Exception as exc:
            logger.warning("Embedding cache set failed: %s", exc)
            self._available = False

    def mset(self, values: dict[str, list[float]]) -> None:
        if not values or not self.enabled:
            return
        try:
            client = self._client()
            pipe = client.pipeline()
            for key, embedding in values.items():
                pipe.setex(key, self.ttl_seconds, json.dumps(embedding))
            pipe.execute()
        except Exception as exc:
            logger.warning("Embedding cache mset failed: %s", exc)
            self._available = False

    def _client(self) -> Any:
        if self._redis is None:
            import redis as redis_lib

            self._redis = redis_lib.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3,
            )
        return self._redis


def _decode_cached_embedding(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        decoded = json.loads(value)
        if isinstance(decoded, list):
            return [float(item) for item in decoded]
    except Exception:
        return None
    return None


class EmbeddingService:
    """
    Generate embeddings for documents, queries, phrases, and claims.

    Uses sentence-transformers with all-MiniLM-L6-v2 model:
    - 384-dimensional vectors
    - Fast inference (~50ms for single text, <1s for batch of 32)
    - Good semantic understanding for short texts
    - Pre-trained on diverse corpus
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        model: Any | None = None,
        cache: Any | None = None,
        local_only: bool | None = None,
        dimension: int | None = None,
    ) -> None:
        """
        Initialize embedding model.

        Args:
            model_name: HuggingFace model name. Default: all-MiniLM-L6-v2
                       Alternatives: all-mpnet-base-v2 (768-dim, slower but better)
        """
        settings = get_settings()
        self.model_name = model_name or _resolve_model_name(settings)
        self.local_only = settings.EMBEDDING_LOCAL_ONLY if local_only is None else local_only
        self._model: Any = model
        self._dimension: int | None = dimension or getattr(settings, "EMBEDDING_DIMENSION", 384)
        self._cache = cache if cache is not None else RedisEmbeddingCache(
            getattr(settings, "REDIS_URL", ""),
            ttl_seconds=getattr(settings, "EMBEDDING_CACHE_TTL_SECONDS", 86400),
        )

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    @property
    def cache_enabled(self) -> bool:
        return bool(getattr(self._cache, "enabled", False))

    @property
    def model(self) -> Any:
        """Lazy load the sentence transformer model."""
        if self._model is None:
            self.load_model()
        return self._model

    def load_model(self) -> Any:
        """Intentionally load the real SentenceTransformer model."""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", self.model_name)
            kwargs = {"local_files_only": True} if self.local_only else {}
            if self.model_name == DEFAULT_EMBEDDING_MODEL_NAME:
                kwargs["revision"] = get_settings().B5_MODEL_REVISION
            self._model = SentenceTransformer(self.model_name, **kwargs)
            test_embedding = self._model.encode("test", convert_to_numpy=True)
            self._dimension = len(test_embedding)
            logger.info("Embedding model loaded. Dimension: %s", self._dimension)
            return self._model
        except ImportError:
            logger.error(
                "sentence-transformers not installed. "
                "Run: pip install sentence-transformers"
            )
            raise
        except Exception as exc:
            logger.error("Failed to load embedding model: %s", exc)
            raise

    @property
    def dimension(self) -> int:
        """Get embedding dimension (384 for all-MiniLM-L6-v2)."""
        return self._dimension or 384

    def cache_key_for_text(self, text: str) -> str:
        return embedding_cache_key(self.model_name, text)

    def embed_document(self, doc: Document) -> list[float]:
        """
        Generate embedding for a document.

        Combines title and snippet/text for better semantic representation.
        For RhetoriQ: Emphasizes title (where narrative phrases often appear)
        and opening text (where framing is established).

        Args:
            doc: Document to embed

        Returns:
            List of floats (384-dim for default model)
        """
        # Combine title + snippet for best semantic signal
        # Title: 60% weight (narrative phrases often in headlines)
        # Snippet: 40% weight (context and framing)
        text = f"{doc.title}. {doc.snippet or doc.text[:500]}"

        # Truncate to avoid exceeding model max length (512 tokens)
        if len(text) > 2000:
            text = text[:2000]

        return self.embed_text(text)

    def embed_query(self, query: str) -> list[float]:
        """
        Generate embedding for a search query or phrase.

        Args:
            query: Text to embed (search query, canonical phrase, claim, etc.)

        Returns:
            List of floats (384-dim)
        """
        return self.embed_text(query)

    def embed_text(self, text: str) -> list[float]:
        normalized = normalize_embedding_text(text)
        if not normalized:
            return self._zero_vector()

        key = self.cache_key_for_text(normalized)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        try:
            embedding = self._encode_one(normalized)
            self._cache_set(key, embedding)
            return embedding
        except Exception as exc:
            logger.warning("Failed to embed text: %s", exc)
            return self._zero_vector()

    def embed_texts(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Generate embeddings for texts while preserving original input order."""
        if not texts:
            return []

        normalized = [normalize_embedding_text(text) for text in texts]
        results: list[list[float] | None] = [
            self._zero_vector() if not text else None for text in normalized
        ]
        keyed_indexes = [
            (index, text, self.cache_key_for_text(text))
            for index, text in enumerate(normalized)
            if text
        ]
        cached_values = self._cache_mget([key for _index, _text, key in keyed_indexes])
        missing: list[tuple[int, str, str]] = []
        for (index, text, key), cached in zip(keyed_indexes, cached_values):
            if cached is None:
                missing.append((index, text, key))
            else:
                results[index] = cached

        if missing:
            try:
                encoded = self._encode_many([text for _index, text, _key in missing], batch_size)
                cache_writes: dict[str, list[float]] = {}
                for (index, _text, key), embedding in zip(missing, encoded):
                    results[index] = embedding
                    cache_writes[key] = embedding
                self._cache_mset(cache_writes)
            except Exception as exc:
                logger.error("Batch embedding failed: %s", exc)
                for index, text, key in missing:
                    embedding = self.embed_text(text)
                    results[index] = embedding
                    self._cache_set(key, embedding)

        return [embedding if embedding is not None else self._zero_vector() for embedding in results]

    def _encode_one(self, text: str) -> list[float]:
        embedding = self.model.encode(text, convert_to_numpy=True)
        return self._coerce_embedding(embedding)

    def _encode_many(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > 100,
        )
        return [self._coerce_embedding(embedding) for embedding in embeddings]

    def _coerce_embedding(self, embedding: Any) -> list[float]:
        try:
            if hasattr(embedding, "tolist"):
                values = embedding.tolist()
            else:
                values = list(embedding)
            coerced = [float(value) for value in values]
            self._dimension = len(coerced)
            return coerced
        except Exception as exc:
            logger.warning("Embedding coercion failed: %s", exc)
            return self._zero_vector()

    def _zero_vector(self) -> list[float]:
        return [0.0] * self.dimension

    def _cache_get(self, key: str) -> list[float] | None:
        try:
            return self._cache.get(key) if self._cache is not None else None
        except Exception as exc:
            logger.warning("Embedding cache read skipped: %s", exc)
            return None

    def _cache_mget(self, keys: list[str]) -> list[list[float] | None]:
        if not keys:
            return []
        try:
            if self._cache is not None and hasattr(self._cache, "mget"):
                return self._cache.mget(keys)
            return [self._cache_get(key) for key in keys]
        except Exception as exc:
            logger.warning("Embedding cache batch read skipped: %s", exc)
            return [None] * len(keys)

    def _cache_set(self, key: str, embedding: list[float]) -> None:
        try:
            if self._cache is not None:
                self._cache.set(key, embedding)
        except Exception as exc:
            logger.warning("Embedding cache write skipped: %s", exc)

    def _cache_mset(self, values: dict[str, list[float]]) -> None:
        if not values:
            return
        try:
            if self._cache is not None and hasattr(self._cache, "mset"):
                self._cache.mset(values)
            elif self._cache is not None:
                for key, embedding in values.items():
                    self._cache.set(key, embedding)
        except Exception as exc:
            logger.warning("Embedding cache batch write skipped: %s", exc)

    def embed_batch_documents(self, docs: list[Document], batch_size: int = 32) -> list[list[float]]:
        """
        Generate embeddings for multiple documents efficiently.

        Uses batch processing for ~10x speedup vs individual encoding.

        Args:
            docs: List of documents to embed
            batch_size: Number of documents to process at once

        Returns:
            List of embeddings (one per document)
        """
        if not docs:
            return []

        texts = [f"{doc.title}. {doc.snippet or doc.text[:500]}" for doc in docs]

        # Truncate long texts
        texts = [text[:2000] if len(text) > 2000 else text for text in texts]

        return self.embed_texts(texts, batch_size=batch_size)

    def embed_batch_queries(self, queries: list[str], batch_size: int = 32) -> list[list[float]]:
        """
        Generate embeddings for multiple queries efficiently.

        Args:
            queries: List of query strings
            batch_size: Batch size for processing

        Returns:
            List of embeddings
        """
        if not queries:
            return []

        return self.embed_texts(queries, batch_size=batch_size)

    def compute_similarity(self, embedding1: list[float], embedding2: list[float]) -> float:
        """
        Compute cosine similarity between two embeddings.

        Returns:
            Float in [0, 1] where 1.0 = identical, 0.0 = orthogonal
        """
        try:
            vec1 = np.array(embedding1, dtype=np.float32)
            vec2 = np.array(embedding2, dtype=np.float32)

            # Cosine similarity
            dot_product = np.dot(vec1, vec2)
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = dot_product / (norm1 * norm2)

            # Normalize to [0, 1] range
            return float((similarity + 1.0) / 2.0)
        except Exception as exc:
            logger.warning(f"Similarity computation failed: {exc}")
            return 0.0

    def find_similar_texts(
        self, query: str, candidate_texts: list[str], top_k: int = 5
    ) -> list[tuple[int, float]]:
        """
        Find most similar texts to query using embeddings.

        Useful for quick in-memory similarity without Redis.

        Args:
            query: Query text
            candidate_texts: List of texts to compare against
            top_k: Number of top results to return

        Returns:
            List of (index, similarity_score) tuples, sorted by similarity desc
        """
        if not candidate_texts:
            return []

        query_embedding = self.embed_query(query)
        candidate_embeddings = self.embed_batch_queries(candidate_texts)

        similarities = [
            (idx, self.compute_similarity(query_embedding, emb))
            for idx, emb in enumerate(candidate_embeddings)
        ]

        # Sort by similarity descending
        similarities.sort(key=lambda x: x[1], reverse=True)

        return similarities[:top_k]

    def _fallback_embed(self, text: str) -> list[float]:
        """
        Deterministic lightweight fallback embedding.

        Uses normalized token hashing with a small synonym map so tests and
        non-ML environments retain useful similarity behavior.
        """
        tokens = _normalize_tokens(text)
        vector = [0.0] * self.dimension
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimension
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            weight = 1.5 if token in {"climate", "policy", "energy", "environment"} else 1.0
            vector[index] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [float(value / norm) for value in vector]


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    """
    Get singleton embedding service instance.

    Cached to avoid loading model multiple times.
    """
    settings = get_settings()
    model_name = _resolve_model_name(settings)
    return EmbeddingService(model_name=model_name)


def _resolve_model_name(settings: Any) -> str:
    model_name = getattr(settings, "EMBEDDING_MODEL_NAME", "") or ""
    legacy_model = getattr(settings, "EMBEDDING_MODEL", "") or ""
    if model_name and model_name != DEFAULT_EMBEDDING_MODEL_NAME:
        return canonical_embedding_model_name(model_name)
    if legacy_model:
        return canonical_embedding_model_name(legacy_model)
    return canonical_embedding_model_name(model_name or DEFAULT_EMBEDDING_MODEL_NAME)
