from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class KnowledgeBaseIndex:
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
