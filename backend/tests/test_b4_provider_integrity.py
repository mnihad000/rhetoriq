from types import SimpleNamespace

import pytest

from services.enrichment import (EnrichmentService, GeminiProvider, GroqProvider, RecordedProvider,
                                ReplayArtifactMissing, validate_artifact, validate_extraction)
from tests.test_b4_e2e_fixture import NOW, raw_fixture, recorded_service
from services.document_normalization import normalize_raw_event
from services.enrichment_artifacts import EnrichmentArtifactStore


@pytest.mark.parametrize("result", [
    {"refusal": "blocked"}, {"entities": [], "canonical_phrases": [], "refusal": "blocked"},
    {"entities": [], "canonical_phrases": ["Public records policy"]},
    {"entities": [], "canonical_phrases": [{"phrase": "policy", "surface_form": "policy"}]},
    {"entities": [], "canonical_phrases": [{"phrase": "policy", "surface_form": "policy", "start": True, "end": 21}]},
    {"entities": [], "canonical_phrases": [{"phrase": "policy", "surface_form": "policy", "start": 15.5, "end": 21}]},
    {"entities": [], "canonical_phrases": [{}] * 21},
])
def test_invalid_structured_extraction_fails_closed(result):
    with pytest.raises(ValueError):
        validate_extraction("Public records policy", result)


def test_unicode_offsets_are_character_indices():
    _, phrases = validate_extraction("é policy", {"entities": [], "canonical_phrases": [
        {"phrase": "policy", "surface_form": "policy", "start": 2, "end": 8}]})
    assert phrases[0].evidence.start == 2


def test_replay_missing_artifact_cannot_invoke_providers(tmp_path):
    class Forbidden:
        provider, model = "gemini", "gemini-2.5-flash"
        def extract(self, _text): raise AssertionError("hosted call")
        def embed(self, _text): raise AssertionError("hosted call")
    service = EnrichmentService(gemini=Forbidden(), replay=True,
                                artifact_store=EnrichmentArtifactStore(str(tmp_path / "empty.sqlite")))
    with pytest.raises(ReplayArtifactMissing):
        service.enrich(normalize_raw_event(raw_fixture(1, NOW)))


def test_embedding_failure_produces_no_successful_artifact(tmp_path):
    class BrokenEmbedding(RecordedProvider):
        def embed(self, _text): raise TimeoutError("embedding timeout")
    store = EnrichmentArtifactStore(str(tmp_path / "artifacts.sqlite"))
    request = normalize_raw_event(raw_fixture(1, NOW))
    service = EnrichmentService(gemini=BrokenEmbedding({"extraction": {"entities": [], "canonical_phrases": []}}), artifact_store=store)
    with pytest.raises(TimeoutError):
        service.enrich(request)
    assert not store.get_by_input_hash(request.payload.input_hash)


def test_tampered_recorded_receipt_and_mismatched_model_fail(tmp_path):
    service = recorded_service(tmp_path / "artifacts.sqlite")
    request = normalize_raw_event(raw_fixture(1, NOW))
    receipt = service.enrich(request).payload.enrichment
    with pytest.raises(ValueError):
        validate_artifact(receipt.model_copy(update={"output_hash": "tampered"}), request.payload.document.text)
    with pytest.raises(ValueError):
        validate_artifact(receipt.model_copy(update={"embedding_model": "MiniLM"}), request.payload.document.text)
    assert service.enrich(request).payload.document.embedding is None
    changed = EnrichmentService(gemini=RecordedProvider({}, model="another-model"), replay=True, artifact_store=service.artifact_store)
    with pytest.raises(ReplayArtifactMissing):
        changed.enrich(request)


def test_explicit_provider_price_ceiling_reserves_worst_case(monkeypatch, tmp_path):
    service = recorded_service(tmp_path / "artifacts.sqlite")
    request = normalize_raw_event(raw_fixture(1, NOW))
    for name in ("INPUT", "OUTPUT", "EMBEDDING"):
        monkeypatch.setenv(f"ENRICHMENT_{name}_PRICE_MICROS_PER_MILLION", "1000000")
    expected = 2 * (len(request.payload.document.text.encode()) + 8192 + 2048) + 2048
    assert service.estimated_cost_micros(request) == expected
    monkeypatch.delenv("ENRICHMENT_INPUT_PRICE_MICROS_PER_MILLION")
    with pytest.raises(ValueError):
        service.estimated_cost_micros(request)


def test_provider_adapters_bound_timeouts_and_disable_sdk_retries():
    calls = []
    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text='{"entities":[],"canonical_phrases":[]}', usage_metadata=None)
    GeminiProvider(client=SimpleNamespace(models=Models())).extract("text")
    assert 0 < calls[0]["config"]["http_options"]["timeout"] <= 20000
    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"entities":[],"canonical_phrases":[]}'))], usage=None)
    GroqProvider(client=SimpleNamespace(chat=SimpleNamespace(completions=Completions()))).extract("text")
    assert 0 < calls[1]["timeout"] <= 20
