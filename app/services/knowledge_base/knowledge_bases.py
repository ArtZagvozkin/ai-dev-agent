import logging
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.components.knowledge_base.vector_writer import QdrantKnowledgeBaseVectorWriter
from app.core.config import Settings
from app.domain.knowledge_bases import KnowledgeBase
from app.repositories.knowledge_base_indexes import KnowledgeBaseIndexRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository


logger = logging.getLogger(__name__)

KB_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

ALLOWED_SOURCE_TYPES = {
    "local_json",
    "confluence",
    "kbpublisher",
    "manual",
}


class KnowledgeBaseService:
    def __init__(
        self,
        settings: Settings,
        session: Session,
        repository: KnowledgeBaseRepository,
        index_repository: KnowledgeBaseIndexRepository,
    ):
        self.settings = settings
        self.session = session
        self.repository = repository
        self.index_repository = index_repository

    def list_knowledge_bases(self) -> list[KnowledgeBase]:
        return self.repository.list()

    def get_knowledge_base(self, kb_key: str) -> KnowledgeBase:
        normalized_key = self._normalize_kb_key(kb_key)

        knowledge_base = self.repository.get_by_key(normalized_key)
        if knowledge_base is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Knowledge base not found: {normalized_key}",
            )

        return knowledge_base

    def create_knowledge_base(self, data: dict[str, Any]) -> KnowledgeBase:
        create_data = self._normalize_create_data(data)

        existing = self.repository.get_by_key(create_data["kb_key"])
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Knowledge base already exists: {create_data['kb_key']}",
            )

        try:
            knowledge_base = self.repository.create(create_data)
            self.session.commit()
            return knowledge_base

        except IntegrityError as exc:
            self.session.rollback()
            raise self._integrity_error_to_http(exc) from exc

        except Exception:
            self.session.rollback()
            raise

    def update_knowledge_base(
        self,
        kb_key: str,
        data: dict[str, Any],
    ) -> KnowledgeBase:
        normalized_key = self._normalize_kb_key(kb_key)

        existing = self.repository.get_by_key(normalized_key)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Knowledge base not found: {normalized_key}",
            )

        update_data = self._normalize_update_data(data)

        if not update_data:
            return existing

        try:
            updated = self.repository.update_by_key(
                kb_key=normalized_key,
                data=update_data,
            )

            if updated is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Knowledge base not found: {normalized_key}",
                )

            self.session.commit()
            return updated

        except IntegrityError as exc:
            self.session.rollback()
            raise self._integrity_error_to_http(exc) from exc

        except Exception:
            self.session.rollback()
            raise

    def delete_knowledge_base(
        self,
        kb_key: str,
        delete_qdrant_collections: bool = True,
    ) -> dict[str, Any]:
        normalized_key = self._normalize_kb_key(kb_key)

        knowledge_base = self.repository.get_by_key(normalized_key)
        if knowledge_base is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Knowledge base not found: {normalized_key}",
            )

        indexes = self.index_repository.list(kb_key=normalized_key)

        building_indexes = [
            index
            for index in indexes
            if index.status == "building"
        ]

        if building_indexes:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Knowledge base has building indexes and cannot be deleted: "
                    f"{normalized_key}"
                ),
            )

        qdrant_collections: list[dict[str, Any]] = []

        if delete_qdrant_collections:
            for index in indexes:
                result = self._delete_qdrant_collection_best_effort(
                    collection_name=index.qdrant_collection_name,
                )
                qdrant_collections.append(result)

        try:
            deleted = self.repository.delete_by_key(normalized_key)
            self.session.commit()

        except Exception:
            self.session.rollback()
            raise

        return {
            "kb_key": normalized_key,
            "deleted": deleted,
            "indexes_count": len(indexes),
            "qdrant_collections": qdrant_collections,
        }

    def _normalize_create_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "kb_key": self._normalize_kb_key(data.get("kb_key")),
            "display_name": self._normalize_required_text(
                data.get("display_name"),
                field_name="display_name",
            ),
            "source_type": self._normalize_source_type(
                data.get("source_type") or "local_json",
            ),
            "source_path": self._normalize_optional_text(data.get("source_path")),
            "default_top_k": self._normalize_positive_int(
                data.get("default_top_k", 6),
                field_name="default_top_k",
            ),
            "default_max_context_chars": self._normalize_positive_int(
                data.get("default_max_context_chars", 10000),
                field_name="default_max_context_chars",
            ),
        }

    def _normalize_update_data(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}

        if "display_name" in data:
            normalized["display_name"] = self._normalize_required_text(
                data.get("display_name"),
                field_name="display_name",
            )

        if "source_type" in data:
            normalized["source_type"] = self._normalize_source_type(
                data.get("source_type"),
            )

        if "source_path" in data:
            normalized["source_path"] = self._normalize_optional_text(
                data.get("source_path"),
            )

        if "default_top_k" in data:
            normalized["default_top_k"] = self._normalize_positive_int(
                data.get("default_top_k"),
                field_name="default_top_k",
            )

        if "default_max_context_chars" in data:
            normalized["default_max_context_chars"] = self._normalize_positive_int(
                data.get("default_max_context_chars"),
                field_name="default_max_context_chars",
            )

        return normalized

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

    def _normalize_source_type(self, value: Any) -> str:
        source_type = str(value or "").strip()

        if source_type not in ALLOWED_SOURCE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "source_type must be one of: "
                    + ", ".join(sorted(ALLOWED_SOURCE_TYPES))
                ),
            )

        return source_type

    def _normalize_required_text(
        self,
        value: Any,
        field_name: str,
    ) -> str:
        text = str(value or "").strip()

        if not text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field_name} is required",
            )

        return text

    def _normalize_optional_text(self, value: Any) -> str | None:
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    def _normalize_positive_int(
        self,
        value: Any,
        field_name: str,
    ) -> int:
        try:
            int_value = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field_name} must be a positive integer",
            ) from exc

        if int_value <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field_name} must be greater than 0",
            )

        return int_value

    def _integrity_error_to_http(self, error: IntegrityError) -> HTTPException:
        message = str(error.orig) if getattr(error, "orig", None) else str(error)

        if "knowledge_bases_kb_key_key" in message or "kb_key" in message:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Knowledge base with this kb_key already exists",
            )

        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Knowledge base constraint violation: {message[:1000]}",
        )

    def _delete_qdrant_collection_best_effort(
        self,
        collection_name: str,
    ) -> dict[str, Any]:
        result = {
            "collection_name": collection_name,
            "deleted": False,
            "error": None,
        }

        qdrant_url = (self.settings.qdrant_url or "").strip()
        qdrant_local_path = (self.settings.qdrant_local_path or "").strip()

        if not qdrant_url and not qdrant_local_path:
            result["error"] = "Qdrant is not configured"
            return result

        try:
            writer = QdrantKnowledgeBaseVectorWriter(
                collection_name=collection_name,
                url=self.settings.qdrant_url or None,
                api_key=self.settings.qdrant_api_key or None,
                location=self.settings.qdrant_local_path or None,
                prefer_grpc=self.settings.qdrant_prefer_grpc,
            )
            writer.delete_collection()
            result["deleted"] = True

        except Exception as exc:
            logger.exception(
                "Failed to delete Qdrant collection for knowledge base: collection=%s",
                collection_name,
            )
            result["error"] = str(exc)[:1000] or exc.__class__.__name__

        return result
