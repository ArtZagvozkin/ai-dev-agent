from fastapi import APIRouter, Depends

from app.api.dependencies import get_jira_client
from app.infrastructure.jira.client import JiraClient
from app.schemas.api import JiraTaskResponse


router = APIRouter(
    prefix="/diagnostics/jira",
    tags=["diagnostics: jira"],
)


@router.get("/task/{issue_key}", response_model=JiraTaskResponse)
def get_jira_task(
    issue_key: str,
    jira: JiraClient = Depends(get_jira_client),
):
    return jira.get_task(issue_key)
