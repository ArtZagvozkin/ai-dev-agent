from fastapi import APIRouter

from app.schemas.api import (
    CodebaseConsultationRequest,
    CodebaseConsultationResponse,
)


router = APIRouter(
    prefix="/manual",
    tags=["manual"],
)


@router.post(
    "/codebase-consultation",
    response_model=CodebaseConsultationResponse,
)
def run_codebase_consultation(
    data: CodebaseConsultationRequest,
):
    """
    Temporary stub endpoint for codebase consultation.

    Future implementation:
    - extract search queries from the question;
    - search codebase files under CODEBASE_PATH;
    - collect relevant snippets;
    - ask LLM to answer using found sources.
    """

    return {
        "question": data.question,
        "answer": (
            "Codebase consultation endpoint is available, "
            "but the real code search implementation is not added yet."
        ),
        "search_queries": [],
        "sources": [],
    }
