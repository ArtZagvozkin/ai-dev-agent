from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import RequestLoggingMiddleware, setup_logging


settings = get_settings()
setup_logging(
    log_dir=settings.log_dir,
    log_level=settings.log_level,
    log_file_name=settings.log_file_name,
    log_backup_days=settings.log_backup_days,
)

from app.api.dependencies import (  # noqa: E402
    start_mattermost_bots,
    start_telegram_bots,
    stop_mattermost_bots,
    stop_telegram_bots,
)
from app.api.routes import health  # noqa: E402
from app.api.routes.diagnostics import gitlab as diagnostics_gitlab  # noqa: E402
from app.api.routes.diagnostics import jira as diagnostics_jira  # noqa: E402
from app.api.routes.diagnostics import mattermost as diagnostics_mattermost  # noqa: E402
from app.api.routes.diagnostics import llm as diagnostics_llm  # noqa: E402
from app.api.routes.codebase import code_projects  # noqa: E402
from app.api.routes.codebase import codebase_indexes  # noqa: E402
from app.api.routes.knowledge_base import knowledge_bases  # noqa: E402
from app.api.routes.knowledge_base import knowledge_base_indexes  # noqa: E402
from app.api.routes.manual import code_review as manual_code_review  # noqa: E402
from app.api.routes.manual import codebase_consultation as manual_codebase_consultation  # noqa: E402
from app.api.routes.manual import knowledge_base_consultation as manual_knowledge_base_consultation  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_mattermost_bots()
    start_telegram_bots()

    try:
        yield
    finally:
        stop_telegram_bots()
        stop_mattermost_bots()


app = FastAPI(lifespan=lifespan)

app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router)
app.include_router(diagnostics_jira.router)
app.include_router(diagnostics_gitlab.router)
app.include_router(diagnostics_mattermost.router)
app.include_router(diagnostics_llm.router)
app.include_router(code_projects.router)
app.include_router(codebase_indexes.router)
app.include_router(knowledge_bases.router)
app.include_router(knowledge_base_indexes.router)
app.include_router(manual_code_review.router)
app.include_router(manual_codebase_consultation.router)
app.include_router(manual_knowledge_base_consultation.router)
