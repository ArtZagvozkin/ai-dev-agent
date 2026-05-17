from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


CodebaseIndexStatus = Literal["building", "ready", "failed", "archived"]


class CodebaseIndexResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    project_key: str

    status: CodebaseIndexStatus

    index_schema_version: str

    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int

    qdrant_collection_name: str

    files_count: int
    chunks_count: int

    build_started_at: str | None = None
    build_finished_at: str | None = None
    error_message: str | None = None

    is_current: bool = False


class CodebaseIndexListResponse(BaseModel):
    items: list[CodebaseIndexResponse]


class CodebaseIndexDeleteResponse(BaseModel):
    deleted: bool
    index_id: UUID
