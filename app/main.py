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

from app.api.routes import health  # noqa: E402
from app.api.routes.diagnostics import gitlab as diagnostics_gitlab  # noqa: E402
from app.api.routes.diagnostics import jira as diagnostics_jira  # noqa: E402
from app.api.routes.diagnostics import mattermost as diagnostics_mattermost  # noqa: E402
from app.api.routes.diagnostics import llm as diagnostics_llm  # noqa: E402
from app.api.routes.manual import code_review as manual_code_review  # noqa: E402


app = FastAPI()

app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router)
app.include_router(diagnostics_jira.router)
app.include_router(diagnostics_gitlab.router)
app.include_router(diagnostics_mattermost.router)
app.include_router(diagnostics_llm.router)
app.include_router(manual_code_review.router)
