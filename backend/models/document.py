from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


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


class SourceReference(BaseModel):
    target_url: str
    anchor_text: str = ""
    context: str = ""
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    extraction_version: str = "b5-links-v1"
    reference_kind: Literal["hyperlink", "structured"] = "hyperlink"
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("Reference offsets must both be present or absent")
        if self.start is not None and self.end < self.start:
            raise ValueError("Reference span is reversed")
        return self


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
    references: list[SourceReference] = Field(default_factory=list, max_length=100)
