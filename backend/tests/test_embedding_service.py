"""
Unit tests for EmbeddingService.
"""

import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

import numpy as np

from models.document import Document
from services.embedding_service import (
    DEFAULT_EMBEDDING_MODEL_NAME,
    EmbeddingService,
    canonical_embedding_model_name,
    embedding_cache_key,
    get_embedding_service,
    normalize_embedding_text,
)


class FakeEmbeddingModel:
    """Small deterministic stand-in for SentenceTransformer."""

    def __init__(self) -> None:
        self.encode_calls = 0
        self.encoded_inputs: list[str | list[str]] = []

    def encode(self, texts, **kwargs):
        self.encode_calls += 1
        self.encoded_inputs.append(texts)
        if isinstance(texts, str):
            return self._encode_one(texts)
        return np.array([self._encode_one(text) for text in texts], dtype=np.float32)

    def _encode_one(self, text: str) -> np.ndarray:
        lower = text.lower()
        vector = np.zeros(384, dtype=np.float32)
        if any(term in lower for term in ["climate", "warming", "environment"]):
            vector[0] = 1.0
        if any(term in lower for term in ["policy", "regulation", "government"]):
            vector[1] = 0.8
        if any(term in lower for term in ["cook", "pasta", "recipe", "dinner"]):
            vector[10] = 1.0
        if not vector.any():
            vector[-1] = 1.0
        return vector


class FakeEmbeddingCache:
    def __init__(self) -> None:
        self.enabled = True
        self.values: dict[str, list[float]] = {}
        self.get_calls: list[str] = []
        self.mget_calls: list[list[str]] = []
        self.set_calls: list[str] = []
        self.mset_calls: list[dict[str, list[float]]] = []

    def get(self, key: str) -> list[float] | None:
        self.get_calls.append(key)
        return self.values.get(key)

    def mget(self, keys: list[str]) -> list[list[float] | None]:
        self.mget_calls.append(keys)
        return [self.values.get(key) for key in keys]

    def set(self, key: str, embedding: list[float]) -> None:
        self.set_calls.append(key)
        self.values[key] = embedding

    def mset(self, values: dict[str, list[float]]) -> None:
        self.mset_calls.append(values)
        self.values.update(values)


@pytest.fixture
def embedding_service():
    return EmbeddingService(model=FakeEmbeddingModel(), cache=FakeEmbeddingCache(), dimension=384)


def test_embedding_service_initialization(embedding_service):
    """Test embedding service can be initialized."""
    assert embedding_service.model_name in {"all-MiniLM-L6-v2", DEFAULT_EMBEDDING_MODEL_NAME}
    assert embedding_service.dimension == 384
    assert embedding_service.model_loaded is True


def test_legacy_minilm_alias_uses_the_canonical_persisted_model_name():
    assert canonical_embedding_model_name("all-MiniLM-L6-v2") == DEFAULT_EMBEDDING_MODEL_NAME


def test_embed_query(embedding_service):
    """Test query embedding generation."""
    embedding = embedding_service.embed_query("climate change policy")

    assert isinstance(embedding, list)
    assert len(embedding) == 384
    assert all(isinstance(x, float) for x in embedding)


def test_embed_document(embedding_service):
    """Test document embedding generation."""
    doc = Document(
        id="test_001",
        source_id="src_001",
        source_name="Test Source",
        source_type="blog",
        url="https://example.com/article",
        title="Climate Change Policy Debate",
        text="This is a longer article about climate policy and its economic impacts.",
        snippet="Climate policy debate continues...",
        entities=["climate", "policy"],
        phrases=["climate change"],
        published_at=datetime.now(),
    )

    embedding = embedding_service.embed_document(doc)

    assert isinstance(embedding, list)
    assert len(embedding) == 384
    assert all(isinstance(x, float) for x in embedding)


def test_embed_batch_queries(embedding_service):
    """Test batch query embedding."""
    queries = [
        "energy policy",
        "climate change",
        "",
        "government regulation",
    ]

    embeddings = embedding_service.embed_batch_queries(queries)

    assert len(embeddings) == 4
    assert all(len(emb) == 384 for emb in embeddings)
    assert embeddings[2] == [0.0] * 384


