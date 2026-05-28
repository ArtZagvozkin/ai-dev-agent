from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


KnowledgeBaseSourceType = Literal[
    "local_json",
    "confluence",
    "kbpublisher",
    "manual",
]


class KnowledgeBaseCreateRequest(BaseModel):
    kb_key: str = Field(
        description="Stable knowledge base key, e.g. axel-docs.",
    )
    display_name: str
    source_type: KnowledgeBaseSourceType = "local_json"
    source_path: str | None = Field(
        default=None,
        description="Path to directory with <entry_id>/article.json files.",
    )
    default_top_k: int = Field(default=6, ge=1)
    default_max_context_chars: int = Field(default=10000, ge=1)

    def to_create_data(self) -> dict[str, Any]:
        return {
            "kb_key": self.kb_key,
            "display_name": self.display_name,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "default_top_k": self.default_top_k,
            "default_max_context_chars": self.default_max_context_chars,
        }


class KnowledgeBaseUpdateRequest(BaseModel):
    display_name: str | None = None
    source_type: KnowledgeBaseSourceType | None = None
    source_path: str | None = None
    default_top_k: int | None = Field(default=None, ge=1)
    default_max_context_chars: int | None = Field(default=None, ge=1)

    def to_update_data(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump(exclude_unset=True)

        return self.dict(exclude_unset=True)


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    kb_key: str
    display_name: str
    source_type: str
    source_path: str | None
    default_top_k: int
    default_max_context_chars: int
    current_index_id: UUID | None


class QdrantCollectionDeleteResult(BaseModel):
    collection_name: str
    deleted: bool
    error: str | None = None


class KnowledgeBaseDeleteResponse(BaseModel):
    kb_key: str
    deleted: bool
    indexes_count: int
    qdrant_collections: list[QdrantCollectionDeleteResult]
