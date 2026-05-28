import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.components.knowledge_base.chunker import KnowledgeBaseChunkDraft
from app.domain.knowledge_base_chunks import KnowledgeBaseChunk


class KnowledgeBaseDocumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_document(
        self,
        index_id: UUID,
        external_id: str,
        source_url: str | None,
        title: str | None,
        full_title: str | None,
        source_revision: str | None,
        updated_at_src: str | None,
        content_hash: str,
        content: str,
        chunks_count: int,
        metadata: dict | None = None,
    ) -> UUID:
        return self.session.execute(
            text(
                """
                INSERT INTO agent.knowledge_base_documents (
                    index_id,
                    external_id,
                    source_url,
                    title,
                    full_title,
                    source_revision,
                    updated_at_src,
                    content_hash,
                    content,
                    chunks_count,
                    metadata
                )
                VALUES (
                    :index_id,
                    :external_id,
                    :source_url,
                    :title,
                    :full_title,
                    :source_revision,
                    :updated_at_src,
                    :content_hash,
                    :content,
                    :chunks_count,
                    CAST(:metadata AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "index_id": index_id,
                "external_id": external_id,
                "source_url": source_url,
                "title": title,
                "full_title": full_title,
                "source_revision": source_revision,
                "updated_at_src": updated_at_src,
                "content_hash": content_hash,
                "content": content,
                "chunks_count": chunks_count,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        ).scalar_one()

    def create_chunks(
        self,
        document_id: UUID,
        chunks: list[KnowledgeBaseChunkDraft],
    ) -> list[UUID]:
        chunk_db_ids: list[UUID] = []

        for chunk in chunks:
            chunk_db_id = self.session.execute(
                text(
                    """
                    INSERT INTO agent.knowledge_base_chunks (
                        document_id,
                        chunk_id,
                        chunk_ord,
                        section_path,
                        block_types,
                        text,
                        contextualized_text,
                        char_len,
                        chunk_hash,
                        metadata
                    )
                    VALUES (
                        :document_id,
                        :chunk_id,
                        :chunk_ord,
                        CAST(:section_path AS jsonb),
                        CAST(:block_types AS jsonb),
                        :text,
                        :contextualized_text,
                        :char_len,
                        :chunk_hash,
                        CAST(:metadata AS jsonb)
                    )
                    RETURNING id
                    """
                ),
                {
                    "document_id": document_id,
                    "chunk_id": chunk.chunk_id,
                    "chunk_ord": chunk.chunk_ord,
                    "section_path": json.dumps(chunk.section_path or [], ensure_ascii=False),
                    "block_types": json.dumps(chunk.block_types or [], ensure_ascii=False),
                    "text": chunk.text,
                    "contextualized_text": chunk.contextualized_text,
                    "char_len": chunk.char_len,
                    "chunk_hash": chunk.chunk_hash,
                    "metadata": json.dumps(chunk.metadata or {}, ensure_ascii=False),
                },
            ).scalar_one()

            chunk_db_ids.append(chunk_db_id)

        return chunk_db_ids

    def create_images_and_links(
        self,
        document_id: UUID,
        chunks: list[KnowledgeBaseChunkDraft],
        chunk_db_ids: list[UUID],
    ) -> None:
        if len(chunks) != len(chunk_db_ids):
            raise ValueError(
                "chunks/chunk_db_ids count mismatch: "
                f"chunks={len(chunks)}, chunk_db_ids={len(chunk_db_ids)}"
            )

        for chunk, chunk_db_id in zip(chunks, chunk_db_ids):
            for ord_num, image in enumerate(chunk.images or [], start=1):
                local_path = (image.get("local_path") or "").strip()
                if not local_path:
                    continue

                image_id = self._upsert_image(
                    document_id=document_id,
                    image=image,
                    local_path=local_path,
                )

                self.session.execute(
                    text(
                        """
                        INSERT INTO agent.knowledge_base_chunk_images (
                            chunk_id,
                            image_id,
                            ord
                        )
                        VALUES (
                            :chunk_id,
                            :image_id,
                            :ord
                        )
                        """
                    ),
                    {
                        "chunk_id": chunk_db_id,
                        "image_id": image_id,
                        "ord": ord_num,
                    },
                )

    def list_chunks_by_index(
        self,
        index_id: UUID,
    ) -> list[KnowledgeBaseChunk]:
        rows = self.session.execute(
            text(
                """
                SELECT
                    c.id AS db_id,
                    c.document_id,
                    d.index_id,
                    c.chunk_id,
                    c.chunk_ord,
                    c.section_path,
                    c.block_types,
                    c.text,
                    c.contextualized_text,
                    c.char_len,
                    c.chunk_hash,
                    c.metadata,
                    d.external_id,
                    d.source_url,
                    d.title,
                    d.full_title,
                    d.source_revision,
                    d.updated_at_src
                FROM agent.knowledge_base_chunks c
                JOIN agent.knowledge_base_documents d ON d.id = c.document_id
                WHERE d.index_id = :index_id
                ORDER BY
                    d.external_id,
                    c.chunk_ord,
                    c.id
                """
            ),
            {
                "index_id": index_id,
            },
        ).mappings()

        return [
            self._map_chunk_row(row)
            for row in rows
        ]

    def _upsert_image(
        self,
        document_id: UUID,
        image: dict,
        local_path: str,
    ) -> UUID:
        existing_id = self.session.execute(
            text(
                """
                SELECT id
                FROM agent.knowledge_base_images
                WHERE document_id = :document_id
                  AND local_path = :local_path
                LIMIT 1
                """
            ),
            {
                "document_id": document_id,
                "local_path": local_path,
            },
        ).scalar_one_or_none()

        metadata = {
            key: value
            for key, value in image.items()
            if key not in {"local_path", "source_url", "original_url", "alt", "caption"}
        }

        params = {
            "document_id": document_id,
            "local_path": local_path,
            "source_url": image.get("source_url") or image.get("original_url"),
            "alt": image.get("alt"),
            "caption": image.get("caption"),
            "metadata": json.dumps(metadata, ensure_ascii=False),
        }

        if existing_id is not None:
            self.session.execute(
                text(
                    """
                    UPDATE agent.knowledge_base_images
                    SET
                        source_url = :source_url,
                        alt = :alt,
                        caption = :caption,
                        metadata = CAST(:metadata AS jsonb)
                    WHERE id = :image_id
                    """
                ),
                {
                    **params,
                    "image_id": existing_id,
                },
            )

            return existing_id

        return self.session.execute(
            text(
                """
                INSERT INTO agent.knowledge_base_images (
                    document_id,
                    local_path,
                    source_url,
                    alt,
                    caption,
                    metadata
                )
                VALUES (
                    :document_id,
                    :local_path,
                    :source_url,
                    :alt,
                    :caption,
                    CAST(:metadata AS jsonb)
                )
                RETURNING id
                """
            ),
            params,
        ).scalar_one()

    def _map_chunk_row(self, row: RowMapping) -> KnowledgeBaseChunk:
        return KnowledgeBaseChunk(
            db_id=row["db_id"],
            document_id=row["document_id"],
            index_id=row["index_id"],
            chunk_id=row["chunk_id"],
            chunk_ord=row["chunk_ord"],
            section_path=self._json_list(row["section_path"]),
            block_types=self._json_list(row["block_types"]),
            text=row["text"],
            contextualized_text=row["contextualized_text"],
            char_len=row["char_len"],
            chunk_hash=row["chunk_hash"],
            metadata=self._json_dict(row["metadata"]),
            external_id=row["external_id"],
            source_url=row["source_url"],
            title=row["title"],
            full_title=row["full_title"],
            source_revision=row["source_revision"],
            updated_at_src=row["updated_at_src"],
        )

    def _json_list(self, value) -> list[str]:
        if value is None:
            return []

        if isinstance(value, list):
            return [
                str(item)
                for item in value
                if item is not None
            ]

        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return []

            if isinstance(loaded, list):
                return [
                    str(item)
                    for item in loaded
                    if item is not None
                ]

        try:
            return [
                str(item)
                for item in value
                if item is not None
            ]
        except TypeError:
            return []

    def _json_dict(self, value) -> dict:
        if value is None:
            return {}

        if isinstance(value, dict):
            return dict(value)

        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return {}

            return loaded if isinstance(loaded, dict) else {}

        return {}
