from fastapi import APIRouter, Depends

from app.api.dependencies import get_codebase_consultation_workflow
from app.application.skills.codebase_consultation.workflow import CodebaseConsultationWorkflow
from app.schemas.api import CodebaseConsultationRequest, CodebaseConsultationResponse


router = APIRouter(
    prefix="/manual",
    tags=["manual"],
)


@router.post("/codebase-consultation", response_model=CodebaseConsultationResponse)
def run_codebase_consultation(
    data: CodebaseConsultationRequest,
    workflow: CodebaseConsultationWorkflow = Depends(get_codebase_consultation_workflow),
):
    return workflow.run(data)
