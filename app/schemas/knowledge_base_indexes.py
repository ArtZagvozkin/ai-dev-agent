from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.components.knowledge_base.embeddings import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    OPENROUTER_BASE_URL,
)
from app.services.knowledge_base.knowledge_base_index_build import (
    KnowledgeBaseIndexBuildRequest as ServiceKnowledgeBaseIndexBuildRequest,
)


class KnowledgeBaseIndexBuildRequest(BaseModel):
    source_path: str | None = Field(
        default=None,
        description="Path to directory with <entry_id>/article.json files.",
    )

    embedding_provider: Literal["openrouter"] = Field(
        description="Embedding provider. Knowledge base indexing currently supports only OpenRouter.",
    )
    embedding_base_url: str = Field(
        default=OPENROUTER_BASE_URL,
        description="OpenRouter OpenAI-compatible API base URL.",
    )
    embedding_model: str = Field(
        min_length=1,
        description="OpenRouter embedding model, e.g. openai/text-embedding-3-small.",
    )
    embedding_batch_size: int = Field(
        default=DEFAULT_EMBEDDING_BATCH_SIZE,
        ge=1,
        le=256,
        description="Embedding request batch size.",
    )
    embedding_dimensions: int | None = Field(default=None, ge=1)

    max_documents: int | None = Field(default=None, ge=1)

    create_if_missing: bool = False
    display_name: str | None = None

    def to_service_request(self) -> ServiceKnowledgeBaseIndexBuildRequest:
        return ServiceKnowledgeBaseIndexBuildRequest(
            source_path=self.source_path,
            embedding_provider=self.embedding_provider,
            embedding_base_url=self.embedding_base_url,
            embedding_model=self.embedding_model,
            embedding_batch_size=self.embedding_batch_size,
            embedding_dimensions=self.embedding_dimensions,
            max_documents=self.max_documents,
            create_if_missing=self.create_if_missing,
            display_name=self.display_name,
        )


class KnowledgeBaseIndexResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    kb_key: str

    status: str
    index_schema_version: str

    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int

    qdrant_collection_name: str

    documents_count: int
    chunks_count: int

    build_started_at: datetime | None
    build_finished_at: datetime | None
    error_message: str | None

    is_current: bool
