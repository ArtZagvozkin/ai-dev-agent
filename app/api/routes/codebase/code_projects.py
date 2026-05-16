from fastapi import APIRouter, Depends, Path, status

from app.api.dependencies import get_code_project_service
from app.schemas.code_projects import (
    PROJECT_KEY_PATTERN,
    CodeProjectCreateRequest,
    CodeProjectDeleteResponse,
    CodeProjectListResponse,
    CodeProjectResponse,
    CodeProjectUpdateRequest,
)
from app.services.code_projects import CodeProjectService


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
