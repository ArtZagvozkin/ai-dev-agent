import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.components.code_search.embeddings import (
    EmbeddingClient,
    build_embedding_client_from_options,
)
from app.components.code_search.indexer import CodebaseIndexer, IndexedCodeFile
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
    skip_failed_files: bool
    max_failed_files: int


@dataclass(slots=True)
class SkippedIndexedFile:
    path: str
    stage: str
    error: str
    language: str | None
    size_bytes: int | None
    chunks_count: int | None
    input_summary: dict[str, Any]

    def to_response(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "stage": self.stage,
            "error": self.error,
            "language": self.language,
            "size_bytes": self.size_bytes,
            "chunks_count": self.chunks_count,
            "input_summary": self.input_summary,
        }


class IndexedFileBuildError(Exception):
    def __init__(
        self,
        stage: str,
        indexed_file: IndexedCodeFile,
        input_summary: dict[str, Any],
        original_error: Exception,
    ):
        self.stage = stage
        self.indexed_file = indexed_file
        self.input_summary = input_summary
        self.original_error = original_error
        super().__init__(str(original_error))


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

        Failed-file skipping rule:
        - if skip_failed_files=false, any file-level error aborts the build;
        - if skip_failed_files=true, failed files are skipped up to max_failed_files;
        - skipped files are not counted in files_count/chunks_count;
        - skipped files must not remain partially persisted in Postgres/Qdrant.
        """
        logger.info(
            "Persistent codebase index build requested: "
            "project_key=%s, require_current_index=%s, attach_mode=%s, "
            "request_max_files=%s, request_max_file_bytes=%s, "
            "request_embedding_provider=%s, request_embedding_model=%s, "
            "request_embedding_dimensions=%s, skip_failed_files=%s, max_failed_files=%s",
            project_key,
            require_current_index,
            attach_mode,
            data.max_files,
            data.max_file_bytes,
            data.embedding_provider,
            data.embedding_model,
            data.embedding_dimensions,
            data.skip_failed_files,
            data.max_failed_files,
        )

        self._ensure_qdrant_provider()

        self._precheck_project_for_build(
            project_key=project_key,
            data=data,
            require_current_index=require_current_index,
        )

        embedding_config = self._resolve_embedding_config(data)
        embedding_client = self._build_embedding_client(embedding_config)

        logger.info(
            "Persistent codebase index embedding config resolved: "
            "project_key=%s, provider=%s, model=%s, dimensions=%s",
            project_key,
            embedding_config.provider,
            embedding_config.model,
            embedding_config.dimensions,
        )

        embedding_dimensions = self._preflight_embedding_provider(
            embedding_client=embedding_client,
            configured_dimensions=embedding_config.dimensions,
        )

        logger.info(
            "Persistent codebase index embedding preflight completed: "
            "project_key=%s, provider=%s, model=%s, actual_dimensions=%s",
            project_key,
            embedding_config.provider,
            embedding_config.model,
            embedding_dimensions,
        )

        index = None

        try:
            options = self._lock_project_for_build(
                project_key=project_key,
                data=data,
                require_current_index=require_current_index,
            )
            project = options.project

            logger.info(
                "Persistent codebase index project locked: "
                "project_key=%s, project_id=%s, repository_path=%s, "
                "max_files=%s, max_file_bytes=%s, initial_current_index_id=%s, "
                "skip_failed_files=%s, max_failed_files=%s",
                project.project_key,
                project.id,
                project.local_repository_path,
                options.max_files,
                options.max_file_bytes,
                options.initial_current_index_id,
                options.skip_failed_files,
                options.max_failed_files,
            )

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

            logger.info(
                "Persistent codebase index record created: "
                "project_key=%s, project_id=%s, index_id=%s, collection=%s",
                project.project_key,
                project.id,
                index.id,
                index.qdrant_collection_name,
            )

        except HTTPException:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            logger.exception(
                "Failed to create persistent codebase index record: project_key=%s",
                project_key,
            )
            raise

        files_count = 0
        chunks_count = 0
        skipped_files: list[SkippedIndexedFile] = []

        try:
            vector_writer = self._build_vector_writer(index.qdrant_collection_name)

            logger.info(
                "Recreating Qdrant collection for persistent codebase index: "
                "project_key=%s, index_id=%s, collection=%s, vector_size=%s",
                project_key,
                index.id,
                index.qdrant_collection_name,
                embedding_dimensions,
            )

            vector_writer.recreate_collection(vector_size=embedding_dimensions)

            for indexed_file in self.indexer.iter_files(
                repository_path=project.local_repository_path,
                max_files=options.max_files,
                max_file_bytes=options.max_file_bytes,
            ):
                try:
                    processed_chunks_count = self._process_indexed_file(
                        project_key=project_key,
                        index_id=index.id,
                        indexed_file=indexed_file,
                        embedding_client=embedding_client,
                        embedding_dimensions=embedding_dimensions,
                        vector_writer=vector_writer,
                        next_files_count=files_count + 1,
                        next_chunks_count=chunks_count + len(indexed_file.chunks),
                    )
                except IndexedFileBuildError as exc:
                    self.session.rollback()

                    if self._skip_or_raise_file_error(
                        options=options,
                        skipped_files=skipped_files,
                        error=exc,
                    ):
                        continue

                    raise exc.original_error from exc

                files_count += 1
                chunks_count += processed_chunks_count

                if files_count % 25 == 0:
                    logger.info(
                        "Persistent codebase index build progress: "
                        "project_key=%s, index_id=%s, files_count=%s, chunks_count=%s, "
                        "skipped_files_count=%s, last_file_path=%s, "
                        "last_file_chunks=%s, last_file_size_bytes=%s",
                        project_key,
                        index.id,
                        files_count,
                        chunks_count,
                        len(skipped_files),
                        indexed_file.path,
                        len(indexed_file.chunks),
                        indexed_file.size_bytes,
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

            logger.info(
                "Persistent codebase index marked ready: "
                "project_key=%s, index_id=%s, files_count=%s, chunks_count=%s, "
                "skipped_files_count=%s",
                project_key,
                index.id,
                files_count,
                chunks_count,
                len(skipped_files),
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

            logger.info(
                "Persistent codebase index attached as current: "
                "project_key=%s, project_id=%s, index_id=%s, attach_mode=%s, "
                "skipped_files_count=%s",
                project_key,
                project.id,
                index.id,
                attach_mode,
                len(skipped_files),
            )

            refreshed_index = self.index_repository.get_by_id(index.id)
            return self._build_response_payload(
                index=refreshed_index or ready_index,
                skipped_files=skipped_files,
            )

        except Exception as exc:
            self.session.rollback()

            logger.exception(
                "Persistent codebase index build failed: "
                "project_key=%s, index_id=%s, files_count=%s, chunks_count=%s, "
                "skipped_files_count=%s, error=%s",
                project_key,
                index.id if index else None,
                files_count,
                chunks_count,
                len(skipped_files),
                exc,
            )

            self._mark_failed_best_effort(
                index_id=index.id if index else None,
                error=exc,
            )

            if index is not None:
                self._delete_qdrant_collection_best_effort(
                    collection_name=index.qdrant_collection_name,
                )

            raise

    def _process_indexed_file(
        self,
        project_key: str,
        index_id: UUID,
        indexed_file: IndexedCodeFile,
        embedding_client: EmbeddingClient,
        embedding_dimensions: int,
        vector_writer: QdrantCodeChunkVectorWriter,
        next_files_count: int,
        next_chunks_count: int,
    ) -> int:
        searchable_texts = [
            chunk.contextualized_text
            for chunk in indexed_file.chunks
        ]

        embedding_input_summary = self._embedding_input_summary(
            indexed_file=indexed_file,
            searchable_texts=searchable_texts,
        )

        try:
            vectors = embedding_client.embed_texts(searchable_texts)
        except Exception as exc:
            logger.exception(
                "Failed to embed indexed file chunks: "
                "project_key=%s, index_id=%s, file_path=%s, "
                "language=%s, size_bytes=%s, chunks=%s, input_summary=%s",
                project_key,
                index_id,
                indexed_file.path,
                indexed_file.language,
                indexed_file.size_bytes,
                len(indexed_file.chunks),
                embedding_input_summary,
            )
            raise IndexedFileBuildError(
                stage="embedding",
                indexed_file=indexed_file,
                input_summary=embedding_input_summary,
                original_error=exc,
            ) from exc

        try:
            vector_size = self._validate_vectors(vectors)

            if vector_size != embedding_dimensions:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Embedding dimension mismatch: "
                        f"expected={embedding_dimensions}, actual={vector_size}"
                    ),
                )

            if len(indexed_file.chunks) != len(vectors):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Chunk/vector count mismatch: "
                        f"chunks={len(indexed_file.chunks)}, vectors={len(vectors)}"
                    ),
                )

        except Exception as exc:
            logger.exception(
                "Invalid embedding vectors for indexed file: "
                "project_key=%s, index_id=%s, file_path=%s, "
                "expected_dimensions=%s, vectors=%s, input_summary=%s",
                project_key,
                index_id,
                indexed_file.path,
                embedding_dimensions,
                len(vectors),
                embedding_input_summary,
            )
            raise IndexedFileBuildError(
                stage="embedding_validation",
                indexed_file=indexed_file,
                input_summary=embedding_input_summary,
                original_error=exc,
            ) from exc

        db_chunk_ids: list[UUID] = []

        try:
            file_id = self.file_repository.create(
                index_id=index_id,
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

            if len(db_chunk_ids) != len(vectors):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Stored chunk/vector count mismatch: "
                        f"chunks={len(db_chunk_ids)}, vectors={len(vectors)}"
                    ),
                )

            vector_writer.upsert_vectors(
                index_id=index_id,
                chunk_ids=db_chunk_ids,
                vectors=vectors,
            )

            self.index_repository.update_counts(
                index_id=index_id,
                files_count=next_files_count,
                chunks_count=next_chunks_count,
            )

            self.session.commit()

        except Exception as exc:
            self.session.rollback()

            if db_chunk_ids:
                self._delete_qdrant_vectors_best_effort(
                    vector_writer=vector_writer,
                    chunk_ids=db_chunk_ids,
                )

            logger.exception(
                "Failed to persist indexed file to Postgres/Qdrant: "
                "project_key=%s, index_id=%s, file_path=%s, "
                "language=%s, size_bytes=%s, chunks=%s, input_summary=%s",
                project_key,
                index_id,
                indexed_file.path,
                indexed_file.language,
                indexed_file.size_bytes,
                len(indexed_file.chunks),
                embedding_input_summary,
            )
            raise IndexedFileBuildError(
                stage="storage",
                indexed_file=indexed_file,
                input_summary=embedding_input_summary,
                original_error=exc,
            ) from exc

        return len(indexed_file.chunks)

    def _skip_or_raise_file_error(
        self,
        options: BuildProjectOptions,
        skipped_files: list[SkippedIndexedFile],
        error: IndexedFileBuildError,
    ) -> bool:
        if not options.skip_failed_files:
            return False

        if len(skipped_files) >= options.max_failed_files:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Codebase index build failed because max_failed_files limit was reached: "
                    f"max_failed_files={options.max_failed_files}, "
                    f"failed_file={error.indexed_file.path}, "
                    f"stage={error.stage}, "
                    f"error={self._format_exception(error.original_error)}"
                ),
            ) from error.original_error

        skipped_file = SkippedIndexedFile(
            path=error.indexed_file.path,
            stage=error.stage,
            error=self._format_exception(error.original_error),
            language=error.indexed_file.language,
            size_bytes=error.indexed_file.size_bytes,
            chunks_count=len(error.indexed_file.chunks),
            input_summary=error.input_summary,
        )

        skipped_files.append(skipped_file)

        logger.warning(
            "Skipping failed indexed file during persistent codebase index build: "
            "project_key=%s, file_path=%s, stage=%s, skipped_files_count=%s, "
            "max_failed_files=%s, error=%s, input_summary=%s",
            options.project.project_key,
            skipped_file.path,
            skipped_file.stage,
            len(skipped_files),
            options.max_failed_files,
            skipped_file.error,
            skipped_file.input_summary,
        )

        return True

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
            skip_failed_files=data.skip_failed_files,
            max_failed_files=data.max_failed_files,
        )

    def _ensure_qdrant_provider(self) -> None:
        qdrant_url = (self.settings.qdrant_url or "").strip()
        qdrant_local_path = (self.settings.qdrant_local_path or "").strip()

        if not qdrant_url and not qdrant_local_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Persistent codebase index build requires QDRANT_URL "
                    "or durable QDRANT_LOCAL_PATH"
                ),
            )

        if qdrant_local_path == ":memory:":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Persistent codebase index build cannot use in-memory Qdrant",
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

    def _embedding_input_summary(
        self,
        indexed_file: IndexedCodeFile,
        searchable_texts: list[str],
    ) -> dict[str, Any]:
        input_lengths = [len(text) for text in searchable_texts]
        max_input_chars = max(input_lengths, default=0)
        total_input_chars = sum(input_lengths)

        largest_chunk = None

        if indexed_file.chunks and searchable_texts:
            largest_index = max(
                range(len(searchable_texts)),
                key=lambda index: len(searchable_texts[index]),
            )
            chunk = indexed_file.chunks[largest_index]

            largest_chunk = {
                "chunk_id": chunk.chunk_id,
                "chunk_type": chunk.chunk_type,
                "symbol": chunk.symbol,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "input_chars": len(searchable_texts[largest_index]),
                "content_chars": len(chunk.content or ""),
                "contextualized_text_chars": len(chunk.contextualized_text or ""),
            }

        return {
            "chunks": len(indexed_file.chunks),
            "total_input_chars": total_input_chars,
            "max_input_chars": max_input_chars,
            "largest_chunk": largest_chunk,
        }

    def _format_exception(self, error: Exception) -> str:
        if isinstance(error, HTTPException):
            return str(error.detail)

        message = str(error).strip()
        if message:
            return message[:4000]

        return error.__class__.__name__

    def _build_response_payload(
        self,
        index,
        skipped_files: list[SkippedIndexedFile],
    ) -> dict[str, Any]:
        return {
            "id": index.id,
            "project_id": index.project_id,
            "project_key": index.project_key,
            "status": index.status,
            "index_schema_version": index.index_schema_version,
            "embedding_provider": index.embedding_provider,
            "embedding_model": index.embedding_model,
            "embedding_dimensions": index.embedding_dimensions,
            "qdrant_collection_name": index.qdrant_collection_name,
            "files_count": index.files_count,
            "chunks_count": index.chunks_count,
            "build_started_at": index.build_started_at,
            "build_finished_at": index.build_finished_at,
            "error_message": index.error_message,
            "is_current": index.is_current,
            "skipped_files_count": len(skipped_files),
            "skipped_files": [
                skipped_file.to_response()
                for skipped_file in skipped_files
            ],
        }

    def _delete_qdrant_vectors_best_effort(
        self,
        vector_writer: QdrantCodeChunkVectorWriter,
        chunk_ids: list[UUID],
    ) -> None:
        try:
            vector_writer.delete_vectors(chunk_ids)
        except Exception:
            logger.exception(
                "Failed to delete Qdrant vectors after file-level build failure: "
                "collection=%s, chunk_ids_count=%s",
                vector_writer.collection_name,
                len(chunk_ids),
            )

    def _mark_failed_best_effort(self, index_id, error: Exception) -> None:
        if index_id is None:
            return

        try:
            self.index_repository.mark_failed(
                index_id=index_id,
                error_message=self._format_exception(error),
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
