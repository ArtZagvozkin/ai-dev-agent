import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.components.code_search.models import CodeChunk


class CodeChunkRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_many(
        self,
        file_id: UUID,
        chunks: list[CodeChunk],
    ) -> list[UUID]:
        chunk_ids: list[UUID] = []

        for chunk in chunks:
            chunk_id = self.session.execute(
                text(
                    """
                    INSERT INTO agent.code_chunks (
                        file_id,
                        chunk_id,
                        parent_chunk_id,
                        chunk_type,
                        start_line,
                        end_line,
                        symbol,
                        ast_node_type,
                        declaration_type,
                        parent_symbol,
                        content,
                        contextualized_text,
                        code_unit,
                        keywords,
                        imports,
                        referenced_symbols,
                        top_level_symbols
                    )
                    VALUES (
                        :file_id,
                        :chunk_id,
                        :parent_chunk_id,
                        :chunk_type,
                        :start_line,
                        :end_line,
                        :symbol,
                        :ast_node_type,
                        :declaration_type,
                        :parent_symbol,
                        :content,
                        :contextualized_text,
                        :code_unit,
                        CAST(:keywords AS jsonb),
                        CAST(:imports AS jsonb),
                        CAST(:referenced_symbols AS jsonb),
                        CAST(:top_level_symbols AS jsonb)
                    )
                    RETURNING id
                    """
                ),
                {
                    "file_id": file_id,
                    "chunk_id": chunk.chunk_id,
                    "parent_chunk_id": chunk.parent_chunk_id,
                    "chunk_type": chunk.chunk_type,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "symbol": chunk.symbol,
                    "ast_node_type": chunk.ast_node_type,
                    "declaration_type": chunk.declaration_type,
                    "parent_symbol": chunk.parent_symbol,
                    "content": chunk.content,
                    "contextualized_text": chunk.contextualized_text,
                    "code_unit": chunk.code_unit,
                    "keywords": json.dumps(chunk.keywords or [], ensure_ascii=False),
                    "imports": json.dumps(chunk.imports or [], ensure_ascii=False),
                    "referenced_symbols": json.dumps(
                        chunk.references or [],
                        ensure_ascii=False,
                    ),
                    "top_level_symbols": json.dumps(
                        chunk.top_level_symbols or [],
                        ensure_ascii=False,
                    ),
                },
            ).scalar_one()

            chunk_ids.append(chunk_id)

        return chunk_ids
