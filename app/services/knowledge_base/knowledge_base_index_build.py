import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.components.knowledge_base.chunker import (
    KnowledgeBaseChunkDraft,
    KnowledgeBaseChunkerConfig,
    chunk_article,
    compute_article_hash,
)
from app.components.knowledge_base.embeddings import (
    KnowledgeBaseEmbeddingClient,
    KnowledgeBaseEmbeddingConfig,
    build_knowledge_base_embedding_client,
    normalize_knowledge_base_embedding_config,
)
from app.components.knowledge_base.vector_writer import QdrantKnowledgeBaseVectorWriter
from app.core.config import Settings
from app.domain.knowledge_bases import KnowledgeBase
from app.repositories.knowledge_base_documents import KnowledgeBaseDocumentRepository
from app.repositories.knowledge_base_indexes import KnowledgeBaseIndexRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository


logger = logging.getLogger(__name__)

KNOWLEDGE_BASE_INDEX_SCHEMA_VERSION = "knowledge-base-index-v1"
KB_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(slots=True)
class KnowledgeBaseIndexBuildRequest:
    embedding_provider: str
    embedding_base_url: str
    embedding_model: str
    embedding_batch_size: int

    source_path: str | None = None
    embedding_dimensions: int | None = None
    max_documents: int | None = None
    create_if_missing: bool = False
    display_name: str | None = None


@dataclass(slots=True)
class BuildKnowledgeBaseOptions:
    knowledge_base: KnowledgeBase
    source_path: Path
    initial_current_index_id: UUID | None
    max_documents: int | None


@dataclass(slots=True)
class ProcessedKnowledgeBaseArticle:
    documents_count: int
    chunks_count: int