def test_compute_similarity(embedding_service):
    """Test cosine similarity computation."""
    emb1 = embedding_service.embed_query("climate change")
    emb2 = embedding_service.embed_query("global warming")
    emb3 = embedding_service.embed_query("cooking recipes")

    # Similar queries should have high similarity
    similarity_12 = embedding_service.compute_similarity(emb1, emb2)
    assert similarity_12 > 0.7

    # Unrelated queries should have low similarity
    similarity_13 = embedding_service.compute_similarity(emb1, emb3)
    assert similarity_13 < 0.6


def test_find_similar_texts(embedding_service):
    """Test finding similar texts."""
    candidates = [
        "Climate change requires urgent action",
        "Global warming is accelerating",
        "Best pasta recipes for dinner",
        "Environmental policy debate",
    ]

    results = embedding_service.find_similar_texts("climate policy", candidates, top_k=2)

    assert len(results) == 2
    # First result should be climate-related
    assert results[0][1] > 0.6  # Similarity score


def test_singleton_instance():
    """Test get_embedding_service returns singleton."""
    get_embedding_service.cache_clear()
    service1 = get_embedding_service()
    service2 = get_embedding_service()

    assert service1 is service2


def test_constructor_does_not_instantiate_sentence_transformer(monkeypatch):
    """Normal unit construction must not load or download a Hugging Face model."""
    class _ExplodingSentenceTransformer:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SentenceTransformer should not be constructed in unit tests")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=_ExplodingSentenceTransformer),
    )

    service = EmbeddingService(cache=FakeEmbeddingCache(), dimension=384)

    assert service.model_loaded is False
    assert service.dimension == 384


def test_cache_miss_encodes_and_writes():
    model = FakeEmbeddingModel()
    cache = FakeEmbeddingCache()
    service = EmbeddingService(model=model, cache=cache, dimension=384)

    embedding = service.embed_query("climate change")

    assert len(embedding) == 384
    assert model.encode_calls == 1
    assert cache.set_calls == [service.cache_key_for_text("climate change")]


def test_cache_hit_avoids_model_call():
    model = FakeEmbeddingModel()
    cache = FakeEmbeddingCache()
    service = EmbeddingService(model=model, cache=cache, dimension=384)
    key = service.cache_key_for_text("climate change")
    cache.values[key] = [0.25] * 384

    embedding = service.embed_query("climate change")

    assert embedding == [0.25] * 384
    assert model.encode_calls == 0


def test_normalized_text_uses_same_cache_key():
    assert normalize_embedding_text("  climate   change\npolicy  ") == "climate change policy"
    assert embedding_cache_key("model/name", "climate change policy") == embedding_cache_key(
        "model/name",
        "  climate   change\npolicy  ",
    )


def test_embed_texts_preserves_original_order_with_cache_hits():
    model = FakeEmbeddingModel()
    cache = FakeEmbeddingCache()
    service = EmbeddingService(model=model, cache=cache, dimension=384)
    cached_key = service.cache_key_for_text("global warming")
    cache.values[cached_key] = [0.75] * 384

    embeddings = service.embed_texts(["climate policy", "global warming", "", "pasta dinner"])

    assert len(embeddings) == 4
    assert embeddings[1] == [0.75] * 384
    assert embeddings[2] == [0.0] * 384
    assert embeddings[0][0] == 1.0
    assert embeddings[3][10] == 1.0


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_REAL_EMBEDDING_TESTS") != "1",
    reason="Set RUN_REAL_EMBEDDING_TESTS=1 to load the real SentenceTransformer model.",
)
def test_real_sentence_transformer_can_encode_text():
    service = EmbeddingService(
        model_name=os.getenv("EMBEDDING_MODEL_NAME", DEFAULT_EMBEDDING_MODEL_NAME),
        cache=FakeEmbeddingCache(),
    )

    embedding = service.embed_query("climate policy")

    assert len(embedding) > 0
    assert any(value != 0.0 for value in embedding)
