from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


SourceKind = Literal["rule", "user_context", "document", "web", "tool", "llm"]
KnowledgeKind = Literal["document", "web"]

EXCERPT_LENGTH = 500


class KnowledgeError(Exception):
    pass


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(KnowledgeModel):
    """Where a statement comes from. GuideOS assigns it; the model only cites
    ids that GuideOS handed out and resolves back to this record."""

    kind: SourceKind
    title: str
    # rule: recommendation_id; user_context: field_key; document/web: document_id;
    # tool: calculation_id of the tool result
    reference: str | None = None
    locator: str | None = None
    url: str | None = None
    content_hash: str | None = None
    retrieved_at: datetime | None = None
    excerpt: str | None = None


class KnowledgeChunk(KnowledgeModel):
    locator: str | None = None
    text: str
    embedding: list[float] | None = None


class KnowledgeDocument(KnowledgeModel):
    """One uploaded document or fetched web page of a domain's knowledge base."""

    document_id: str
    domain_id: str
    kind: KnowledgeKind
    title: str
    filename: str | None = None
    url: str | None = None
    content_hash: str
    embedding_model: str | None = None
    created_at: datetime
    chunks: list[KnowledgeChunk]
    # Web pages: the document_id of the newer fetch that replaced this one.
    # A superseded version is no longer searched but stays citable.
    superseded_by: str | None = None


class RetrievedPassage(KnowledgeModel):
    """A chunk selected for one request, with the id the model may cite."""

    source_id: str
    kind: KnowledgeKind
    document_id: str
    title: str
    locator: str | None = None
    url: str | None = None
    content_hash: str
    retrieved_at: datetime
    text: str
    score: float

    def to_source(self) -> SourceRef:
        excerpt = self.text
        if len(excerpt) > EXCERPT_LENGTH:
            excerpt = excerpt[:EXCERPT_LENGTH].rstrip() + " …"
        return SourceRef(
            kind=self.kind,
            title=self.title,
            reference=self.document_id,
            locator=self.locator,
            url=self.url,
            content_hash=self.content_hash,
            retrieved_at=self.retrieved_at,
            excerpt=excerpt,
        )
