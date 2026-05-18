import logging
from collections.abc import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.application.bots.codebase_consultation_mattermost import (
    MattermostCodebaseConsultationBot,
)
from app.application.skills.code_review.context_builder import ContextBuilder
from app.application.skills.code_review.workflow import CodeReviewWorkflow
from app.application.skills.codebase_consultation.workflow import CodebaseConsultationWorkflow
from app.components.code_search.embeddings import build_embedding_client
from app.components.code_search.indexer import CodebaseIndexCache, CodebaseIndexer
from app.components.code_search.vector_store import build_vector_store_factory
from app.components.diff.localizer import DiffLineLocalizer
from app.components.llm.structured_client import StructuredLLMClient
from app.components.review.comment_publisher import ReviewCommentPublisher
from app.core.config import Settings, get_settings
from app.database.session import create_db_session, get_session_factory
from app.infrastructure.gitlab.client import GitLabClient
from app.infrastructure.jira.client import JiraClient
from app.infrastructure.mattermost.client import MattermostClient
from app.infrastructure.mattermost.websocket_bot import MattermostWebSocketBotRunner
from app.repositories.code_chunks import CodeChunkRepository
from app.repositories.code_projects import CodeProjectRepository
from app.repositories.codebase_files import CodebaseFileRepository
from app.repositories.codebase_indexes import CodebaseIndexRepository
from app.services.codebase.code_projects import CodeProjectService
from app.services.codebase.codebase_consultation_index import (
    PersistentCodebaseIndexCache,
    PersistentCodebaseIndexLoader,
)
from app.services.codebase.codebase_index_build import CodebaseIndexBuildService
from app.services.codebase.codebase_indexes import CodebaseIndexService


logger = logging.getLogger(__name__)

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

persistent_codebase_indexer = CodebaseIndexer(
    embedding_client=embedding_client,
)

persistent_codebase_index_cache = PersistentCodebaseIndexCache()

code_review_workflow = CodeReviewWorkflow(
    llm=llm,
    context_builder=context_builder,
    gitlab=gitlab,
    jira=jira,
    comment_publisher=review_comment_publisher,
)

mattermost_bot_runners: list[MattermostWebSocketBotRunner] = []


def get_db_session() -> Generator[Session, None, None]:
    yield from create_db_session()


def get_app_settings() -> Settings:
    return settings


def get_gitlab_client() -> GitLabClient:
    return gitlab


def get_jira_client() -> JiraClient:
    return jira


def get_mattermost_client() -> MattermostClient:
    return mattermost


def get_llm_client() -> StructuredLLMClient:
    return llm


def get_codebase_index_cache() -> CodebaseIndexCache:
    return codebase_index_cache


def get_code_review_workflow() -> CodeReviewWorkflow:
    return code_review_workflow


def build_codebase_consultation_workflow_for_session(
    session: Session,
) -> CodebaseConsultationWorkflow:
    persistent_index_loader = PersistentCodebaseIndexLoader(
        settings=settings,
        project_repository=CodeProjectRepository(session),
        index_repository=CodebaseIndexRepository(session),
        chunk_repository=CodeChunkRepository(session),
        cache=persistent_codebase_index_cache,
    )

    return CodebaseConsultationWorkflow(
        llm=llm,
        persistent_index_loader=persistent_index_loader,
        agent_context_path=settings.agent_context_path,
        gitlab_base_url=settings.gitlab_url,
    )


def get_codebase_consultation_workflow(
    session: Session = Depends(get_db_session),
) -> CodebaseConsultationWorkflow:
    return build_codebase_consultation_workflow_for_session(session)


def get_code_project_repository(
    session: Session = Depends(get_db_session),
) -> CodeProjectRepository:
    return CodeProjectRepository(session)


def get_code_project_service(
    session: Session = Depends(get_db_session),
) -> CodeProjectService:
    return CodeProjectService(
        repository=CodeProjectRepository(session),
        index_repository=CodebaseIndexRepository(session),
        session=session,
    )


def get_codebase_index_service(
    session: Session = Depends(get_db_session),
) -> CodebaseIndexService:
    return CodebaseIndexService(
        repository=CodebaseIndexRepository(session),
        session=session,
        settings=settings,
    )


def get_codebase_index_build_service(
    session: Session = Depends(get_db_session),
) -> CodebaseIndexBuildService:
    return CodebaseIndexBuildService(
        settings=settings,
        session=session,
        project_repository=CodeProjectRepository(session),
        index_repository=CodebaseIndexRepository(session),
        file_repository=CodebaseFileRepository(session),
        chunk_repository=CodeChunkRepository(session),
        indexer=persistent_codebase_indexer,
    )


def start_mattermost_bots() -> None:
    if not settings.mattermost_pf_scout_enabled:
        logger.info("Mattermost pf_scout bot is disabled")
        return

    if mattermost_bot_runners:
        logger.warning("Mattermost bot runners are already started")
        return

    try:
        session_factory = get_session_factory()

        pf_scout_client = MattermostClient(
            base_url=settings.mattermost_url,
            token=settings.mattermost_pf_scout_bot_token,
        )

        pf_scout_bot = MattermostCodebaseConsultationBot(
            mattermost=pf_scout_client,
            project_key=settings.mattermost_pf_scout_project_key,
            session_factory=session_factory,
            workflow_factory=build_codebase_consultation_workflow_for_session,
            max_post_chars=settings.mattermost_bot_max_post_chars,
        )

        pf_scout_runner = MattermostWebSocketBotRunner(
            name="pf_scout",
            client=pf_scout_client,
            on_direct_message=pf_scout_bot.handle_direct_message,
            reconnect_seconds=settings.mattermost_bot_reconnect_seconds,
            worker_count=settings.mattermost_bot_worker_count,
        )

        pf_scout_runner.start()
        mattermost_bot_runners.append(pf_scout_runner)

        logger.info(
            "Mattermost pf_scout bot started: project_key=%s",
            settings.mattermost_pf_scout_project_key,
        )

    except Exception:
        logger.exception("Failed to start Mattermost pf_scout bot")


def stop_mattermost_bots() -> None:
    while mattermost_bot_runners:
        runner = mattermost_bot_runners.pop()

        try:
            runner.stop()
        except Exception:
            logger.exception(
                "Failed to stop Mattermost bot runner: name=%s",
                runner.name,
            )
