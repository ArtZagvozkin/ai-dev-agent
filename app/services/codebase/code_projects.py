from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.code_projects import CodeProjectRepository
from app.repositories.codebase_indexes import CodebaseIndexRepository
from app.schemas.code_projects import (
    CodeProjectCreateRequest,
    CodeProjectUpdateRequest,
)


NON_NULL_UPDATE_FIELDS = {
    "display_name",
    "gitlab_project",
    "default_branch",
    "local_repository_path",
    "default_max_files",
    "default_max_file_bytes",
    "default_top_k",
    "default_include_full_code_units",
}


class CodeProjectService:
    def __init__(
        self,
        repository: CodeProjectRepository,
        index_repository: CodebaseIndexRepository,
        session: Session,
    ):
        self.repository = repository
        self.index_repository = index_repository
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

        self._validate_update_payload(payload)

        try:
            project = self.repository.update_by_key(project_key, payload)

            if project is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Code project not found: {project_key}",
                )

            self.session.commit()
            return project
        except HTTPException:
            self.session.rollback()
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
        try:
            project = self.repository.get_by_key_for_update(project_key)

            if project is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Code project not found: {project_key}",
                )

            if project.current_index_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Code project has current index and cannot be deleted: {project_key}",
                )

            deleted = self.repository.delete_by_key(project_key)

            if not deleted:
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
            self.session.rollback()
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

    def list_project_indexes(self, project_key: str):
        self.get_project(project_key)
        return self.index_repository.list(project_key=project_key)

    def detach_current_index(self, project_key: str):
        try:
            project = self.repository.get_by_key_for_update(project_key)

            if project is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Code project not found: {project_key}",
                )

            if project.current_index_id is None:
                self.session.rollback()
                return project

            cleared = self.repository.clear_current_index(project.id)

            if not cleared:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Code project not found: {project_key}",
                )

            self.session.commit()

            refreshed = self.repository.get_by_key(project_key)
            return refreshed or project
        except HTTPException:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

    def switch_current_index(self, project_key: str, index_id: UUID):
        try:
            project = self.repository.get_by_key_for_update(project_key)

            if project is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Code project not found: {project_key}",
                )

            index = self.index_repository.get_by_id(index_id)

            if index is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Codebase index not found: {index_id}",
                )

            if index.project_id != project.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Codebase index belongs to another project: "
                        f"project_key={project_key}, index_id={index_id}"
                    ),
                )

            if index.status != "ready":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Only ready codebase index can be attached: "
                        f"index_id={index_id}, status={index.status}"
                    ),
                )

            if index.chunks_count <= 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Codebase index has no chunks and cannot be attached: {index_id}",
                )

            updated = self.repository.set_current_index(
                project_id=project.id,
                index_id=index.id,
            )

            if not updated:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Code project not found: {project_key}",
                )

            self.session.commit()

            refreshed = self.index_repository.get_by_id(index_id)
            return refreshed or index
        except HTTPException:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

    def _validate_update_payload(self, payload: dict):
        for field in NON_NULL_UPDATE_FIELDS:
            if field in payload and payload[field] is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field cannot be null: {field}",
                )
