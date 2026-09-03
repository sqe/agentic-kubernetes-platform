"""Validated knowledge-agent API and extraction models."""

from typing import Any

from pydantic import BaseModel, Field, model_validator


class DocumentIngest(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    text: str | None = Field(default=None, max_length=200_000)
    source_uri: str | None = Field(default=None, max_length=2_000)
    ontology: str = Field(default="core", min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def has_source(self) -> "DocumentIngest":
        if bool(self.text) == bool(self.source_uri):
            raise ValueError("provide exactly one of text or source_uri")
        if self.source_uri and not self.source_uri.startswith(("s3://", "https://")):
            raise ValueError("source_uri must use s3:// or https://")
        return self


class Entity(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    type: str = Field(default="concept", max_length=100)
    description: str = Field(default="", max_length=2_000)
    aliases: list[str] = Field(default_factory=list, max_length=50)


class Relationship(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=500)
    type: str = Field(default="related_to", max_length=100)
    evidence: str = Field(default="", max_length=2_000)


class ExtractedGraph(BaseModel):
    entities: list[Entity] = Field(default_factory=list, max_length=500)
    relationships: list[Relationship] = Field(default_factory=list, max_length=1_000)


class McpCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
