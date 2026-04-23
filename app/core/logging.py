import logging
import logging.config
import re
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class DailyLogFileHandler(TimedRotatingFileHandler):
    """
    Пишет текущие логи в:
        ai-dev-agent.log

    При смене дня переносит старый файл в:
        ai-dev-agent.2026-04-24.log
    """

    def __init__(
        self,
        filename: str,
        backup_count: int,
        encoding: str = "utf-8",
    ):
        super().__init__(
            filename=filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding=encoding,
            utc=False,
        )

        self.suffix = "%Y-%m-%d"
        self.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def rotation_filename(self, default_name: str) -> str:
        """
        TimedRotatingFileHandler по умолчанию делает:
            ai-dev-agent.log.2026-04-24

        Нам нужен формат:
            ai-dev-agent.2026-04-24.log
        """

        default_path = Path(default_name)
        rotated_date = default_path.name.rsplit(".", 1)[-1]

        base_path = Path(self.baseFilename)
        return str(
            base_path.with_name(
                f"{base_path.stem}.{rotated_date}{base_path.suffix}"
            )
        )

    def getFilesToDelete(self) -> list[str]:
        """
        Удаляет старые daily log файлы по backupCount.

        Ищет файлы вида:
            ai-dev-agent.2026-04-24.log
        """

        if self.backupCount <= 0:
            return []

        base_path = Path(self.baseFilename)
        log_dir = base_path.parent

        pattern = re.compile(
            rf"^{re.escape(base_path.stem)}\."
            rf"(\d{{4}}-\d{{2}}-\d{{2}})"
            rf"{re.escape(base_path.suffix)}$"
        )

        result = []

        for file_path in log_dir.iterdir():
            match = pattern.match(file_path.name)
            if match:
                result.append(str(file_path))

        result.sort()

        if len(result) <= self.backupCount:
            return []

        return result[: len(result) - self.backupCount]


def setup_logging(
    log_dir: str,
    log_level: str,
    log_file_name: str,
    log_backup_days: int,
) -> None:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    log_file = log_path / log_file_name

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": (
                        "%(asctime)s | %(levelname)s | %(name)s | "
                        "%(message)s"
                    )
                },
                "detailed": {
                    "format": (
                        "%(asctime)s | %(levelname)s | %(name)s | "
                        "%(filename)s:%(lineno)d | %(message)s"
                    )
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": log_level,
                    "formatter": "default",
                },
                "daily_file": {
                    "()": DailyLogFileHandler,
                    "level": log_level,
                    "formatter": "detailed",
                    "filename": str(log_file),
                    "backup_count": log_backup_days,
                    "encoding": "utf-8",
                },
            },
            "root": {
                "level": log_level,
                "handlers": [
                    "console",
                    "daily_file",
                ],
            },
            "loggers": {
                "uvicorn": {
                    "level": log_level,
                    "handlers": [
                        "console",
                        "daily_file",
                    ],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": log_level,
                    "handlers": [
                        "console",
                        "daily_file",
                    ],
                    "propagate": False,
                },
                # HTTP access logs пишем сами через middleware,
                # чтобы контролировать формат и request_id.
                "uvicorn.access": {
                    "level": "WARNING",
                    "handlers": [
                        "console",
                        "daily_file",
                    ],
                    "propagate": False,
                },
            },
        }
    )

    logging.getLogger(__name__).info(
        "Logging configured: log_dir=%s, log_file=%s, log_level=%s, backup_days=%s",
        log_path,
        log_file.name,
        log_level,
        log_backup_days,
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        logger = logging.getLogger("app.http")

        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        started_at = time.perf_counter()

        client_host = request.client.host if request.client else None

        logger.info(
            "HTTP request started: request_id=%s, method=%s, path=%s, client=%s",
            request_id,
            request.method,
            request.url.path,
            client_host,
        )

        try:
            response = await call_next(request)

        except Exception:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)

            logger.exception(
                "HTTP request failed: request_id=%s, method=%s, path=%s, client=%s, duration_ms=%s",
                request_id,
                request.method,
                request.url.path,
                client_host,
                duration_ms,
            )

            raise

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)

        response.headers["X-Request-ID"] = request_id

        logger.info(
            "HTTP request completed: request_id=%s, method=%s, path=%s, status_code=%s, duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        return response
