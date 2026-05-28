from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class KnowledgeBaseChunk:
    db_id: UUID
    document_id: UUID
    index_id: UUID

    chunk_id: str
    chunk_ord: int

    section_path: list[str]
    block_types: list[str]

    text: str
    contextualized_text: str
    char_len: int
    chunk_hash: str
    metadata: dict[str, Any]

    external_id: str
    source_url: str | None
    title: str | None
    full_title: str | None
    source_revision: str | None
    updated_at_src: Any
