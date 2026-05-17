from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class CodebaseIndex:
    id: UUID
    project_id: UUID
    project_key: str

    status: str

    index_schema_version: str

    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int

    qdrant_collection_name: str

    files_count: int
    chunks_count: int

    build_started_at: datetime | None
    build_finished_at: datetime | None
    error_message: str | None

    is_current: bool
