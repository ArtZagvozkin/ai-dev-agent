from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.codebase_indexes import CodebaseIndexRepository


ALLOWED_INDEX_STATUSES = {"building", "ready", "failed", "archived"}


class CodebaseIndexService:
    def __init__(self, repository: CodebaseIndexRepository, session: Session):
        self.repository = repository
        self.session = session

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
        try:
            index = self.get_index(index_id)

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

            return {
                "deleted": True,
                "index_id": index_id,
            }
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
