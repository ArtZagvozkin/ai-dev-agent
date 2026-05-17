from fastapi import APIRouter, Depends, Path, status

from app.api.dependencies import (
    get_code_project_service,
    get_codebase_index_build_service,
)
from app.schemas.code_projects import (
    PROJECT_KEY_PATTERN,
    CodeProjectCreateRequest,
    CodeProjectDeleteResponse,
    CodeProjectListResponse,
    CodeProjectResponse,
    CodeProjectUpdateRequest,
)
from app.schemas.codebase_indexes import (
    CodebaseIndexBuildRequest,
    CodebaseIndexResponse,
)
from app.services.codebase.code_projects import CodeProjectService
from app.services.codebase.codebase_index_build import CodebaseIndexBuildService


router = APIRouter(
    prefix="/code-projects",
    tags=["code-projects"],
)


@router.post(
    "",
    response_model=CodeProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_code_project(
    data: CodeProjectCreateRequest,
    service: CodeProjectService = Depends(get_code_project_service),
):
    return service.create_project(data)


@router.get("", response_model=CodeProjectListResponse)
def list_code_projects(
    service: CodeProjectService = Depends(get_code_project_service),
):
    return {
        "items": service.list_projects(),
    }


@router.post(
    "/{project_key}/index/build",
    response_model=CodebaseIndexResponse,
    status_code=status.HTTP_201_CREATED,
)
def build_first_codebase_index(
    data: CodebaseIndexBuildRequest,
    project_key: str = Path(
        min_length=1,
        max_length=128,
        pattern=PROJECT_KEY_PATTERN,
    ),
    service: CodebaseIndexBuildService = Depends(get_codebase_index_build_service),
):
    return service.build_first_index(
        project_key=project_key,
        data=data,
    )


@router.get("/{project_key}", response_model=CodeProjectResponse)
def get_code_project(
    project_key: str = Path(
        min_length=1,
        max_length=128,
        pattern=PROJECT_KEY_PATTERN,
    ),
    service: CodeProjectService = Depends(get_code_project_service),
):
    return service.get_project(project_key)


@router.patch("/{project_key}", response_model=CodeProjectResponse)
def update_code_project(
    data: CodeProjectUpdateRequest,
    project_key: str = Path(
        min_length=1,
        max_length=128,
        pattern=PROJECT_KEY_PATTERN,
    ),
    service: CodeProjectService = Depends(get_code_project_service),
):
    return service.update_project(project_key, data)


@router.delete("/{project_key}", response_model=CodeProjectDeleteResponse)
def delete_code_project(
    project_key: str = Path(
        min_length=1,
        max_length=128,
        pattern=PROJECT_KEY_PATTERN,
    ),
    service: CodeProjectService = Depends(get_code_project_service),
):
    return service.delete_project(project_key)
