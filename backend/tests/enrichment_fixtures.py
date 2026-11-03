"""Recorded provider fixtures without a streaming runtime."""
from datetime import datetime, timezone
from models.document import Document
from services.enrichment import EnrichmentService, RecordedProvider
from services.enrichment_artifacts import EnrichmentArtifactStore
from services.event_factory import raw_event_from_document

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def raw_fixture(index, published_at, *, duplicate_text=None):
    domain = f"publisher{index % 4}.example"
    text = duplicate_text or f"Public records policy changes today. Record {index}."
    document = Document(
        id=f"doc_b4_fixture_{index}", source_name=domain,
        source_type="government_record" if index % 2 else "national_news",
        url=f"https://{domain}/records/{index}", title=f"Public records policy {index}",
        text=text, snippet=text, collected_at=NOW, published_at=published_at,
        language="en", content_type="article", entities=[], phrases=[],
        metadata={"provider": "federal_register" if index % 2 else "searxng",
                  "source_policy": {"decision": "allow", "citable": True},
                  "evidence_status": "canonical_evidence"},
    )
    event = raw_event_from_document(document, producer="fixture", correlation_id="b4-fixture")
    return event.model_copy(update={"event_id": f"raw_b4_fixture_{index}"})


def recorded_service(target, *, replay=False):
    fixture = {
        "extraction": {
            "entities": [],
            "canonical_phrases": [{"phrase": "public records policy", "surface_form": "Public records policy",
                                   "start": 0, "end": len("Public records policy")}],
        },
        "embedding": [1.0] + [0.0] * 383,
    }
    provider = RecordedProvider(fixture)
    if replay:
        def forbid_inference(_text):
            raise AssertionError("Replay must never invoke a hosted provider")
        provider.extract = forbid_inference
        provider.embed = forbid_inference
    return EnrichmentService(gemini=provider, replay=replay,
                             artifact_store=EnrichmentArtifactStore(str(target)))
