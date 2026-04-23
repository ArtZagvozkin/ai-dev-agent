from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_app_settings, get_gitlab_client
from app.core.config import Settings
from app.infrastructure.gitlab.client import GitLabClient
from app.schemas.api import (
    CreateInlineCommentRequest,
    GitLabDiscussionResponse,
    GitLabMRResponse,
)


router = APIRouter(
    prefix="/diagnostics/gitlab",
    tags=["diagnostics: gitlab"],
)


@router.get("/mr/{mr_iid}", response_model=GitLabMRResponse)
def get_gitlab_mr(
    mr_iid: int,
    gitlab: GitLabClient = Depends(get_gitlab_client),
):
    return gitlab.get_merge_request_data(mr_iid)


@router.get("/agent-context")
def get_agent_context(
    ref: str = Query(...),
    file_path: str | None = Query(default=None),
    settings: Settings = Depends(get_app_settings),
    gitlab: GitLabClient = Depends(get_gitlab_client),
):
    resolved_file_path = file_path or settings.agent_context_path

    content = gitlab.get_raw_file(
        file_path=resolved_file_path,
        ref=ref,
    )

    return {
        "path": resolved_file_path,
        "ref": ref,
        "content": content,
    }


@router.post(
    "/mr/{mr_iid}/inline-comment",
    response_model=GitLabDiscussionResponse,
)
def create_inline_comment(
    mr_iid: int,
    data: CreateInlineCommentRequest,
    gitlab: GitLabClient = Depends(get_gitlab_client),
):
    return gitlab.create_inline_comment(
        mr_iid=mr_iid,
        body=data.body,
        new_path=data.new_path,
        new_line=data.new_line,
    )
