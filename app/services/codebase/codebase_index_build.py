import logging
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.components.code_search.embeddings import (
    EmbeddingClient,
    build_embedding_client_from_options,
)
from app.components.code_search.indexer import CodebaseIndexer
from app.components.code_search.retriever import INDEX_SCHEMA_VERSION
from app.components.code_search.vector_writer import QdrantCodeChunkVectorWriter
from app.core.config import Settings
from app.domain.code_projects import CodeProject
from app.repositories.code_chunks import CodeChunkRepository
from app.repositories.code_projects import CodeProjectRepository
from app.repositories.codebase_files import CodebaseFileRepository
from app.repositories.codebase_indexes import CodebaseIndexRepository
from app.schemas.codebase_indexes import CodebaseIndexBuildRequest


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BuildEmbeddingConfig:
    provider: str
    model: str
    dimensions: int | None


@dataclass(slots=True)
class BuildProjectOptions:
    project: CodeProject
    max_files: int
    max_file_bytes: int
    initial_current_index_id: UUID | None


class CodebaseIndexBuildService:
    def __init__(
        self,
        settings: Settings,
        session: Session,
        project_repository: CodeProjectRepository,
        index_repository: CodebaseIndexRepository,
        file_repository: CodebaseFileRepository,
        chunk_repository: CodeChunkRepository,
        indexer: CodebaseIndexer,
    ):
        self.settings = settings
        self.session = session
        self.project_repository = project_repository
        self.index_repository = index_repository
        self.file_repository = file_repository
        self.chunk_repository = chunk_repository
        self.indexer = indexer

    def build_first_index(self, project_key: str, data: CodebaseIndexBuildRequest):
        return self._build_persistent_index(
            project_key=project_key,
            data=data,
            require_current_index=False,
            attach_mode="if_empty",
        )

    def rebuild_index(self, project_key: str, data: CodebaseIndexBuildRequest):
        return self._build_persistent_index(
            project_key=project_key,
            data=data,
            require_current_index=True,
            attach_mode="overwrite_if_current_unchanged",
        )

    def _build_persistent_index(
        self,
        project_key: str,
        data: CodebaseIndexBuildRequest,
        require_current_index: bool,
        attach_mode: str,
    ):
        """
        Builds a persistent codebase index.

        Safety rule:
        - current_index_id is changed only after:
          1. chunks are persisted in Postgres;
          2. vectors are persisted in Qdrant;
          3. codebase_indexes.status is switched to 'ready'.

        Rebuild safety rule:
        - rebuild can replace current_index_id only if current_index_id was not
          changed while the long build was running.
        """
        self._ensure_qdrant_provider()

        self._precheck_project_for_build(
            project_key=project_key,
            data=data,
            require_current_index=require_current_index,
        )

        embedding_config = self._resolve_embedding_config(data)
        embedding_client = self._build_embedding_client(embedding_config)

        embedding_dimensions = self._preflight_embedding_provider(
            embedding_client=embedding_client,
            configured_dimensions=embedding_config.dimensions,
        )

        index = None

        try:
            options = self._lock_project_for_build(
                project_key=project_key,
                data=data,
                require_current_index=require_current_index,
            )
            project = options.project

            index_id = uuid4()
            qdrant_collection_name = self._build_collection_name(
                project_key=project.project_key,
                index_id=index_id,
            )

            index = self.index_repository.create_building(
                index_id=index_id,
                project_id=project.id,
                index_schema_version=INDEX_SCHEMA_VERSION,
                embedding_provider=embedding_config.provider,
                embedding_model=embedding_config.model,
                embedding_dimensions=embedding_dimensions,
                qdrant_collection_name=qdrant_collection_name,
            )

            self.session.commit()
        except HTTPException:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

        files_count = 0
        chunks_count = 0

        try:
            vector_writer = self._build_vector_writer(index.qdrant_collection_name)
            vector_writer.recreate_collection(vector_size=embedding_dimensions)

            for indexed_file in self.indexer.iter_files(
                repository_path=project.local_repository_path,
                max_files=options.max_files,
                max_file_bytes=options.max_file_bytes,
            ):
                file_id = self.file_repository.create(
                    index_id=index.id,
                    path=indexed_file.path,
                    language=indexed_file.language,
                    content_hash=indexed_file.content_hash,
                    size_bytes=indexed_file.size_bytes,
                    chunks_count=len(indexed_file.chunks),
                )

                db_chunk_ids = self.chunk_repository.create_many(
                    file_id=file_id,
                    chunks=indexed_file.chunks,
                )

                self.session.commit()

                searchable_texts = [
                    chunk.contextualized_text
                    for chunk in indexed_file.chunks
                ]

                vectors = embedding_client.embed_texts(searchable_texts)
                vector_size = self._validate_vectors(vectors)

                if vector_size != embedding_dimensions:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=(
                            "Embedding dimension mismatch: "
                            f"expected={embedding_dimensions}, actual={vector_size}"
                        ),
                    )

                if len(db_chunk_ids) != len(vectors):
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=(
                            "Stored chunk/vector count mismatch: "
                            f"chunks={len(db_chunk_ids)}, vectors={len(vectors)}"
                        ),
                    )

                vector_writer.upsert_vectors(
                    index_id=index.id,
                    chunk_ids=db_chunk_ids,
                    vectors=vectors,
                )

                files_count += 1
                chunks_count += len(indexed_file.chunks)

                self.index_repository.update_counts(
                    index_id=index.id,
                    files_count=files_count,
                    chunks_count=chunks_count,
                )
                self.session.commit()

                if files_count % 25 == 0:
                    logger.info(
                        "Persistent codebase index build progress: "
                        "project_key=%s, index_id=%s, files_count=%s, chunks_count=%s",
                        project_key,
                        index.id,
                        files_count,
                        chunks_count,
                    )

            if chunks_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        "No searchable code chunks found under: "
                        f"{project.local_repository_path}"
                    ),
                )

            ready_index = self.index_repository.mark_ready(
                index_id=index.id,
                files_count=files_count,
                chunks_count=chunks_count,
            )

            if attach_mode == "if_empty":
                current_set = self.project_repository.set_current_index_if_empty(
                    project_id=project.id,
                    index_id=index.id,
                )
            elif attach_mode == "overwrite_if_current_unchanged":
                if options.initial_current_index_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Code project has no current index at rebuild attach stage: "
                            f"{project_key}"
                        ),
                    )

                current_set = self.project_repository.set_current_index_if_matches(
                    project_id=project.id,
                    expected_current_index_id=options.initial_current_index_id,
                    new_index_id=index.id,
                )
            else:
                raise RuntimeError(f"Unsupported index attach mode: {attach_mode}")

            if not current_set:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Code project current index was changed while build was running: "
                        f"{project_key}"
                    ),
                )

            self.session.commit()

            refreshed_index = self.index_repository.get_by_id(index.id)
            return refreshed_index or ready_index

        except Exception as exc:
            self.session.rollback()
            self._mark_failed_best_effort(index_id=index.id if index else None, error=exc)

            if index is not None:
                self._delete_qdrant_collection_best_effort(
                    collection_name=index.qdrant_collection_name,
                )

            raise

    def _precheck_project_for_build(
        self,
        project_key: str,
        data: CodebaseIndexBuildRequest,
        require_current_index: bool,
    ) -> BuildProjectOptions:
        project = self.project_repository.get_by_key(project_key)

        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Code project not found: {project_key}",
            )

        self._validate_project_build_state(
            project=project,
            project_key=project_key,
            require_current_index=require_current_index,
        )

        return self._build_project_options(project, data)

    def _lock_project_for_build(
        self,
        project_key: str,
        data: CodebaseIndexBuildRequest,
        require_current_index: bool,
    ) -> BuildProjectOptions:
        project = self.project_repository.get_by_key_for_update(project_key)

        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Code project not found: {project_key}",
            )

        self._validate_project_build_state(
            project=project,
            project_key=project_key,
            require_current_index=require_current_index,
        )

        return self._build_project_options(project, data)

    def _validate_project_build_state(
        self,
        project: CodeProject,
        project_key: str,
        require_current_index: bool,
    ) -> None:
        if require_current_index and project.current_index_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Code project has no current index. "
                    f"Use /code-projects/{project_key}/index/build first."
                ),
            )

        if not require_current_index and project.current_index_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code project already has current index: {project_key}",
            )

        if self.index_repository.has_building_index(project.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code project already has building index: {project_key}",
            )

    def _build_project_options(
        self,
        project: CodeProject,
        data: CodebaseIndexBuildRequest,
    ) -> BuildProjectOptions:
        return BuildProjectOptions(
            project=project,
            max_files=data.max_files or project.default_max_files,
            max_file_bytes=data.max_file_bytes or project.default_max_file_bytes,
            initial_current_index_id=project.current_index_id,
        )

    def _ensure_qdrant_provider(self) -> None:
        if self.settings.vector_store_provider != "qdrant":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Persistent codebase index build requires VECTOR_STORE_PROVIDER=qdrant",
            )

    def _resolve_embedding_config(
        self,
        data: CodebaseIndexBuildRequest,
    ) -> BuildEmbeddingConfig:
        provider_source = (
            data.embedding_provider
            if data.embedding_provider is not None
            else self.settings.embedding_provider
        )
        model_source = (
            data.embedding_model
            if data.embedding_model is not None
            else self.settings.embedding_model
        )

        provider = (provider_source or "").lower().strip()
        model = (model_source or "").strip()

        dimensions = (
            data.embedding_dimensions
            if data.embedding_dimensions is not None
            else self.settings.embedding_dimensions
        )

        if not provider:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="embedding_provider is required",
            )

        if provider in {"openai", "openai_compatible"} and not model:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="embedding_model is required",
            )

        return BuildEmbeddingConfig(
            provider=provider,
            model=model,
            dimensions=dimensions,
        )

    def _build_embedding_client(
        self,
        config: BuildEmbeddingConfig,
    ) -> EmbeddingClient:
        try:
            return build_embedding_client_from_options(
                provider=config.provider,
                model=config.model,
                api_key=self.settings.embedding_api_key,
                base_url=self.settings.embedding_base_url,
                dimensions=config.dimensions,
                batch_size=self.settings.embedding_batch_size,
                allow_hashing=False,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    def _preflight_embedding_provider(
        self,
        embedding_client: EmbeddingClient,
        configured_dimensions: int | None,
    ) -> int:
        probe_texts = [
            "ai-dev-agent embedding provider preflight",
            "code search semantic retrieval test",
        ]

        vectors = embedding_client.embed_texts(probe_texts)
        vector_size = self._validate_vectors(vectors)

        if len(vectors) != len(probe_texts):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Embedding provider preflight returned unexpected vector count: "
                    f"expected={len(probe_texts)}, actual={len(vectors)}"
                ),
            )

        if configured_dimensions is not None and vector_size != configured_dimensions:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Configured embedding_dimensions does not match provider response: "
                    f"configured={configured_dimensions}, actual={vector_size}"
                ),
            )

        return vector_size

    def _validate_vectors(self, vectors: list[list[float]]) -> int:
        if not vectors:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Embedding provider returned no vectors",
            )

        vector_size = len(vectors[0])
        if vector_size <= 0:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Embedding provider returned an empty vector",
            )

        for vector in vectors:
            if len(vector) != vector_size:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Embedding provider returned vectors with different dimensions",
                )

        return vector_size

    def _build_vector_writer(self, collection_name: str) -> QdrantCodeChunkVectorWriter:
        return QdrantCodeChunkVectorWriter(
            collection_name=collection_name,
            url=self.settings.qdrant_url or None,
            api_key=self.settings.qdrant_api_key or None,
            location=self.settings.qdrant_local_path or None,
            prefer_grpc=self.settings.qdrant_prefer_grpc,
        )

    def _build_collection_name(self, project_key: str, index_id: UUID) -> str:
        base = self.settings.qdrant_collection_name or "codebase_chunks"
        raw_name = f"{base}_{project_key}_{index_id}"
        return re.sub(r"[^a-zA-Z0-9_]+", "_", raw_name).strip("_").lower()

    def _mark_failed_best_effort(self, index_id, error: Exception) -> None:
        if index_id is None:
            return

        try:
            self.index_repository.mark_failed(
                index_id=index_id,
                error_message=str(error),
            )
            self.session.commit()
        except Exception:
            logger.exception(
                "Failed to mark persistent codebase index as failed: index_id=%s",
                index_id,
            )
            self.session.rollback()

    def _delete_qdrant_collection_best_effort(self, collection_name: str) -> None:
        try:
            writer = self._build_vector_writer(collection_name)
            writer.delete_collection()
        except Exception:
            logger.exception(
                "Failed to delete Qdrant collection after failed codebase index build: "
                "collection=%s",
                collection_name,
            )
