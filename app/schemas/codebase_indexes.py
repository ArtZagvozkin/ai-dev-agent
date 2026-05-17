from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


CodebaseIndexStatus = Literal["building", "ready", "failed", "archived"]


class CodebaseIndexBuildRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    max_files: int | None = Field(default=None, ge=1, le=10000)
    max_file_bytes: int | None = Field(default=None, ge=1, le=5000000)

    embedding_provider: str | None = Field(default=None, min_length=1, max_length=64)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=255)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=20000)


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

    build_started_at: datetime | None = None
    build_finished_at: datetime | None = None
    error_message: str | None = None

    is_current: bool = False


class CodebaseIndexListResponse(BaseModel):
    items: list[CodebaseIndexResponse]


class CodebaseIndexDeleteResponse(BaseModel):
    deleted: bool
    index_id: UUID
