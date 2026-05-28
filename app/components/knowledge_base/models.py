from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.knowledge_base_chunks import KnowledgeBaseChunk


@dataclass(slots=True)
class KnowledgeBaseVectorSearchHit:
    chunk_db_id: UUID
    vector_score: float


@dataclass(slots=True)
class RetrievedKnowledgeBaseChunk:
    chunk: KnowledgeBaseChunk

    score: float
    bm25_score: float
    vector_score: float
    combined_score: float

    def cache_key(self) -> UUID:
        return self.chunk.db_id


@dataclass(slots=True)
class KnowledgeBaseIndexStats:
    kb_key: str
    index_id: UUID
    documents_count: int
    chunks_count: int

    def to_payload(self) -> dict:
        return {
            "kb_key": self.kb_key,
            "index_id": self.index_id,
            "documents_count": self.documents_count,
            "chunks_count": self.chunks_count,
        }
