from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.code_projects import CodeProjectRepository
from app.schemas.code_projects import (
    CodeProjectCreateRequest,
    CodeProjectUpdateRequest,
)


class CodeProjectService:
    def __init__(self, repository: CodeProjectRepository, session: Session):
        self.repository = repository
        self.session = session

    def list_projects(self):
        return self.repository.list()

    def get_project(self, project_key: str):
        project = self.repository.get_by_key(project_key)

        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Code project not found: {project_key}",
            )

        return project

    def create_project(self, data: CodeProjectCreateRequest):
        existing = self.repository.get_by_key(data.project_key)

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code project already exists: {data.project_key}",
            )

        try:
            project = self.repository.create(data.model_dump())
            self.session.commit()
            return project
        except IntegrityError as exc:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code project already exists or violates DB constraints: {data.project_key}",
            ) from exc
        except Exception:
            self.session.rollback()
            raise

    def update_project(self, project_key: str, data: CodeProjectUpdateRequest):
        payload = data.model_dump(exclude_unset=True)

        if not payload:
            return self.get_project(project_key)

        try:
            project = self.repository.update_by_key(project_key, payload)

            if project is None:
                self.session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Code project not found: {project_key}",
                )

            self.session.commit()
            return project
        except HTTPException:
            raise
        except IntegrityError as exc:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Code project update violates DB constraints: {project_key}",
            ) from exc
        except Exception:
            self.session.rollback()
            raise

    def delete_project(self, project_key: str):
        project = self.get_project(project_key)

        if project.current_index_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code project has current index and cannot be deleted: {project_key}",
            )

        try:
            deleted = self.repository.delete_by_key(project_key)

            if not deleted:
                self.session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Code project not found: {project_key}",
                )

            self.session.commit()

            return {
                "deleted": True,
                "project_key": project_key,
            }
        except HTTPException:
            raise
        except IntegrityError as exc:
            self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code project cannot be deleted because it is referenced: {project_key}",
            ) from exc
        except Exception:
            self.session.rollback()
            raise
