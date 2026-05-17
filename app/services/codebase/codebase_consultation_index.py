from dataclasses import dataclass
from threading import Lock
from uuid import UUID

from fastapi import HTTPException, status

from app.components.code_search.embeddings import build_embedding_client_from_options
from app.components.code_search.indexer import CodebaseIndex as SearchCodebaseIndex
from app.components.code_search.models import CodeChunk, IndexStats
from app.components.code_search.persistent_vector_store import PersistentQdrantVectorStore
from app.core.config import Settings
from app.domain.code_projects import CodeProject
from app.domain.codebase_indexes import CodebaseIndex as CodebaseIndexRecord
from app.repositories.code_chunks import CodeChunkRepository
from app.repositories.code_projects import CodeProjectRepository
from app.repositories.codebase_indexes import CodebaseIndexRepository


@dataclass(slots=True)
class CodebaseConsultationIndexBundle:
    project: CodeProject
    index_record: CodebaseIndexRecord
    index: SearchCodebaseIndex
    context_path: str | None
    top_k: int
    include_full_code_units: bool


class PersistentCodebaseIndexCache:
    def __init__(self):
        self._items: dict[UUID, SearchCodebaseIndex] = {}
        self._lock = Lock()

    def get(self, index_id: UUID) -> SearchCodebaseIndex | None:
        with self._lock:
            return self._items.get(index_id)

    def put(self, index_id: UUID, index: SearchCodebaseIndex) -> None:
        with self._lock:
            self._items[index_id] = index

    def forget(self, index_id: UUID) -> None:
        with self._lock:
            self._items.pop(index_id, None)


class PersistentCodebaseIndexLoader:
    def __init__(
        self,
        settings: Settings,
        project_repository: CodeProjectRepository,
        index_repository: CodebaseIndexRepository,
        chunk_repository: CodeChunkRepository,
        cache: PersistentCodebaseIndexCache,
    ):
        self.settings = settings
        self.project_repository = project_repository
        self.index_repository = index_repository
        self.chunk_repository = chunk_repository
        self.cache = cache

    def load(self, project_id: str) -> CodebaseConsultationIndexBundle:
        """
        project_id в API сейчас соответствует code_projects.project_key.
        """
        project_key = project_id.strip()

        project = self.project_repository.get_by_key(project_key)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Code project not found: {project_key}",
            )

        if project.current_index_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code project has no current codebase index: {project_key}",
            )

        index_record = self.index_repository.get_by_id(project.current_index_id)
        if index_record is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Code project points to missing codebase index: "
                    f"project_key={project_key}, index_id={project.current_index_id}"
                ),
            )

        if index_record.project_id != project.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Current codebase index belongs to another project: "
                    f"project_key={project_key}, index_id={index_record.id}"
                ),
            )

        if index_record.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Current codebase index is not ready: "
                    f"project_key={project_key}, index_id={index_record.id}, "
                    f"status={index_record.status}"
                ),
            )

        if index_record.chunks_count <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Current codebase index has no chunks: "
                    f"project_key={project_key}, index_id={index_record.id}"
                ),
            )

        search_index = self.cache.get(index_record.id)
        if search_index is None:
            search_index = self._load_search_index(
                project=project,
                index_record=index_record,
            )
            self.cache.put(index_record.id, search_index)

        return CodebaseConsultationIndexBundle(
            project=project,
            index_record=index_record,
            index=search_index,
            context_path=project.consultation_context_path or self.settings.agent_context_path,
            top_k=project.default_top_k,
            include_full_code_units=project.default_include_full_code_units,
        )

    def _load_search_index(
        self,
        project: CodeProject,
        index_record: CodebaseIndexRecord,
    ) -> SearchCodebaseIndex:
        rows = self.chunk_repository.list_by_index(index_record.id)

        if not rows:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"No chunks found in Postgres for codebase index: {index_record.id}",
            )

        if len(rows) != index_record.chunks_count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Postgres chunk count does not match codebase index metadata: "
                    f"index_id={index_record.id}, "
                    f"expected={index_record.chunks_count}, actual={len(rows)}"
                ),
            )

        chunks_by_db_id: dict[str, CodeChunk] = {
            str(db_id): chunk
            for db_id, chunk in rows
        }
        chunks = [chunk for _, chunk in rows]

        embedding_client = build_embedding_client_from_options(
            provider=index_record.embedding_provider,
            model=index_record.embedding_model,
            api_key=self.settings.embedding_api_key,
            base_url=self.settings.embedding_base_url,
            dimensions=index_record.embedding_dimensions,
            batch_size=self.settings.embedding_batch_size,
            allow_hashing=False,
        )

        vector_store = PersistentQdrantVectorStore(
            index_id=index_record.id,
            collection_name=index_record.qdrant_collection_name,
            chunks_by_db_id=chunks_by_db_id,
            url=self.settings.qdrant_url or None,
            api_key=self.settings.qdrant_api_key or None,
            location=self.settings.qdrant_local_path or None,
            prefer_grpc=self.settings.qdrant_prefer_grpc,
        )

        stats = IndexStats(
            repository_path=project.local_repository_path,
            files_indexed=index_record.files_count,
            chunks_indexed=index_record.chunks_count,
        )

        return SearchCodebaseIndex(
            chunks=chunks,
            stats=stats,
            embedding_client=embedding_client,
            vector_store=vector_store,
            force_vector_reindex=False,
        )
