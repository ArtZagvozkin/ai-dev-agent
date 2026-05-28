from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_knowledge_base_consultation_workflow
from app.application.skills.knowledge_base_consultation.workflow import (
    KnowledgeBaseConsultationWorkflow,
)
from app.schemas.knowledge_base_consultation import (
    KnowledgeBaseConsultationRequest,
    KnowledgeBaseConsultationResponse,
)


router = APIRouter(
    prefix="/manual",
    tags=["manual"],
)


@router.post(
    "/knowledge-base-consultation",
    response_model=KnowledgeBaseConsultationResponse,
)
def run_knowledge_base_consultation(
    data: KnowledgeBaseConsultationRequest,
    workflow: KnowledgeBaseConsultationWorkflow = Depends(
        get_knowledge_base_consultation_workflow,
    ),
):
    return workflow.run(data)
