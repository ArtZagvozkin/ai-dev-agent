from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domain.codebase_indexes import CodebaseIndex


INDEX_SELECT_COLUMNS = """
    i.id,
    i.project_id,
    p.project_key,
    i.status,
    i.index_schema_version,
    i.embedding_provider,
    i.embedding_model,
    i.embedding_dimensions,
    i.qdrant_collection_name,
    i.files_count,
    i.chunks_count,
    i.build_started_at,
    i.build_finished_at,
    i.error_message,
    (p.current_index_id = i.id) AS is_current
"""


class CodebaseIndexRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(
        self,
        project_key: str | None = None,
        status: str | None = None,
    ) -> list[CodebaseIndex]:
        conditions = []
        params: dict[str, Any] = {}

        if project_key:
            conditions.append("p.project_key = :project_key")
            params["project_key"] = project_key

        if status:
            conditions.append("i.status = :status")
            params["status"] = status

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        rows = self.session.execute(
            text(
                f"""
                SELECT
                    {INDEX_SELECT_COLUMNS}
                FROM agent.codebase_indexes i
                JOIN agent.code_projects p ON p.id = i.project_id
                {where_clause}
                ORDER BY i.build_started_at DESC NULLS LAST, i.id DESC
                """
            ),
            params,
        ).mappings()

        return [self._map_row(row) for row in rows]

    def get_by_id(self, index_id: UUID) -> CodebaseIndex | None:
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT
                        {INDEX_SELECT_COLUMNS}
                    FROM agent.codebase_indexes i
                    JOIN agent.code_projects p ON p.id = i.project_id
                    WHERE i.id = :index_id
                    """
                ),
                {"index_id": index_id},
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def delete_by_id(self, index_id: UUID) -> bool:
        result = self.session.execute(
            text(
                """
                DELETE FROM agent.codebase_indexes
                WHERE id = :index_id
                """
            ),
            {"index_id": index_id},
        )

        return result.rowcount > 0

    def _map_row(self, row: RowMapping) -> CodebaseIndex:
        return CodebaseIndex(
            id=row["id"],
            project_id=row["project_id"],
            project_key=row["project_key"],
            status=row["status"],
            index_schema_version=row["index_schema_version"],
            embedding_provider=row["embedding_provider"],
            embedding_model=row["embedding_model"],
            embedding_dimensions=row["embedding_dimensions"],
            qdrant_collection_name=row["qdrant_collection_name"],
            files_count=row["files_count"],
            chunks_count=row["chunks_count"],
            build_started_at=row["build_started_at"],
            build_finished_at=row["build_finished_at"],
            error_message=row["error_message"],
            is_current=row["is_current"],
        )