class KnowledgeBaseIndexBuildService:
    def __init__(
        self,
        settings: Settings,
        session: Session,
        knowledge_base_repository: KnowledgeBaseRepository,
        index_repository: KnowledgeBaseIndexRepository,
        document_repository: KnowledgeBaseDocumentRepository,
    ):
        self.settings = settings
        self.session = session
        self.knowledge_base_repository = knowledge_base_repository
        self.index_repository = index_repository
        self.document_repository = document_repository

    def build_first_index(
        self,
        kb_key: str,
        data: KnowledgeBaseIndexBuildRequest,
    ) -> dict[str, Any]:
        return self._build_persistent_index(
            kb_key=self._normalize_kb_key(kb_key),
            data=data,
            require_current_index=False,
            attach_mode="if_empty",
        )

    def rebuild_index(
        self,
        kb_key: str,
        data: KnowledgeBaseIndexBuildRequest,
    ) -> dict[str, Any]:
        return self._build_persistent_index(
            kb_key=self._normalize_kb_key(kb_key),
            data=data,
            require_current_index=True,
            attach_mode="overwrite_if_current_unchanged",
        )

    def _build_persistent_index(
        self,
        kb_key: str,
        data: KnowledgeBaseIndexBuildRequest,
        require_current_index: bool,
        attach_mode: str,
    ) -> dict[str, Any]:
        logger.info(
            "Knowledge base index build requested: "
            "kb_key=%s, require_current_index=%s, attach_mode=%s, "
            "source_path=%s, embedding_provider=%s, embedding_base_url=%s, "
            "embedding_model=%s, embedding_dimensions=%s, embedding_batch_size=%s, "
            "max_documents=%s",
            kb_key,
            require_current_index,
            attach_mode,
            data.source_path,
            data.embedding_provider,
            data.embedding_base_url,
            data.embedding_model,
            data.embedding_dimensions,
            data.embedding_batch_size,
            data.max_documents,
        )

        self._ensure_qdrant_provider()

        embedding_config = self._resolve_embedding_config(data)
        embedding_client = self._build_embedding_client(embedding_config)

        embedding_dimensions = self._preflight_embedding_provider(
            embedding_client=embedding_client,
            configured_dimensions=embedding_config.dimensions,
        )

        index = None

        try:
            options = self._lock_knowledge_base_for_build(
                kb_key=kb_key,
                data=data,
                require_current_index=require_current_index,
            )

            knowledge_base = options.knowledge_base

            index_id = uuid4()
            qdrant_collection_name = self._build_collection_name(
                kb_key=knowledge_base.kb_key,
                index_id=index_id,
            )

            index = self.index_repository.create_building(
                index_id=index_id,
                knowledge_base_id=knowledge_base.id,
                index_schema_version=KNOWLEDGE_BASE_INDEX_SCHEMA_VERSION,
                embedding_provider=embedding_config.provider,
                embedding_model=embedding_config.model,
                embedding_dimensions=embedding_dimensions,
                qdrant_collection_name=qdrant_collection_name,
            )

            self.session.commit()

            logger.info(
                "Knowledge base index record created: "
                "kb_key=%s, knowledge_base_id=%s, index_id=%s, collection=%s",
                knowledge_base.kb_key,
                knowledge_base.id,
                index.id,
                index.qdrant_collection_name,
            )

        except HTTPException:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            logger.exception(
                "Failed to create knowledge base index record: kb_key=%s",
                kb_key,
            )
            raise

        documents_count = 0
        chunks_count = 0

        try:
            vector_writer = self._build_vector_writer(index.qdrant_collection_name)
            vector_writer.recreate_collection(vector_size=embedding_dimensions)

            article_paths = self._iter_article_paths(
                source_path=options.source_path,
                max_documents=options.max_documents,
            )

            if not article_paths:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No article.json files found under: {options.source_path}",
                )

            chunker_config = KnowledgeBaseChunkerConfig()

            for article_path in article_paths:
                processed = self._process_article_file(
                    index_id=index.id,
                    article_path=article_path,
                    embedding_client=embedding_client,
                    embedding_dimensions=embedding_dimensions,
                    vector_writer=vector_writer,
                    next_documents_count=documents_count + 1,
                    next_chunks_count=chunks_count,
                    chunker_config=chunker_config,
                )

                documents_count += processed.documents_count
                chunks_count += processed.chunks_count

                if documents_count and documents_count % 25 == 0:
                    logger.info(
                        "Knowledge base index build progress: "
                        "kb_key=%s, index_id=%s, documents_count=%s, chunks_count=%s, "
                        "last_article=%s",
                        kb_key,
                        index.id,
                        documents_count,
                        chunks_count,
                        article_path,
                    )

            if chunks_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No searchable knowledge base chunks found under: {options.source_path}",
                )

            ready_index = self.index_repository.mark_ready(
                index_id=index.id,
                documents_count=documents_count,
                chunks_count=chunks_count,
            )

            if attach_mode == "if_empty":
                current_set = self.knowledge_base_repository.set_current_index_if_empty(
                    knowledge_base_id=options.knowledge_base.id,
                    index_id=index.id,
                )

            elif attach_mode == "overwrite_if_current_unchanged":
                if options.initial_current_index_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Knowledge base has no current index at rebuild attach stage: "
                            f"{kb_key}"
                        ),
                    )

                current_set = self.knowledge_base_repository.set_current_index_if_matches(
                    knowledge_base_id=options.knowledge_base.id,
                    expected_current_index_id=options.initial_current_index_id,
                    new_index_id=index.id,
                )

            else:
                raise RuntimeError(f"Unsupported index attach mode: {attach_mode}")

            if not current_set:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Knowledge base current index was changed while build was running: "
                        f"{kb_key}"
                    ),
                )

            self.session.commit()

            refreshed_index = self.index_repository.get_by_id(index.id)
            return self._build_response_payload(refreshed_index or ready_index)

        except Exception as exc:
            self.session.rollback()

            logger.exception(
                "Knowledge base index build failed: "
                "kb_key=%s, index_id=%s, documents_count=%s, chunks_count=%s, error=%s",
                kb_key,
                index.id if index else None,
                documents_count,
                chunks_count,
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

    def _process_article_file(
        self,
        index_id: UUID,
        article_path: Path,
        embedding_client: KnowledgeBaseEmbeddingClient,
        embedding_dimensions: int,
        vector_writer: QdrantKnowledgeBaseVectorWriter,
        next_documents_count: int,
        next_chunks_count: int,
        chunker_config: KnowledgeBaseChunkerConfig,
    ) -> ProcessedKnowledgeBaseArticle:
        try:
            article = json.loads(article_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid article.json: article_path={article_path}, error={exc}",
            ) from exc

        try:
            chunks, article_content = chunk_article(article, chunker_config)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to chunk article: article_path={article_path}, error={exc}",
            ) from exc

        if not chunks:
            logger.warning(
                "Knowledge base article produced no chunks and will be skipped: article_path=%s",
                article_path,
            )
            return ProcessedKnowledgeBaseArticle(
                documents_count=0,
                chunks_count=0,
            )

        searchable_texts = [
            chunk.contextualized_text.strip()
            for chunk in chunks
        ]

        self._validate_searchable_texts(
            article_path=article_path,
            chunks=chunks,
            searchable_texts=searchable_texts,
        )

        embedding_input_summary = self._embedding_input_summary(
            chunks=chunks,
            searchable_texts=searchable_texts,
        )

        try:
            vectors = embedding_client.embed_texts(searchable_texts)

        except Exception as exc:
            logger.exception(
                "Failed to embed knowledge base article chunks: "
                "index_id=%s, article_path=%s, chunks=%s, input_summary=%s",
                index_id,
                article_path,
                len(chunks),
                embedding_input_summary,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Embedding request failed for knowledge base article: "
                    f"article_path={article_path}, error={self._format_exception(exc)}"
                ),
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

            if len(chunks) != len(vectors):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Chunk/vector count mismatch: "
                        f"chunks={len(chunks)}, vectors={len(vectors)}"
                    ),
                )

        except Exception:
            logger.exception(
                "Invalid embedding vectors for knowledge base article: "
                "index_id=%s, article_path=%s, expected_dimensions=%s, "
                "vectors=%s, input_summary=%s",
                index_id,
                article_path,
                embedding_dimensions,
                len(vectors),
                embedding_input_summary,
            )
            raise

        chunk_db_ids: list[UUID] = []

        try:
            article_hash = compute_article_hash(article, article_content)

            document_id = self.document_repository.create_document(
                index_id=index_id,
                external_id=str(article.get("entry_id") or ""),
                source_url=article.get("source_url"),
                title=article.get("title"),
                full_title=article.get("full_title"),
                source_revision=str(article.get("revision"))
                if article.get("revision") is not None
                else None,
                updated_at_src=article.get("updated_at"),
                content_hash=article_hash,
                content=article_content,
                chunks_count=len(chunks),
                metadata={
                    "article_path": str(article_path),
                },
            )

            chunk_db_ids = self.document_repository.create_chunks(
                document_id=document_id,
                chunks=chunks,
            )

            if len(chunk_db_ids) != len(vectors):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Stored chunk/vector count mismatch: "
                        f"chunks={len(chunk_db_ids)}, vectors={len(vectors)}"
                    ),
                )

            self.document_repository.create_images_and_links(
                document_id=document_id,
                chunks=chunks,
                chunk_db_ids=chunk_db_ids,
            )

            vector_writer.upsert_vectors(
                index_id=index_id,
                document_id=document_id,
                chunk_ids=chunk_db_ids,
                vectors=vectors,
            )

            self.index_repository.update_counts(
                index_id=index_id,
                documents_count=next_documents_count,
                chunks_count=next_chunks_count + len(chunks),
            )

            self.session.commit()

        except Exception:
            self.session.rollback()

            if chunk_db_ids:
                self._delete_qdrant_vectors_best_effort(
                    vector_writer=vector_writer,
                    chunk_ids=chunk_db_ids,
                )

            raise

        return ProcessedKnowledgeBaseArticle(
            documents_count=1,
            chunks_count=len(chunks),
        )

    def _lock_knowledge_base_for_build(
        self,
        kb_key: str,
        data: KnowledgeBaseIndexBuildRequest,
        require_current_index: bool,
    ) -> BuildKnowledgeBaseOptions:
        knowledge_base = self.knowledge_base_repository.get_by_key_for_update(kb_key)

        if knowledge_base is None:
            if not data.create_if_missing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Knowledge base not found: {kb_key}",
                )

            if not data.source_path:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="source_path is required to create knowledge base",
                )

            knowledge_base = self.knowledge_base_repository.create_if_missing(
                kb_key=kb_key,
                display_name=data.display_name or kb_key,
                source_type="local_json",
                source_path=data.source_path,
            )

            self.session.flush()

        if data.source_path and data.source_path != knowledge_base.source_path:
            updated = self.knowledge_base_repository.update_source_path(
                kb_key=kb_key,
                source_path=data.source_path,
            )
            if updated:
                knowledge_base = updated

        self._validate_knowledge_base_build_state(
            knowledge_base=knowledge_base,
            kb_key=kb_key,
            require_current_index=require_current_index,
        )

        self._validate_knowledge_base_source_type(knowledge_base)

        source_path_value = data.source_path or knowledge_base.source_path
        if not source_path_value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Knowledge base source_path is not configured: {kb_key}",
            )

        source_path = Path(source_path_value)
        if not source_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Knowledge base source_path not found: {source_path}",
            )

        if not source_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Knowledge base source_path must be a directory: {source_path}",
            )

        return BuildKnowledgeBaseOptions(
            knowledge_base=knowledge_base,
            source_path=source_path,
            initial_current_index_id=knowledge_base.current_index_id,
            max_documents=data.max_documents,
        )

    def _validate_knowledge_base_build_state(
        self,
        knowledge_base: KnowledgeBase,
        kb_key: str,
        require_current_index: bool,
    ) -> None:
        if require_current_index and knowledge_base.current_index_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Knowledge base has no current index. "
                    f"Run first build for knowledge base: {kb_key}"
                ),
            )

        if not require_current_index and knowledge_base.current_index_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Knowledge base already has current index: {kb_key}",
            )

        if self.index_repository.has_building_index(knowledge_base.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Knowledge base already has building index: {kb_key}",
            )

    def _validate_knowledge_base_source_type(
        self,
        knowledge_base: KnowledgeBase,
    ) -> None:
        if knowledge_base.source_type != "local_json":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Knowledge base index build currently supports only local_json source_type: "
                    f"kb_key={knowledge_base.kb_key}, source_type={knowledge_base.source_type}"
                ),
            )

    def _iter_article_paths(
        self,
        source_path: Path,
        max_documents: int | None,
    ) -> list[Path]:
        if max_documents is not None and max_documents <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="max_documents must be greater than 0",
            )

        def sort_key(path: Path) -> tuple[int, int | str]:
            parent_name = path.parent.name

            if parent_name.isdigit():
                return (0, int(parent_name))

            return (1, parent_name)

        article_paths = sorted(
            source_path.glob("*/article.json"),
            key=sort_key,
        )

        if max_documents is not None:
            article_paths = article_paths[:max_documents]

        return article_paths

    def _ensure_qdrant_provider(self) -> None:
        qdrant_url = (self.settings.qdrant_url or "").strip()
        qdrant_local_path = (self.settings.qdrant_local_path or "").strip()

        if not qdrant_url and not qdrant_local_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Knowledge base index build requires QDRANT_URL "
                    "or durable QDRANT_LOCAL_PATH"
                ),
            )

        if qdrant_local_path == ":memory:":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Knowledge base index build cannot use in-memory Qdrant",
            )

    def _resolve_embedding_config(
        self,
        data: KnowledgeBaseIndexBuildRequest,
    ) -> KnowledgeBaseEmbeddingConfig:
        try:
            return normalize_knowledge_base_embedding_config(
                provider=data.embedding_provider,
                base_url=data.embedding_base_url,
                model=data.embedding_model,
                batch_size=data.embedding_batch_size,
                dimensions=data.embedding_dimensions,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    def _build_embedding_client(
        self,
        config: KnowledgeBaseEmbeddingConfig,
    ) -> KnowledgeBaseEmbeddingClient:
        try:
            return build_knowledge_base_embedding_client(
                api_key=self.settings.openrouter_api_key,
                config=config,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    def _preflight_embedding_provider(
        self,
        embedding_client: KnowledgeBaseEmbeddingClient,
        configured_dimensions: int | None,
    ) -> int:
        probe_texts = [
            "ai-dev-agent knowledge base embedding provider preflight",
            "knowledge base semantic retrieval test",
        ]

        try:
            vectors = embedding_client.embed_texts(probe_texts)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Embedding provider preflight failed: "
                    f"{self._format_exception(exc)}"
                ),
            ) from exc

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

    def _validate_searchable_texts(
        self,
        article_path: Path,
        chunks: list[KnowledgeBaseChunkDraft],
        searchable_texts: list[str],
    ) -> None:
        if len(chunks) != len(searchable_texts):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Chunk/searchable text count mismatch: "
                    f"article_path={article_path}, "
                    f"chunks={len(chunks)}, searchable_texts={len(searchable_texts)}"
                ),
            )

        empty_items = [
            chunks[index].chunk_id
            for index, text in enumerate(searchable_texts)
            if not text
        ]

        if empty_items:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Knowledge base chunk has empty embedding input: "
                    f"article_path={article_path}, chunk_ids={empty_items[:10]}"
                ),
            )

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

        for vector_index, vector in enumerate(vectors):
            if len(vector) != vector_size:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "Embedding provider returned vectors with different dimensions: "
                        f"expected={vector_size}, actual={len(vector)}, "
                        f"vector_index={vector_index}"
                    ),
                )

            for value_index, value in enumerate(vector):
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=(
                            "Embedding provider returned non-numeric vector value: "
                            f"vector_index={vector_index}, "
                            f"value_index={value_index}, value={value!r}"
                        ),
                    ) from exc

                if not math.isfinite(numeric_value):
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=(
                            "Embedding provider returned non-finite vector value: "
                            f"vector_index={vector_index}, "
                            f"value_index={value_index}, value={value!r}"
                        ),
                    )

        return vector_size

    def _build_vector_writer(
        self,
        collection_name: str,
    ) -> QdrantKnowledgeBaseVectorWriter:
        return QdrantKnowledgeBaseVectorWriter(
            collection_name=collection_name,
            url=self.settings.qdrant_url or None,
            api_key=self.settings.qdrant_api_key or None,
            location=self.settings.qdrant_local_path or None,
            prefer_grpc=self.settings.qdrant_prefer_grpc,
        )

    def _build_collection_name(
        self,
        kb_key: str,
        index_id: UUID,
    ) -> str:
        base = self.settings.qdrant_collection_name or "knowledge_base_chunks"
        raw_name = f"{base}_{kb_key}_{index_id}"
        return re.sub(r"[^a-zA-Z0-9_]+", "_", raw_name).strip("_").lower()

    def _embedding_input_summary(
        self,
        chunks: list[KnowledgeBaseChunkDraft],
        searchable_texts: list[str],
    ) -> dict[str, Any]:
        input_lengths = [len(text) for text in searchable_texts]
        max_input_chars = max(input_lengths, default=0)
        total_input_chars = sum(input_lengths)

        largest_chunk = None

        if chunks and searchable_texts:
            largest_index = max(
                range(len(searchable_texts)),
                key=lambda index: len(searchable_texts[index]),
            )
            chunk = chunks[largest_index]

            largest_chunk = {
                "chunk_id": chunk.chunk_id,
                "chunk_ord": chunk.chunk_ord,
                "title": chunk.title,
                "section_path": chunk.section_path,
                "block_types": chunk.block_types,
                "input_chars": len(searchable_texts[largest_index]),
                "text_chars": len(chunk.text or ""),
                "contextualized_text_chars": len(chunk.contextualized_text or ""),
            }

        return {
            "chunks": len(chunks),
            "total_input_chars": total_input_chars,
            "max_input_chars": max_input_chars,
            "largest_chunk": largest_chunk,
        }

    def _normalize_kb_key(self, value: Any) -> str:
        kb_key = str(value or "").strip()

        if not kb_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="kb_key is required",
            )

        if not KB_KEY_RE.match(kb_key):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "kb_key must match pattern: "
                    "^[a-z0-9][a-z0-9_-]*$"
                ),
            )

        return kb_key

    def _format_exception(self, error: Exception) -> str:
        if isinstance(error, HTTPException):
            return str(error.detail)

        message = str(error).strip()
        if message:
            return message[:4000]

        return error.__class__.__name__

    def _build_response_payload(self, index) -> dict[str, Any]:
        return {
            "id": index.id,
            "knowledge_base_id": index.knowledge_base_id,
            "kb_key": index.kb_key,
            "status": index.status,
            "index_schema_version": index.index_schema_version,
            "embedding_provider": index.embedding_provider,
            "embedding_model": index.embedding_model,
            "embedding_dimensions": index.embedding_dimensions,
            "qdrant_collection_name": index.qdrant_collection_name,
            "documents_count": index.documents_count,
            "chunks_count": index.chunks_count,
            "build_started_at": index.build_started_at,
            "build_finished_at": index.build_finished_at,
            "error_message": index.error_message,
            "is_current": index.is_current,
        }

    def _delete_qdrant_vectors_best_effort(
        self,
        vector_writer: QdrantKnowledgeBaseVectorWriter,
        chunk_ids: list[UUID],
    ) -> None:
        try:
            vector_writer.delete_vectors(chunk_ids)
        except Exception:
            logger.exception(
                "Failed to delete Qdrant vectors after knowledge base article failure: "
                "collection=%s, chunk_ids_count=%s",
                vector_writer.collection_name,
                len(chunk_ids),
            )

    def _mark_failed_best_effort(
        self,
        index_id: UUID | None,
        error: Exception,
    ) -> None:
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
                "Failed to mark knowledge base index as failed: index_id=%s",
                index_id,
            )
            self.session.rollback()

    def _delete_qdrant_collection_best_effort(
        self,
        collection_name: str,
    ) -> None:
        try:
            writer = self._build_vector_writer(collection_name)
            writer.delete_collection()
        except Exception:
            logger.exception(
                "Failed to delete Qdrant collection after failed knowledge base index build: "
                "collection=%s",
                collection_name,
            )
