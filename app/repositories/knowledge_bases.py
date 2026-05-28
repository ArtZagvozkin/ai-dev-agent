from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domain.knowledge_bases import KnowledgeBase


KNOWLEDGE_BASE_SELECT_COLUMNS = """
    id,
    kb_key,
    display_name,
    source_type,
    source_path,
    default_top_k,
    default_max_context_chars,
    current_index_id
"""


class KnowledgeBaseRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self) -> list[KnowledgeBase]:
        rows = self.session.execute(
            text(
                f"""
                SELECT
                    {KNOWLEDGE_BASE_SELECT_COLUMNS}
                FROM agent.knowledge_bases
                ORDER BY kb_key
                """
            )
        ).mappings()

        return [self._map_row(row) for row in rows]

    def get_by_id(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT
                        {KNOWLEDGE_BASE_SELECT_COLUMNS}
                    FROM agent.knowledge_bases
                    WHERE id = :knowledge_base_id
                    """
                ),
                {"knowledge_base_id": knowledge_base_id},
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def get_by_key(self, kb_key: str) -> KnowledgeBase | None:
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT
                        {KNOWLEDGE_BASE_SELECT_COLUMNS}
                    FROM agent.knowledge_bases
                    WHERE kb_key = :kb_key
                    """
                ),
                {"kb_key": kb_key},
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def get_by_key_for_update(self, kb_key: str) -> KnowledgeBase | None:
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT
                        {KNOWLEDGE_BASE_SELECT_COLUMNS}
                    FROM agent.knowledge_bases
                    WHERE kb_key = :kb_key
                    FOR UPDATE
                    """
                ),
                {"kb_key": kb_key},
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def create(self, data: dict[str, Any]) -> KnowledgeBase:
        row = (
            self.session.execute(
                text(
                    f"""
                    INSERT INTO agent.knowledge_bases (
                        kb_key,
                        display_name,
                        source_type,
                        source_path,
                        default_top_k,
                        default_max_context_chars
                    )
                    VALUES (
                        :kb_key,
                        :display_name,
                        :source_type,
                        :source_path,
                        :default_top_k,
                        :default_max_context_chars
                    )
                    RETURNING
                        {KNOWLEDGE_BASE_SELECT_COLUMNS}
                    """
                ),
                data,
            )
            .mappings()
            .one()
        )

        return self._map_row(row)

    def create_if_missing(
        self,
        kb_key: str,
        display_name: str,
        source_type: str,
        source_path: str,
        default_top_k: int = 6,
        default_max_context_chars: int = 10000,
    ) -> KnowledgeBase:
        existing = self.get_by_key(kb_key)
        if existing:
            return existing

        return self.create(
            {
                "kb_key": kb_key,
                "display_name": display_name,
                "source_type": source_type,
                "source_path": source_path,
                "default_top_k": default_top_k,
                "default_max_context_chars": default_max_context_chars,
            }
        )

    def update_by_key(
        self,
        kb_key: str,
        data: dict[str, Any],
    ) -> KnowledgeBase | None:
        if not data:
            return self.get_by_key(kb_key)

        allowed_fields = {
            "display_name",
            "source_type",
            "source_path",
            "default_top_k",
            "default_max_context_chars",
        }

        update_data = {
            key: value
            for key, value in data.items()
            if key in allowed_fields
        }

        if not update_data:
            return self.get_by_key(kb_key)

        set_clause = ", ".join(
            f"{field} = :{field}"
            for field in update_data
        )

        params = {
            **update_data,
            "kb_key": kb_key,
        }

        row = (
            self.session.execute(
                text(
                    f"""
                    UPDATE agent.knowledge_bases
                    SET
                        {set_clause},
                        updated_at = now()
                    WHERE kb_key = :kb_key
                    RETURNING
                        {KNOWLEDGE_BASE_SELECT_COLUMNS}
                    """
                ),
                params,
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def update_source_path(
        self,
        kb_key: str,
        source_path: str,
    ) -> KnowledgeBase | None:
        row = (
            self.session.execute(
                text(
                    f"""
                    UPDATE agent.knowledge_bases
                    SET
                        source_path = :source_path,
                        updated_at = now()
                    WHERE kb_key = :kb_key
                    RETURNING
                        {KNOWLEDGE_BASE_SELECT_COLUMNS}
                    """
                ),
                {
                    "kb_key": kb_key,
                    "source_path": source_path,
                },
            )
            .mappings()
            .first()
        )

        return self._map_row(row) if row else None

    def set_current_index_if_empty(
        self,
        knowledge_base_id: UUID,
        index_id: UUID,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE agent.knowledge_bases
                SET
                    current_index_id = :index_id,
                    updated_at = now()
                WHERE id = :knowledge_base_id
                  AND current_index_id IS NULL
                """
            ),
            {
                "knowledge_base_id": knowledge_base_id,
                "index_id": index_id,
            },
        )

        return result.rowcount > 0

    def set_current_index_if_matches(
        self,
        knowledge_base_id: UUID,
        expected_current_index_id: UUID,
        new_index_id: UUID,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE agent.knowledge_bases
                SET
                    current_index_id = :new_index_id,
                    updated_at = now()
                WHERE id = :knowledge_base_id
                  AND current_index_id = :expected_current_index_id
                """
            ),
            {
                "knowledge_base_id": knowledge_base_id,
                "expected_current_index_id": expected_current_index_id,
                "new_index_id": new_index_id,
            },
        )

        return result.rowcount > 0

    def set_current_index(
        self,
        knowledge_base_id: UUID,
        index_id: UUID,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE agent.knowledge_bases
                SET
                    current_index_id = :index_id,
                    updated_at = now()
                WHERE id = :knowledge_base_id
                """
            ),
            {
                "knowledge_base_id": knowledge_base_id,
                "index_id": index_id,
            },
        )

        return result.rowcount > 0

    def clear_current_index(
        self,
        knowledge_base_id: UUID,
    ) -> bool:
        result = self.session.execute(
            text(
                """
                UPDATE agent.knowledge_bases
                SET
                    current_index_id = NULL,
                    updated_at = now()
                WHERE id = :knowledge_base_id
                """
            ),
            {
                "knowledge_base_id": knowledge_base_id,
            },
        )

        return result.rowcount > 0

    def delete_by_key(self, kb_key: str) -> bool:
        result = self.session.execute(
            text(
                """
                DELETE FROM agent.knowledge_bases
                WHERE kb_key = :kb_key
                """
            ),
            {"kb_key": kb_key},
        )

        return result.rowcount > 0

    def _map_row(self, row: RowMapping) -> KnowledgeBase:
        return KnowledgeBase(
            id=row["id"],
            kb_key=row["kb_key"],
            display_name=row["display_name"],
            source_type=row["source_type"],
            source_path=row["source_path"],
            default_top_k=row["default_top_k"],
            default_max_context_chars=row["default_max_context_chars"],
            current_index_id=row["current_index_id"],
        )
