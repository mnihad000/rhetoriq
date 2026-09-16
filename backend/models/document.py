from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel


SourceInstitutionKind = Literal["official", "media", "advocacy", "independent", "community", "unknown"]
SourceContentForm = Literal[
    "original_reporting", "reposting", "opinion", "transcript", "community_post", "unknown"
]
SourceIdeology = Literal["left", "center", "right", "unknown"]
SourceClassificationMethod = Literal["registry", "heuristic", "unknown"]
SourceClassificationConfidence = Literal["low", "medium", "high"]


class SourceProfile(BaseModel):
    institution_kind: SourceInstitutionKind = "unknown"
    content_form: SourceContentForm = "unknown"
    ideology: SourceIdeology = "unknown"
    classification_method: SourceClassificationMethod = "unknown"
    classification_confidence: SourceClassificationConfidence = "low"


class Document(BaseModel):
    id: str
    source_id: str | None = None
    source_name: str
    source_type: Literal[
        "forum", "blog", "local_news", "national_news", "commentary", "speech_transcript",
        "government_record",
    ]
    url: str
    title: str
    author: str | None = None
    published_at: datetime | None = None
    collected_at: datetime | None = None
    text: str
    snippet: str | None = None
    language: str | None = None
    content_type: str | None = None
    geographic_scope: Literal["local", "state", "national", "international", "unknown"] | None = None
    entities: list[str]
    phrases: list[str]
    claims: list[str] | None = None
    embedding: list[float] | None = None
    duplicate_of_doc_id: str | None = None
    is_seeded_demo_data: bool | None = None
    metadata: dict[str, Any] | None = None
    source_profile: SourceProfile | None = None
