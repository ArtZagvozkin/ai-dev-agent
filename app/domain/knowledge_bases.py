from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class KnowledgeBase:
    id: UUID
    kb_key: str
    display_name: str
    source_type: str
    source_path: str | None
    default_top_k: int
    default_max_context_chars: int
    current_index_id: UUID | None
