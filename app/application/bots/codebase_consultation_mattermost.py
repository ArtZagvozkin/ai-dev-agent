import logging
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.application.skills.codebase_consultation.workflow import CodebaseConsultationWorkflow
from app.infrastructure.mattermost.client import MattermostClient
from app.infrastructure.mattermost.websocket_bot import MattermostDirectMessage
from app.schemas.api import CodebaseConsultationRequest


logger = logging.getLogger(__name__)


class MattermostCodebaseConsultationBot:
    def __init__(
        self,
        mattermost: MattermostClient,
        project_key: str,
        session_factory: Callable[[], Session],
        workflow_factory: Callable[[Session], CodebaseConsultationWorkflow],
        max_post_chars: int = 12000,
    ):
        self.mattermost = mattermost
        self.project_key = project_key
        self.session_factory = session_factory
        self.workflow_factory = workflow_factory
        self.max_post_chars = max(max_post_chars, 1000)

    def handle_direct_message(self, message: MattermostDirectMessage) -> None:
        question = message.message.strip()

        if not question:
            return

        logger.info(
            "Codebase consultation Mattermost request started: "
            "project_key=%s, channel_id=%s, user_id=%s, post_id=%s, question_size=%s",
            self.project_key,
            message.channel_id,
            message.user_id,
            message.post_id,
            len(question),
        )

        session = self.session_factory()

        try:
            workflow = self.workflow_factory(session)

            result = workflow.run(
                CodebaseConsultationRequest(
                    project_id=self.project_key,
                    question=question,
                )
            )

            response_message = self._format_success_response(
                question=question,
                result=result,
            )

            self._send_long_message(
                channel_id=message.channel_id,
                message=response_message,
            )

            logger.info(
                "Codebase consultation Mattermost request completed: "
                "project_key=%s, channel_id=%s, user_id=%s, post_id=%s",
                self.project_key,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

        except HTTPException as exc:
            session.rollback()

            logger.exception(
                "Codebase consultation Mattermost request failed with HTTPException: "
                "project_key=%s, channel_id=%s, user_id=%s, post_id=%s",
                self.project_key,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

            self._send_long_message(
                channel_id=message.channel_id,
                message=self._format_error_response(exc),
            )

        except Exception as exc:
            session.rollback()

            logger.exception(
                "Codebase consultation Mattermost request failed: "
                "project_key=%s, channel_id=%s, user_id=%s, post_id=%s",
                self.project_key,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

            self._send_long_message(
                channel_id=message.channel_id,
                message=(
                    "Не удалось выполнить консультацию по кодовой базе.\n\n"
                    f"Ошибка: `{self._safe_inline(str(exc))}`"
                ),
            )

        finally:
            session.close()

    def _format_success_response(self, question: str, result: dict) -> str:
        answer = (result.get("answer") or "").strip()
        sources = result.get("sources") or []
        index_stats = result.get("index_stats") or {}
        query_plan = result.get("query_plan") or {}

        sections = [
            answer or "Ответ не был сформирован.",
        ]

        if sources:
            sections.append(self._format_sources(sources))

        sections.append(
            self._format_footer(
                question=question,
                index_stats=index_stats,
                query_plan=query_plan,
            )
        )

        return "\n\n".join(section for section in sections if section)

    def _format_sources(self, sources: list[dict]) -> str:
        lines = ["**Источники:**"]
    
        for index, source in enumerate(sources, start=1):
            path = source.get("path") or "unknown"
            start_line = source.get("start_line")
            end_line = source.get("end_line")
            gitlab_url = source.get("gitlab_url")
            symbol = source.get("symbol")
            chunk_type = source.get("chunk_type") or "chunk"
    
            location = path
            if start_line is not None and end_line is not None:
                location = f"{path}:{start_line}-{end_line}"
    
            location_label = f"`{location}`"
            if gitlab_url:
                location_label = f"[`{location}`]({gitlab_url})"
    
            suffix = f" — `{symbol}`" if symbol else ""
    
            lines.append(
                f"{index}. {location_label} ({chunk_type}){suffix}"
            )
    
        return "\n".join(lines)

    def _format_footer(
        self,
        question: str,
        index_stats: dict,
        query_plan: dict,
    ) -> str:
        files_indexed = index_stats.get("files_indexed")
        chunks_indexed = index_stats.get("chunks_indexed")
        retrieval_mode = query_plan.get("retrieval_mode") or "unknown"

        lines = [
            "---",
            f"**Retrieval mode:** `{retrieval_mode}`",
        ]

        if files_indexed is not None and chunks_indexed is not None:
            lines.append(
                f"**Индекс:** файлов `{files_indexed}`, chunks `{chunks_indexed}`"
            )

        return "\n".join(lines)

    def _format_error_response(self, exc: HTTPException) -> str:
        detail = exc.detail

        return (
            "Не удалось выполнить консультацию по кодовой базе.\n\n"
            f"HTTP status: `{exc.status_code}`\n"
            f"Ошибка: `{self._safe_inline(str(detail))}`"
        )

    def _send_long_message(self, channel_id: str, message: str) -> None:
        parts = self._split_message(message)

        root_id = None

        for index, part in enumerate(parts):
            post = self.mattermost.create_post(
                channel_id=channel_id,
                message=part,
                root_id=root_id,
            )

            if index == 0:
                root_id = post.get("id")

    def _split_message(self, message: str) -> list[str]:
        if len(message) <= self.max_post_chars:
            return [message]

        parts = []
        current = ""

        for line in message.splitlines(keepends=True):
            if len(current) + len(line) <= self.max_post_chars:
                current += line
                continue

            if current:
                parts.append(current.rstrip())

            if len(line) <= self.max_post_chars:
                current = line
                continue

            for start in range(0, len(line), self.max_post_chars):
                parts.append(line[start : start + self.max_post_chars].rstrip())

            current = ""

        if current:
            parts.append(current.rstrip())

        return [
            self._add_part_header(part, index, len(parts))
            for index, part in enumerate(parts, start=1)
        ]

    def _add_part_header(self, part: str, index: int, total: int) -> str:
        if total <= 1:
            return part

        return f"**Часть {index}/{total}**\n\n{part}"

    def _safe_inline(self, value: str) -> str:
        return value.replace("`", "'")[:1000]
