from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.application.skills.knowledge_base_consultation.workflow import (
    KnowledgeBaseConsultationWorkflow,
)
from app.infrastructure.mattermost.client import MattermostClient
from app.infrastructure.mattermost.websocket_bot import MattermostDirectMessage
from app.schemas.knowledge_base_consultation import KnowledgeBaseConsultationRequest


logger = logging.getLogger(__name__)


class MattermostKnowledgeBaseConsultationBot:
    def __init__(
        self,
        mattermost: MattermostClient,
        kb_key: str,
        session_factory: Callable[[], Session],
        workflow_factory: Callable[[Session], KnowledgeBaseConsultationWorkflow],
        max_post_chars: int = 12000,
    ):
        self.mattermost = mattermost
        self.kb_key = kb_key
        self.session_factory = session_factory
        self.workflow_factory = workflow_factory
        self.max_post_chars = max(max_post_chars, 1000)

    def handle_direct_message(self, message: MattermostDirectMessage) -> None:
        question = message.message.strip()

        if not question:
            return

        logger.info(
            "Knowledge base consultation Mattermost request started: "
            "kb_key=%s, channel_id=%s, user_id=%s, post_id=%s, question_size=%s",
            self.kb_key,
            message.channel_id,
            message.user_id,
            message.post_id,
            len(question),
        )

        session: Session | None = None

        try:
            self.mattermost.create_post(
                channel_id=message.channel_id,
                message=(
                    "Принял вопрос в работу.\n\n"
                    f"База знаний: `{self._safe_inline(self.kb_key)}`\n"
                    "Ищу релевантные материалы в индексе базы знаний и готовлю ответ."
                ),
                root_id=message.post_id,
            )

            session = self.session_factory()
            workflow = self.workflow_factory(session)

            result = workflow.run(
                KnowledgeBaseConsultationRequest(
                    kb_key=self.kb_key,
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
                root_id=message.post_id,
            )

            logger.info(
                "Knowledge base consultation Mattermost request completed: "
                "kb_key=%s, channel_id=%s, user_id=%s, post_id=%s",
                self.kb_key,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

        except HTTPException as exc:
            if session is not None:
                session.rollback()

            logger.exception(
                "Knowledge base consultation Mattermost request failed with HTTPException: "
                "kb_key=%s, channel_id=%s, user_id=%s, post_id=%s",
                self.kb_key,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

            self._send_long_message(
                channel_id=message.channel_id,
                message=self._format_error_response(exc),
                root_id=message.post_id,
            )

        except Exception as exc:
            if session is not None:
                session.rollback()

            logger.exception(
                "Knowledge base consultation Mattermost request failed: "
                "kb_key=%s, channel_id=%s, user_id=%s, post_id=%s",
                self.kb_key,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

            self._send_long_message(
                channel_id=message.channel_id,
                message=(
                    "Не удалось выполнить консультацию по базе знаний.\n\n"
                    f"Ошибка: `{self._safe_inline(str(exc))}`"
                ),
                root_id=message.post_id,
            )

        finally:
            if session is not None:
                session.close()

    def _format_success_response(
        self,
        question: str,
        result: dict,
    ) -> str:
        answer = (result.get("answer") or "").strip()
        limitations = (result.get("limitations") or "").strip()
        sources = result.get("sources") or []
        index_stats = result.get("index_stats") or {}

        sections = [
            answer or "Ответ не был сформирован.",
        ]

        if limitations:
            sections.append(
                "**Ограничения:**\n"
                f"{limitations}"
            )

        if sources:
            sections.append(self._format_sources(sources))

        sections.append(
            self._format_footer(
                question=question,
                index_stats=index_stats,
            )
        )

        return "\n\n".join(section for section in sections if section)

    def _format_sources(
        self,
        sources: list[dict],
    ) -> str:
        lines = ["**Источники:**"]

        for index, source in enumerate(sources, start=1):
            title = source.get("title") or "Без названия"
            full_title = source.get("full_title")
            source_url = source.get("source_url")
            section_path = source.get("section_path") or []
            chunk_id = source.get("chunk_id")
            chunk_ord = source.get("chunk_ord")
            score = source.get("score")

            title_label = self._safe_inline(str(title))
            if source_url:
                title_label = f"[{title_label}]({source_url})"

            lines.append(f"{index}. {title_label}")

            if full_title and full_title != title:
                lines.append(f"   Путь: `{self._safe_inline(str(full_title))}`")

            if section_path:
                section = " / ".join(str(item) for item in section_path if item)
                if section:
                    lines.append(f"   Раздел: `{self._safe_inline(section)}`")

            details = []

            if chunk_id:
                details.append(f"chunk `{self._safe_inline(str(chunk_id))}`")

            if chunk_ord is not None:
                details.append(f"ord `{chunk_ord}`")

            if score is not None:
                details.append(f"score `{score}`")

            if details:
                lines.append(f"   {'; '.join(details)}")

        return "\n".join(lines)

    def _format_footer(
        self,
        question: str,
        index_stats: dict,
    ) -> str:
        kb_key = index_stats.get("kb_key") or self.kb_key
        index_id = index_stats.get("index_id")
        documents_count = index_stats.get("documents_count")
        chunks_count = index_stats.get("chunks_count")

        lines = [
            "---",
            f"**База знаний:** `{self._safe_inline(str(kb_key))}`",
        ]

        if index_id:
            lines.append(f"**Index ID:** `{self._safe_inline(str(index_id))}`")

        if documents_count is not None and chunks_count is not None:
            lines.append(
                f"**Индекс:** документов `{documents_count}`, chunks `{chunks_count}`"
            )

        return "\n".join(lines)

    def _format_error_response(
        self,
        exc: HTTPException,
    ) -> str:
        detail = exc.detail

        return (
            "Не удалось выполнить консультацию по базе знаний.\n\n"
            f"HTTP status: `{exc.status_code}`\n"
            f"Ошибка: `{self._safe_inline(str(detail))}`"
        )

    def _send_long_message(
        self,
        channel_id: str,
        message: str,
        root_id: str | None = None,
    ) -> None:
        parts = self._split_message(message)

        thread_root_id = root_id

        for index, part in enumerate(parts):
            post = self.mattermost.create_post(
                channel_id=channel_id,
                message=part,
                root_id=thread_root_id,
            )

            if not thread_root_id and index == 0:
                thread_root_id = post.get("id")

    def _split_message(
        self,
        message: str,
    ) -> list[str]:
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

    def _add_part_header(
        self,
        part: str,
        index: int,
        total: int,
    ) -> str:
        if total <= 1:
            return part

        return f"**Часть {index}/{total}**\n\n{part}"

    def _safe_inline(
        self,
        value: str,
    ) -> str:
        return " ".join(value.replace("`", "'").split())[:1000]
