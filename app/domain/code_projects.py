from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class CodeProject:
    id: UUID
    project_key: str
    display_name: str

    gitlab_project: str
    default_branch: str
    local_repository_path: str

    review_context_path: str | None
    consultation_context_path: str | None

    default_top_k: int
    max_files: int
    max_file_bytes: int
    include_full_code_units: bool

    current_index_id: UUID | None
