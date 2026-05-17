import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
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

    def list_by_index(
        self,
        index_id: UUID,
    ) -> list[tuple[UUID, CodeChunk]]:
        rows = self.session.execute(
            text(
                """
                SELECT
                    c.id AS db_id,
                    c.chunk_id,
                    c.parent_chunk_id,
                    c.chunk_type,
                    f.path,
                    f.language,
                    c.start_line,
                    c.end_line,
                    c.symbol,
                    c.ast_node_type,
                    c.declaration_type,
                    c.parent_symbol,
                    c.content,
                    c.contextualized_text,
                    c.code_unit,
                    c.keywords,
                    c.imports,
                    c.referenced_symbols,
                    c.top_level_symbols
                FROM agent.code_chunks c
                JOIN agent.codebase_files f ON f.id = c.file_id
                WHERE f.index_id = :index_id
                ORDER BY
                    f.path,
                    c.start_line,
                    c.end_line,
                    c.chunk_type,
                    c.chunk_id
                """
            ),
            {"index_id": index_id},
        ).mappings()

        return [
            (
                row["db_id"],
                self._map_chunk_row(row),
            )
            for row in rows
        ]

    def _map_chunk_row(self, row: RowMapping) -> CodeChunk:
        return CodeChunk(
            chunk_id=row["chunk_id"],
            parent_chunk_id=row["parent_chunk_id"],
            chunk_type=row["chunk_type"],
            path=row["path"],
            language=row["language"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            content=row["content"],
            contextualized_text=row["contextualized_text"],
            symbol=row["symbol"],
            ast_node_type=row["ast_node_type"],
            declaration_type=row["declaration_type"],
            parent_symbol=row["parent_symbol"],
            keywords=self._json_list(row["keywords"]),
            imports=self._json_list(row["imports"]),
            references=self._json_list(row["referenced_symbols"]),
            top_level_symbols=self._json_list(row["top_level_symbols"]),
            code_unit=row["code_unit"],
        )

    def _json_list(self, value) -> list[str]:
        if value is None:
            return []

        if isinstance(value, list):
            return [str(item) for item in value if item is not None]

        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return []

            if isinstance(loaded, list):
                return [str(item) for item in loaded if item is not None]

            return []

        try:
            return [str(item) for item in value if item is not None]
        except TypeError:
            return []
