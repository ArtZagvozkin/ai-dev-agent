from fastapi import APIRouter, Depends

from app.api.dependencies import get_app_settings, get_code_review_workflow
from app.application.skills.code_review.workflow import CodeReviewWorkflow
from app.core.config import Settings
from app.schemas.api import ReviewRequest, ReviewWithPublishResponse


router = APIRouter(
    prefix="/manual",
    tags=["manual"],
)


@router.post("/review", response_model=ReviewWithPublishResponse)
def run_code_review(
    data: ReviewRequest,
    settings: Settings = Depends(get_app_settings),
    workflow: CodeReviewWorkflow = Depends(get_code_review_workflow),
):
    return workflow.run(settings.agent_context_path, data)
