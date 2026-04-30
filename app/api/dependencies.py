from app.application.skills.code_review.context_builder import ContextBuilder
from app.application.skills.codebase_consultation.workflow import CodebaseConsultationWorkflow
from app.components.code_search.embeddings import build_embedding_client
from app.components.code_search.indexer import CodebaseIndexCache, CodebaseIndexer
from app.components.code_search.vector_store import build_vector_store_factory
from app.application.skills.code_review.workflow import CodeReviewWorkflow
from app.components.diff.localizer import DiffLineLocalizer
from app.components.llm.structured_client import StructuredLLMClient
from app.components.review.comment_publisher import ReviewCommentPublisher
from app.core.config import Settings, get_settings
from app.infrastructure.gitlab.client import GitLabClient
from app.infrastructure.jira.client import JiraClient
from app.infrastructure.mattermost.client import MattermostClient


settings = get_settings()

llm = StructuredLLMClient(
    model=settings.model_llm,
    api_key=settings.openrouter_api_key,
    base_url=settings.base_url,
    max_tokens=settings.llm_max_tokens,
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

mattermost = MattermostClient(
    base_url=settings.mattermost_url,
    token=settings.mattermost_bot_token,
)

diff_line_localizer = DiffLineLocalizer()

review_comment_publisher = ReviewCommentPublisher(
    gitlab=gitlab,
    localizer=diff_line_localizer,
)
embedding_client = build_embedding_client(settings)
vector_store_factory = build_vector_store_factory(settings)
codebase_index_cache = CodebaseIndexCache(
    indexer=CodebaseIndexer(
        embedding_client=embedding_client,
        vector_store_factory=vector_store_factory,
    )
)

code_review_workflow = CodeReviewWorkflow(
    llm=llm,
    context_builder=context_builder,
    gitlab=gitlab,
    jira=jira,
    comment_publisher=review_comment_publisher,
)
codebase_consultation_workflow = CodebaseConsultationWorkflow(
    llm=llm,
    index_cache=codebase_index_cache,
    agent_context_path=settings.agent_context_path,
)


def get_app_settings() -> Settings:
    return settings


def get_gitlab_client() -> GitLabClient:
    return gitlab


def get_jira_client() -> JiraClient:
    return jira


def get_mattermost_client() -> MattermostClient:
    return mattermost


def get_code_review_workflow() -> CodeReviewWorkflow:
    return code_review_workflow


def get_codebase_consultation_workflow() -> CodebaseConsultationWorkflow:
    return codebase_consultation_workflow


def get_llm_client() -> StructuredLLMClient:
    return llm
