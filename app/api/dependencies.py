from app.application.skills.code_review.context_builder import ContextBuilder
from app.application.skills.code_review.workflow import CodeReviewWorkflow
from app.components.diff.localizer import DiffLineLocalizer
from app.components.llm.structured_client import StructuredLLMClient
from app.components.review.comment_publisher import ReviewCommentPublisher
from app.core.config import Settings, get_settings
from app.infrastructure.gitlab.client import GitLabClient
from app.infrastructure.jira.client import JiraClient


settings = get_settings()

llm = StructuredLLMClient(
    model=settings.model_llm,
    api_key=settings.openrouter_api_key,
    base_url=settings.base_url,
)

context_builder = ContextBuilder()

gitlab = GitLabClient(
    base_url=settings.gitlab_url,
    token=settings.gitlab_token,
    project_id=settings.gitlab_project_id,
)

jira = JiraClient(
    base_url=settings.jira_url,
    email=settings.jira_email,
    api_token=settings.jira_api_token,
)

diff_line_localizer = DiffLineLocalizer()

review_comment_publisher = ReviewCommentPublisher(
    gitlab=gitlab,
    localizer=diff_line_localizer,
)

code_review_workflow = CodeReviewWorkflow(
    llm=llm,
    context_builder=context_builder,
    gitlab=gitlab,
    jira=jira,
    comment_publisher=review_comment_publisher,
)


def get_app_settings() -> Settings:
    return settings


def get_gitlab_client() -> GitLabClient:
    return gitlab


def get_jira_client() -> JiraClient:
    return jira


def get_code_review_workflow() -> CodeReviewWorkflow:
    return code_review_workflow

def get_llm_client() -> StructuredLLMClient:
    return llm
