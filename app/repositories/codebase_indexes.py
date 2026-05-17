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

    def has_building_index(self, project_id: UUID) -> bool:
        row = self.session.execute(
            text(
                """
                SELECT 1
                FROM agent.codebase_indexes
                WHERE project_id = :project_id
                  AND status = 'building'
                LIMIT 1
                """
            ),
            {"project_id": project_id},
        ).first()

        return row is not None

    def create_building(
        self,
        index_id: UUID,
        project_id: UUID,
        index_schema_version: str,
        embedding_provider: str,
        embedding_model: str,
        embedding_dimensions: int,
        qdrant_collection_name: str,
    ) -> CodebaseIndex:
        self.session.execute(
            text(
                """
                INSERT INTO agent.codebase_indexes (
                    id,
                    project_id,
                    status,
                    index_schema_version,
                    embedding_provider,
                    embedding_model,
                    embedding_dimensions,
                    qdrant_collection_name,
                    build_started_at
                )
                VALUES (
                    :id,
                    :project_id,
                    'building',
                    :index_schema_version,
                    :embedding_provider,
                    :embedding_model,
                    :embedding_dimensions,
                    :qdrant_collection_name,
                    now()
                )
                """
            ),
            {
                "id": index_id,
                "project_id": project_id,
                "index_schema_version": index_schema_version,
                "embedding_provider": embedding_provider,
                "embedding_model": embedding_model,
                "embedding_dimensions": embedding_dimensions,
                "qdrant_collection_name": qdrant_collection_name,
            },
        )

        index = self.get_by_id(index_id)
        assert index is not None
        return index

    def update_counts(
        self,
        index_id: UUID,
        files_count: int,
        chunks_count: int,
    ) -> None:
        self.session.execute(
            text(
                """
                UPDATE agent.codebase_indexes
                SET
                    files_count = :files_count,
                    chunks_count = :chunks_count
                WHERE id = :index_id
                """
            ),
            {
                "index_id": index_id,
                "files_count": files_count,
                "chunks_count": chunks_count,
            },
        )

    def mark_ready(
        self,
        index_id: UUID,
        files_count: int,
        chunks_count: int,
    ) -> CodebaseIndex:
        self.session.execute(
            text(
                """
                UPDATE agent.codebase_indexes
                SET
                    status = 'ready',
                    files_count = :files_count,
                    chunks_count = :chunks_count,
                    build_finished_at = now(),
                    error_message = NULL
                WHERE id = :index_id
                """
            ),
            {
                "index_id": index_id,
                "files_count": files_count,
                "chunks_count": chunks_count,
            },
        )

        index = self.get_by_id(index_id)
        assert index is not None
        return index

    def mark_failed(self, index_id: UUID, error_message: str) -> None:
        self.session.execute(
            text(
                """
                UPDATE agent.codebase_indexes
                SET
                    status = 'failed',
                    build_finished_at = now(),
                    error_message = :error_message
                WHERE id = :index_id
                """
            ),
            {
                "index_id": index_id,
                "error_message": error_message[:4000],
            },
        )

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
