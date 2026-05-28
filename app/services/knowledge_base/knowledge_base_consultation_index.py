from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from uuid import UUID

from fastapi import HTTPException, status

from app.components.knowledge_base.embeddings import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    OPENROUTER_BASE_URL,
    build_knowledge_base_embedding_client,
    normalize_knowledge_base_embedding_config,
)
from app.components.knowledge_base.models import KnowledgeBaseIndexStats
from app.components.knowledge_base.retriever import KnowledgeBaseSearchIndex
from app.components.knowledge_base.vector_store import (
    PersistentQdrantKnowledgeBaseVectorStore,
)
from app.core.config import Settings
from app.domain.knowledge_base_indexes import KnowledgeBaseIndex
from app.domain.knowledge_bases import KnowledgeBase
from app.repositories.knowledge_base_documents import KnowledgeBaseDocumentRepository
from app.repositories.knowledge_base_indexes import KnowledgeBaseIndexRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository


@dataclass(slots=True)
class KnowledgeBaseConsultationIndexBundle:
    knowledge_base: KnowledgeBase
    index_record: KnowledgeBaseIndex
    index: KnowledgeBaseSearchIndex
    top_k: int
    max_context_chars: int


class PersistentKnowledgeBaseIndexCache:
    def __init__(self):
        self._items: dict[UUID, KnowledgeBaseSearchIndex] = {}
        self._lock = Lock()

    def get(self, index_id: UUID) -> KnowledgeBaseSearchIndex | None:
        with self._lock:
            return self._items.get(index_id)

    def put(
        self,
        index_id: UUID,
        index: KnowledgeBaseSearchIndex,
    ) -> None:
        with self._lock:
            self._items[index_id] = index

    def forget(
        self,
        index_id: UUID,
    ) -> None:
        with self._lock:
            self._items.pop(index_id, None)


class PersistentKnowledgeBaseIndexLoader:
    def __init__(
        self,
        settings: Settings,
        knowledge_base_repository: KnowledgeBaseRepository,
        index_repository: KnowledgeBaseIndexRepository,
        document_repository: KnowledgeBaseDocumentRepository,
        cache: PersistentKnowledgeBaseIndexCache,
    ):
        self.settings = settings
        self.knowledge_base_repository = knowledge_base_repository
        self.index_repository = index_repository
        self.document_repository = document_repository
        self.cache = cache

    def load(
        self,
        kb_key: str,
    ) -> KnowledgeBaseConsultationIndexBundle:
        normalized_kb_key = str(kb_key or "").strip()

        if not normalized_kb_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="kb_key is required",
            )

        knowledge_base = self.knowledge_base_repository.get_by_key(normalized_kb_key)
        if knowledge_base is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Knowledge base not found: {normalized_kb_key}",
            )

        if knowledge_base.current_index_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Knowledge base has no current index: {normalized_kb_key}",
            )

        index_record = self.index_repository.get_by_id(
            knowledge_base.current_index_id,
        )
        if index_record is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Knowledge base points to missing index: "
                    f"kb_key={normalized_kb_key}, "
                    f"index_id={knowledge_base.current_index_id}"
                ),
            )

        if index_record.knowledge_base_id != knowledge_base.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Current knowledge base index belongs to another knowledge base: "
                    f"kb_key={normalized_kb_key}, index_id={index_record.id}"
                ),
            )

        if index_record.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Current knowledge base index is not ready: "
                    f"kb_key={normalized_kb_key}, index_id={index_record.id}, "
                    f"status={index_record.status}"
                ),
            )

        if index_record.chunks_count <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Current knowledge base index has no chunks: "
                    f"kb_key={normalized_kb_key}, index_id={index_record.id}"
                ),
            )

        search_index = self.cache.get(index_record.id)
        if search_index is None:
            search_index = self._load_search_index(
                knowledge_base=knowledge_base,
                index_record=index_record,
            )
            self.cache.put(index_record.id, search_index)

        return KnowledgeBaseConsultationIndexBundle(
            knowledge_base=knowledge_base,
            index_record=index_record,
            index=search_index,
            top_k=knowledge_base.default_top_k,
            max_context_chars=knowledge_base.default_max_context_chars,
        )

    def _load_search_index(
        self,
        knowledge_base: KnowledgeBase,
        index_record: KnowledgeBaseIndex,
    ) -> KnowledgeBaseSearchIndex:
        chunks = self.document_repository.list_chunks_by_index(index_record.id)

        if not chunks:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"No chunks found in Postgres for knowledge base index: {index_record.id}",
            )

        if len(chunks) != index_record.chunks_count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Postgres chunk count does not match knowledge base index metadata: "
                    f"index_id={index_record.id}, "
                    f"expected={index_record.chunks_count}, actual={len(chunks)}"
                ),
            )

        embedding_config = normalize_knowledge_base_embedding_config(
            provider=index_record.embedding_provider,
            base_url=OPENROUTER_BASE_URL,
            model=index_record.embedding_model,
            batch_size=DEFAULT_EMBEDDING_BATCH_SIZE,
            dimensions=self._query_embedding_dimensions(index_record),
        )

        embedding_client = build_knowledge_base_embedding_client(
            api_key=self.settings.openrouter_api_key,
            config=embedding_config,
        )

        chunks_by_db_id = {
            str(chunk.db_id): chunk
            for chunk in chunks
        }

        vector_store = PersistentQdrantKnowledgeBaseVectorStore(
            index_id=index_record.id,
            collection_name=index_record.qdrant_collection_name,
            chunks_by_db_id=chunks_by_db_id,
            url=self.settings.qdrant_url or None,
            api_key=self.settings.qdrant_api_key or None,
            location=self.settings.qdrant_local_path or None,
            prefer_grpc=self.settings.qdrant_prefer_grpc,
        )

        stats = KnowledgeBaseIndexStats(
            kb_key=knowledge_base.kb_key,
            index_id=index_record.id,
            documents_count=index_record.documents_count,
            chunks_count=index_record.chunks_count,
        )

        return KnowledgeBaseSearchIndex(
            chunks=chunks,
            stats=stats,
            embedding_client=embedding_client,
            vector_store=vector_store,
        )

    def _query_embedding_dimensions(
        self,
        index_record: KnowledgeBaseIndex,
    ) -> int | None:
        model = (index_record.embedding_model or "").strip().lower()

        # Для OpenAI text-embedding-3-* параметр dimensions допустим.
        # Для остальных моделей безопаснее не передавать dimensions повторно,
        # потому что часть embedding-моделей не поддерживает этот параметр.
        if model in {
            "openai/text-embedding-3-small",
            "openai/text-embedding-3-large",
            "text-embedding-3-small",
            "text-embedding-3-large",
        }:
            return index_record.embedding_dimensions

        return None
