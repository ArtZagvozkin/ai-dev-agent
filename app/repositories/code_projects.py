from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domain.code_projects import CodeProject


PROJECT_SELECT_COLUMNS = """
    id,
    project_key,
    display_name,
    gitlab_project,
    default_branch,
    local_repository_path,
    review_context_path,
    consultation_context_path,
    default_max_files,
    default_max_file_bytes,
    default_top_k,
    default_include_full_code_units,
    current_index_id
"""


class CodeProjectRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self) -> list[CodeProject]:
        rows = self.session.execute(
            text(
                f"""
                SELECT
                    {PROJECT_SELECT_COLUMNS}
                FROM agent.code_projects
                ORDER BY project_key
                """
            )
        ).mappings()

        return [self._map_row(row) for row in rows]

    def get_by_key(self, project_key: str) -> CodeProject | None:
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT
                        {PROJECT_SELECT_COLUMNS}
                    FROM agent.code_projects
                    WHERE project_key = :project_key
                    """
                ),
                {"project_key": project_key},
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def get_by_key_for_update(self, project_key: str) -> CodeProject | None:
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT
                        {PROJECT_SELECT_COLUMNS}
                    FROM agent.code_projects
                    WHERE project_key = :project_key
                    FOR UPDATE
                    """
                ),
                {"project_key": project_key},
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def get_by_id(self, project_id: UUID) -> CodeProject | None:
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT
                        {PROJECT_SELECT_COLUMNS}
                    FROM agent.code_projects
                    WHERE id = :project_id
                    """
                ),
                {"project_id": project_id},
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def create(self, data: dict[str, Any]) -> CodeProject:
        row = (
            self.session.execute(
                text(
                    f"""
                    INSERT INTO agent.code_projects (
                        project_key,
                        display_name,
                        gitlab_project,
                        default_branch,
                        local_repository_path,
                        review_context_path,
                        consultation_context_path,
                        default_max_files,
                        default_max_file_bytes,
                        default_top_k,
                        default_include_full_code_units
                    )
                    VALUES (
                        :project_key,
                        :display_name,
                        :gitlab_project,
                        :default_branch,
                        :local_repository_path,
                        :review_context_path,
                        :consultation_context_path,
                        :default_max_files,
                        :default_max_file_bytes,
                        :default_top_k,
                        :default_include_full_code_units
                    )
                    RETURNING
                        {PROJECT_SELECT_COLUMNS}
                    """
                ),
                data,
            )
            .mappings()
            .one()
        )

        return self._map_row(row)

    def update_by_key(self, project_key: str, data: dict[str, Any]) -> CodeProject | None:
        if not data:
            return self.get_by_key(project_key)

        allowed_fields = {
            "display_name",
            "gitlab_project",
            "default_branch",
            "local_repository_path",
            "review_context_path",
            "consultation_context_path",
            "default_max_files",
            "default_max_file_bytes",
            "default_top_k",
            "default_include_full_code_units",
        }

        update_data = {
            key: value
            for key, value in data.items()
            if key in allowed_fields
        }

        if not update_data:
            return self.get_by_key(project_key)

        set_clause = ", ".join(
            f"{field} = :{field}"
            for field in update_data
        )

        params = {
            **update_data,
            "project_key": project_key,
        }

        row = (
            self.session.execute(
                text(
                    f"""
                    UPDATE agent.code_projects
                    SET {set_clause}
                    WHERE project_key = :project_key
                    RETURNING
                        {PROJECT_SELECT_COLUMNS}
                    """
                ),
                params,
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def set_current_index_if_empty(
        self,
        project_id: UUID,
        index_id: UUID,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE agent.code_projects
                SET current_index_id = :index_id
                WHERE id = :project_id
                  AND current_index_id IS NULL
                """
            ),
            {
                "project_id": project_id,
                "index_id": index_id,
            },
        )

        return result.rowcount > 0

    def delete_by_key(self, project_key: str) -> bool:
        result = self.session.execute(
            text(
                """
                DELETE FROM agent.code_projects
                WHERE project_key = :project_key
                """
            ),
            {"project_key": project_key},
        )

        return result.rowcount > 0

    def _map_row(self, row: RowMapping) -> CodeProject:
        return CodeProject(
            id=row["id"],
            project_key=row["project_key"],
            display_name=row["display_name"],
            gitlab_project=row["gitlab_project"],
            default_branch=row["default_branch"],
            local_repository_path=row["local_repository_path"],
            review_context_path=row["review_context_path"],
            consultation_context_path=row["consultation_context_path"],
            default_max_files=row["default_max_files"],
            default_max_file_bytes=row["default_max_file_bytes"],
            default_top_k=row["default_top_k"],
            default_include_full_code_units=row["default_include_full_code_units"],
            current_index_id=row["current_index_id"],
        )
