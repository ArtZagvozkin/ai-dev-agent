from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


PROJECT_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class CodeProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=PROJECT_KEY_PATTERN,
    )
    display_name: str = Field(min_length=1, max_length=255)

    gitlab_project: str = Field(min_length=1, max_length=512)
    default_branch: str = Field(default="master", min_length=1, max_length=255)
    local_repository_path: str = Field(min_length=1, max_length=1024)

    review_context_path: str | None = Field(default=None, max_length=1024)
    consultation_context_path: str | None = Field(default=None, max_length=1024)

    default_top_k: int = Field(default=10, ge=1, le=20)
    max_files: int = Field(default=7000, ge=1, le=10000)
    max_file_bytes: int = Field(default=200000, ge=1, le=5000000)
    include_full_code_units: bool = True


class CodeProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)

    gitlab_project: str | None = Field(default=None, min_length=1, max_length=512)
    default_branch: str | None = Field(default=None, min_length=1, max_length=255)
    local_repository_path: str | None = Field(default=None, min_length=1, max_length=1024)

    review_context_path: str | None = Field(default=None, max_length=1024)
    consultation_context_path: str | None = Field(default=None, max_length=1024)

    default_top_k: int | None = Field(default=None, ge=1, le=20)
    max_files: int | None = Field(default=None, ge=1, le=10000)
    max_file_bytes: int | None = Field(default=None, ge=1, le=5000000)
    include_full_code_units: bool | None = None


class CodeProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID

    project_key: str
    display_name: str

    gitlab_project: str
    default_branch: str
    local_repository_path: str

    review_context_path: str | None = None
    consultation_context_path: str | None = None

    default_top_k: int
    max_files: int
    max_file_bytes: int
    include_full_code_units: bool

    current_index_id: UUID | None = None


class CodeProjectListResponse(BaseModel):
    items: list[CodeProjectResponse]


class CodeProjectDeleteResponse(BaseModel):
    deleted: bool
    project_key: str
