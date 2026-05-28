from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class KnowledgeBaseConsultationRequest(BaseModel):
    kb_key: str = Field(
        min_length=1,
        description="Knowledge base key, e.g. axel-docs.",
    )
    question: str = Field(
        min_length=1,
        description="User question.",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Number of chunks to retrieve. If omitted, knowledge base default_top_k is used.",
    )
    max_context_chars: int | None = Field(
        default=None,
        ge=1000,
        le=50000,
        description="Max total source text size passed to LLM.",
    )


class KnowledgeBaseSourceResponse(BaseModel):
    chunk_db_id: UUID
    document_id: UUID

    chunk_id: str
    chunk_ord: int

    title: str | None
    full_title: str | None
    source_url: str | None
    source_revision: str | None
    updated_at_src: Any

    section_path: list[str]
    block_types: list[str]

    text: str
    contextualized_text: str
    metadata: dict[str, Any]

    score: float

    bm25_score: float | None = None
    vector_score: float | None = None
    combined_score: float | None = None


class KnowledgeBaseConsultationResponse(BaseModel):
    answer: str
    limitations: str
    source_numbers: list[int]

    sources: list[KnowledgeBaseSourceResponse]
    retrieved_chunks: list[KnowledgeBaseSourceResponse]

    index_stats: dict[str, Any]
