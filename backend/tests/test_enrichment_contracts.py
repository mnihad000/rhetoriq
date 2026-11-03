import pytest
from services.event_factory import raw_event_from_document
from tests.test_domain_events import _document


def test_tests_disable_developer_dotenv():
    from config import Settings
    assert Settings.model_config["env_file"] is None
    assert Settings().DATABASE_URL == ""


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
