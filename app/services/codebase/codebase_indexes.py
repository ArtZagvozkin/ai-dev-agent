import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.components.code_search.vector_writer import QdrantCodeChunkVectorWriter
from app.core.config import Settings
from app.repositories.codebase_indexes import CodebaseIndexRepository


logger = logging.getLogger(__name__)

ALLOWED_INDEX_STATUSES = {"building", "ready", "failed", "archived"}


class CodebaseIndexService:
    def __init__(
        self,
        repository: CodebaseIndexRepository,
        session: Session,
        settings: Settings,
    ):
        self.repository = repository
        self.session = session
        self.settings = settings

    def list_indexes(
        self,
        project_key: str | None = None,
        index_status: str | None = None,
    ):
        if index_status is not None and index_status not in ALLOWED_INDEX_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid codebase index status: {index_status}",
            )

        return self.repository.list(
            project_key=project_key,
            status=index_status,
        )

    def get_index(self, index_id: UUID):
        index = self.repository.get_by_id(index_id)

        if index is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Codebase index not found: {index_id}",
            )

        return index

    def delete_index(self, index_id: UUID):
        qdrant_collection_name = None

        try:
            index = self.get_index(index_id)
            qdrant_collection_name = index.qdrant_collection_name

            if index.is_current:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Current codebase index cannot be deleted: {index_id}",
                )

            if index.status == "building":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Building codebase index cannot be deleted: {index_id}",
                )

            deleted = self.repository.delete_by_id(index_id)

            if not deleted:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Codebase index not found: {index_id}",
                )

            self.session.commit()

        except HTTPException:
            self.session.rollback()
            raise
        except IntegrityError as exc:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Codebase index cannot be deleted because it is referenced: {index_id}",
            ) from exc
        except Exception:
            self.session.rollback()
            raise

        if qdrant_collection_name:
            self._delete_qdrant_collection_best_effort(qdrant_collection_name)

        return {
            "deleted": True,
            "index_id": index_id,
        }

    def _delete_qdrant_collection_best_effort(self, collection_name: str) -> None:
        qdrant_url = (self.settings.qdrant_url or "").strip()
        qdrant_local_path = (self.settings.qdrant_local_path or "").strip()

        if not qdrant_url and not qdrant_local_path:
            logger.warning(
                "Skipping Qdrant collection cleanup because Qdrant is not configured: "
                "collection=%s",
                collection_name,
            )
            return

        if qdrant_local_path == ":memory:":
            logger.warning(
                "Skipping Qdrant collection cleanup because in-memory Qdrant is not durable: "
                "collection=%s",
                collection_name,
            )
            return

        writer = QdrantCodeChunkVectorWriter(
            collection_name=collection_name,
            url=qdrant_url or None,
            api_key=self.settings.qdrant_api_key or None,
            location=qdrant_local_path or None,
            prefer_grpc=self.settings.qdrant_prefer_grpc,
        )

        try:
            writer.delete_collection()
        except Exception:
            logger.exception(
                "Failed to delete Qdrant collection for codebase index: collection=%s",
                collection_name,
            )
