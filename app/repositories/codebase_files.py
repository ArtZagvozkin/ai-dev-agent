from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class CodebaseFileRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        index_id: UUID,
        path: str,
        language: str,
        content_hash: str,
        size_bytes: int,
        chunks_count: int,
    ) -> UUID:
        return self.session.execute(
            text(
                """
                INSERT INTO agent.codebase_files (
                    index_id,
                    path,
                    language,
                    content_hash,
                    size_bytes,
                    chunks_count
                )
                VALUES (
                    :index_id,
                    :path,
                    :language,
                    :content_hash,
                    :size_bytes,
                    :chunks_count
                )
                RETURNING id
                """
            ),
            {
                "index_id": index_id,
                "path": path,
                "language": language,
                "content_hash": content_hash,
                "size_bytes": size_bytes,
                "chunks_count": chunks_count,
            },
        ).scalar_one()
