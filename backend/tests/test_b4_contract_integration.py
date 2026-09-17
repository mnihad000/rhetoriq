import json
from pathlib import Path
import struct

import httpx
import pytest

from models.events import PRIMARY_TOPICS, DLQ_TOPICS, event_schema
from services.event_factory import raw_event_from_document
from services.kafka_runtime import SchemaRegistry, SchemaRegistryError
from tests.test_b3_event_backbone import _document


def test_committed_contract_snapshots_match_models():
    schemas = Path(__file__).resolve().parents[1] / "events" / "schemas"
    for topic in (*PRIMARY_TOPICS, *DLQ_TOPICS):
        assert json.loads((schemas / f"{topic}.schema.json").read_text(encoding="utf-8")) == event_schema(topic)


@pytest.mark.parametrize("same_subject", [True, False])
def test_registry_accepts_previous_registered_writer_only_for_its_subject(same_subject):
    event = raw_event_from_document(_document(), producer="test", correlation_id="test")
    schema = event_schema("raw.documents.v1")

    def respond(request):
        if request.method == "PUT":
            return httpx.Response(200, json={"compatibility": "BACKWARD_TRANSITIVE"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": 42})
        if request.url.path.endswith("/versions"):
            return httpx.Response(200, json=[{
                "subject": "raw.documents.v1-value" if same_subject else "other-topic-value", "version": 1
            }])
        return httpx.Response(200, json={"schema": json.dumps(schema)})

    registry = SchemaRegistry("http://registry.test", transport=httpx.MockTransport(respond))
    framed = b"\x00" + struct.pack(">I", 41) + event.model_dump_json().encode()
    if same_subject:
        assert registry.decode("raw.documents.v1", framed)["event_id"] == event.event_id
    else:
        with pytest.raises(SchemaRegistryError, match="not registered"):
            registry.decode("raw.documents.v1", framed)


def test_production_worker_never_competes_with_flink_sources():
    from events.worker import TOPICS_BY_ROLE
    for topics in TOPICS_BY_ROLE.values():
        assert "raw.documents.v1" not in topics
        assert "documents.enriched.v1" not in topics


def test_tests_disable_developer_dotenv():
    from config import Settings
    assert Settings.model_config["env_file"] is None
    assert Settings().DATABASE_URL == ""


def test_dead_letter_redacts_bearer_value_with_space():
    from events.worker import _redact
    assert "private-credential" not in _redact("Authorization: Bearer private-credential")


def test_hosted_vector_model_identity_and_normalization():
    from models.events import EnrichmentReceipt
    from services.hosted_vectors import validate_hosted_vector
    receipt = EnrichmentReceipt(
        artifact_id="a", input_hash="i", output_hash="o", provider="gemini",
        model="gemini-2.5-flash", prompt_version="b4-v1", schema_version="enrichment.v1",
        embedding=[1.0] + [0.0] * 383,
    )
    assert len(validate_hosted_vector(receipt)) == 384
    with pytest.raises(ValueError, match="model identity"):
        validate_hosted_vector(receipt.model_copy(update={"embedding_model": "MiniLM"}))
    with pytest.raises(ValueError, match="normalized"):
        validate_hosted_vector(receipt.model_copy(update={"embedding": [2.0] + [0.0] * 383}))
