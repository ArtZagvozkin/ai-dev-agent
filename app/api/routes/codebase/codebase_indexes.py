from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_codebase_index_service
from app.schemas.code_projects import PROJECT_KEY_PATTERN
from app.schemas.codebase_indexes import (
    CodebaseIndexDeleteResponse,
    CodebaseIndexListResponse,
    CodebaseIndexResponse,
)
from app.services.codebase.codebase_indexes import CodebaseIndexService


router = APIRouter(
    prefix="/codebase-indexes",
    tags=["codebase-indexes"],
)


@router.get("", response_model=CodebaseIndexListResponse)
def list_codebase_indexes(
    project_key: str | None = Query(
        default=None,
        min_length=1,
        max_length=128,
        pattern=PROJECT_KEY_PATTERN,
    ),
    index_status: Literal["building", "ready", "failed", "archived"] | None = Query(
        default=None,
        alias="status",
    ),
    service: CodebaseIndexService = Depends(get_codebase_index_service),
):
    return {
        "items": service.list_indexes(
            project_key=project_key,
            index_status=index_status,
        ),
    }


@router.get("/{index_id}", response_model=CodebaseIndexResponse)
def get_codebase_index(
    index_id: UUID,
    service: CodebaseIndexService = Depends(get_codebase_index_service),
):
    return service.get_index(index_id)


@router.delete(
    "/{index_id}",
    response_model=CodebaseIndexDeleteResponse,
    status_code=status.HTTP_200_OK,
)
def delete_codebase_index(
    index_id: UUID,
    service: CodebaseIndexService = Depends(get_codebase_index_service),
):
    return service.delete_index(index_id)
