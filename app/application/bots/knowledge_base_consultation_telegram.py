from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.application.skills.knowledge_base_consultation.workflow import (
    KnowledgeBaseConsultationWorkflow,
)
from app.infrastructure.telegram.client import TelegramBotApiClient
from app.infrastructure.telegram.markdown import (
    convert_to_md_v2,
    split_md_v2,
    split_plain_text,
)
from app.schemas.knowledge_base_consultation import KnowledgeBaseConsultationRequest


logger = logging.getLogger(__name__)


class TelegramKnowledgeBaseConsultationBot:
    def __init__(
        self,
        kb_key: str,
        session_factory: Callable[[], Session],
        workflow_factory: Callable[[Session], KnowledgeBaseConsultationWorkflow],
        max_message_chars: int = 3900,
        allowed_chat_ids: set[int] | None = None,
    ):
        self.kb_key = kb_key
        self.session_factory = session_factory
        self.workflow_factory = workflow_factory
        self.max_message_chars = max(1000, min(max_message_chars, 4096))
        self.allowed_chat_ids = allowed_chat_ids or set()

    def handle_update(
        self,
        update: dict[str, Any],
        telegram: TelegramBotApiClient,
    ) -> None:
        message = update.get("message") or update.get("edited_message")

        if not message:
            logger.info(
                "Telegram update ignored because it has no message: update_id=%s",
                update.get("update_id"),
            )
            return

        self._handle_message(
            message=message,
            telegram=telegram,
            update_id=update.get("update_id"),
        )

    def _handle_message(
        self,
        message: dict[str, Any],
        telegram: TelegramBotApiClient,
        update_id: int | None,
    ) -> None:
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}

        chat_id = chat.get("id")
        chat_type = chat.get("type") or "unknown"
        message_id = message.get("message_id")
        text = (message.get("text") or message.get("caption") or "").strip()

        logger.info(
            "Telegram message received: kb_key=%s, update_id=%s, chat_id=%s, "
            "chat_type=%s, message_id=%s, from_user_id=%s, text_size=%s, "
            "has_document=%s, has_photo=%s",
            self.kb_key,
            update_id,
            chat_id,
            chat_type,
            message_id,
            from_user.get("id"),
            len(text),
            bool(message.get("document")),
            bool(message.get("photo")),
        )

        if chat_id is None or message_id is None:
            logger.warning(
                "Telegram message ignored because chat_id or message_id is missing: "
                "update_id=%s",
                update_id,
            )
            return

        if not self._is_allowed_chat(int(chat_id)):
            logger.warning(
                "Telegram message ignored because chat is not allowed: chat_id=%s",
                chat_id,
            )
            return

        if self._is_start_command(text):
            self._send_plain(
                telegram=telegram,
                chat_id=int(chat_id),
                reply_to_message_id=int(message_id),
                text=(
                    "Привет! Я консультант по базе знаний.\n\n"
                    "Можно просто написать вопрос в личном чате.\n"
                    "В группе используй команду /kb вопрос, /ask вопрос, "
                    "reply на сообщение бота или упоминание бота."
                ),
            )
            return

        question = self._extract_question(
            message=message,
            telegram=telegram,
            chat_type=chat_type,
            text=text,
        )

        logger.info(
            "Telegram response decision: chat_id=%s, message_id=%s, "
            "chat_type=%s, should_respond=%s, question_size=%s",
            chat_id,
            message_id,
            chat_type,
            bool(question),
            len(question),
        )

        if not question:
            if chat_type in {"private", "group", "supergroup"} and self._is_supported_trigger(
                message=message,
                telegram=telegram,
                chat_type=chat_type,
                text=text,
            ):
                self._send_plain(
                    telegram=telegram,
                    chat_id=int(chat_id),
                    reply_to_message_id=int(message_id),
                    text="Напиши вопрос текстом.",
                )
            return

        self._process_question(
            telegram=telegram,
            chat_id=int(chat_id),
            reply_to_message_id=int(message_id),
            question=question,
        )

    def _process_question(
        self,
        telegram: TelegramBotApiClient,
        chat_id: int,
        reply_to_message_id: int,
        question: str,
    ) -> None:
        logger.info(
            "Telegram knowledge base consultation request started: "
            "kb_key=%s, chat_id=%s, reply_to_message_id=%s, question_size=%s",
            self.kb_key,
            chat_id,
            reply_to_message_id,
            len(question),
        )

        processing_message: dict[str, Any] | None = None

        try:
            telegram.send_chat_action(
                chat_id=chat_id,
                action="typing",
            )

            processing_message = telegram.send_message(
                chat_id=chat_id,
                text="Принял вопрос в работу.",
                reply_to_message_id=reply_to_message_id,
            )

            result = self._run_workflow(question)

            response_message = self._format_success_response(
                question=question,
                result=result,
            )

            self._delete_processing_message(
                telegram=telegram,
                chat_id=chat_id,
                processing_message=processing_message,
            )
            processing_message = None

            self._send_markdown(
                telegram=telegram,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text=response_message,
            )

            logger.info(
                "Telegram knowledge base consultation request completed: "
                "kb_key=%s, chat_id=%s, reply_to_message_id=%s",
                self.kb_key,
                chat_id,
                reply_to_message_id,
            )

        except HTTPException as exc:
            logger.exception(
                "Telegram knowledge base consultation request failed with HTTPException: "
                "kb_key=%s, chat_id=%s, reply_to_message_id=%s",
                self.kb_key,
                chat_id,
                reply_to_message_id,
            )

            self._delete_processing_message(
                telegram=telegram,
                chat_id=chat_id,
                processing_message=processing_message,
            )

            self._send_plain(
                telegram=telegram,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text=self._format_http_error_response(exc),
            )

        except Exception as exc:
            logger.exception(
                "Telegram knowledge base consultation request failed: "
                "kb_key=%s, chat_id=%s, reply_to_message_id=%s",
                self.kb_key,
                chat_id,
                reply_to_message_id,
            )

            self._delete_processing_message(
                telegram=telegram,
                chat_id=chat_id,
                processing_message=processing_message,
            )

            self._send_plain(
                telegram=telegram,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text=(
                    "Не удалось выполнить консультацию по базе знаний.\n\n"
                    f"Ошибка: {self._safe_inline(str(exc))}"
                ),
            )

    def _run_workflow(
        self,
        question: str,
    ) -> dict:
        session: Session | None = None

        try:
            session = self.session_factory()
            workflow = self.workflow_factory(session)

            return workflow.run(
                KnowledgeBaseConsultationRequest(
                    kb_key=self.kb_key,
                    question=question,
                )
            )

        except Exception:
            if session is not None:
                session.rollback()
            raise

        finally:
            if session is not None:
                session.close()

    def _extract_question(
        self,
        message: dict[str, Any],
        telegram: TelegramBotApiClient,
        chat_type: str,
        text: str,
    ) -> str:
        if not text:
            return ""

        command_question = self._extract_command_question(text)

        if command_question is not None:
            return command_question.strip()

        if chat_type == "private":
            return text.strip()

        if self._is_reply_to_bot(message, telegram):
            return text.strip()

        bot_mention = self._bot_mention(telegram)

        if bot_mention and text.lower().startswith(bot_mention):
            return text[len(bot_mention) :].lstrip(" \t,:;-")

        if self._has_bot_mention(message, telegram):
            return self._remove_bot_mention(text, telegram).strip()

        return ""

    def _extract_command_question(
        self,
        text: str,
    ) -> str | None:
        if not text:
            return None

        first_token, _, rest = text.partition(" ")
        normalized_command = first_token.split("@", 1)[0].lower()

        if normalized_command not in {"/kb", "/ask"}:
            return None

        return rest.strip()

    def _is_supported_trigger(
        self,
        message: dict[str, Any],
        telegram: TelegramBotApiClient,
        chat_type: str,
        text: str,
    ) -> bool:
        if chat_type == "private":
            return True

        if self._extract_command_question(text) is not None:
            return True

        if self._is_reply_to_bot(message, telegram):
            return True

        if self._has_bot_mention(message, telegram):
            return True

        return False

    def _is_start_command(
        self,
        text: str,
    ) -> bool:
        if not text:
            return False

        first_token = text.split(maxsplit=1)[0]
        normalized_command = first_token.split("@", 1)[0].lower()

        return normalized_command == "/start"

    def _is_reply_to_bot(
        self,
        message: dict[str, Any],
        telegram: TelegramBotApiClient,
    ) -> bool:
        reply_to_message = message.get("reply_to_message") or {}
        reply_from = reply_to_message.get("from") or {}

        return bool(
            telegram.bot_id is not None
            and reply_from.get("id") == telegram.bot_id
        )

    def _has_bot_mention(
        self,
        message: dict[str, Any],
        telegram: TelegramBotApiClient,
    ) -> bool:
        text = message.get("text") or message.get("caption") or ""
        bot_mention = self._bot_mention(telegram)

        if not bot_mention or not text:
            return False

        for entity in list(message.get("entities") or []) + list(message.get("caption_entities") or []):
            if entity.get("type") != "mention":
                continue

            offset = int(entity.get("offset") or 0)
            length = int(entity.get("length") or 0)
            mention_text = text[offset : offset + length]

            if mention_text.lower() == bot_mention:
                return True

        return False

    def _remove_bot_mention(
        self,
        text: str,
        telegram: TelegramBotApiClient,
    ) -> str:
        bot_mention = self._bot_mention(telegram)

        if not bot_mention:
            return text

        return re.sub(
            pattern=re.escape(bot_mention),
            repl="",
            string=text,
            flags=re.IGNORECASE,
        ).strip()

    def _bot_mention(
        self,
        telegram: TelegramBotApiClient,
    ) -> str:
        if not telegram.bot_username:
            return ""

        return f"@{telegram.bot_username}".lower()

    def _format_success_response(
        self,
        question: str,
        result: dict,
    ) -> str:
        answer = (result.get("answer") or "").strip()
        limitations = (result.get("limitations") or "").strip()

        citation_data = self._build_local_citations(
            answer=answer,
            result=result,
        )

        answer = citation_data["answer"]
        source_urls = citation_data["source_urls"]

        sections = [
            answer or "Ответ не был сформирован.",
        ]

        # if limitations:
        #     sections.append(
        #         "**Ограничения:**\n"
        #         f"{limitations}"
        #     )

        sources_section = self._format_source_urls(source_urls)
        if sources_section:
            sections.append(sources_section)

        return "\n\n".join(section for section in sections if section)

    def _build_local_citations(
        self,
        answer: str,
        result: dict,
    ) -> dict[str, Any]:
        retrieved_chunks = result.get("retrieved_chunks") or []
        selected_sources = result.get("sources") or []

        answer_source_numbers = self._extract_answer_source_numbers(answer)
        llm_source_numbers = self._normalize_source_numbers(
            result.get("source_numbers") or []
        )

        ordered_source_numbers = self._merge_ordered_numbers(
            answer_source_numbers,
            llm_source_numbers,
        )

        explicit_sources_by_number = self._build_explicit_sources_by_number(
            source_numbers=llm_source_numbers,
            sources=selected_sources,
        )

        old_to_local: dict[int, int] = {}
        url_to_local: dict[str, int] = {}
        source_urls: list[str] = []

        for old_source_number in ordered_source_numbers:
            source = self._find_source_by_number(
                source_number=old_source_number,
                retrieved_chunks=retrieved_chunks,
                explicit_sources_by_number=explicit_sources_by_number,
            )

            if not source:
                continue

            source_url = (source.get("source_url") or "").strip()

            if not source_url:
                continue

            local_number = url_to_local.get(source_url)

            if local_number is None:
                source_urls.append(source_url)
                local_number = len(source_urls)
                url_to_local[source_url] = local_number

            old_to_local[old_source_number] = local_number

        rewritten_answer = self._replace_answer_citations(
            answer=answer,
            old_to_local=old_to_local,
        )

        return {
            "answer": rewritten_answer,
            "source_urls": source_urls,
            "old_to_local": old_to_local,
        }

    def _extract_answer_source_numbers(
        self,
        answer: str,
    ) -> list[int]:
        numbers: list[int] = []

        for match in re.finditer(r"\[([0-9,\s]+)\]", answer or ""):
            raw_numbers = re.split(r"[,\s]+", match.group(1).strip())

            for raw_number in raw_numbers:
                if not raw_number:
                    continue

                try:
                    numbers.append(int(raw_number))
                except ValueError:
                    continue

        return self._merge_ordered_numbers(numbers)

    def _normalize_source_numbers(
        self,
        source_numbers: list[Any],
    ) -> list[int]:
        normalized: list[int] = []

        for source_number in source_numbers:
            try:
                value = int(source_number)
            except (TypeError, ValueError):
                continue

            if value <= 0:
                continue

            normalized.append(value)

        return self._merge_ordered_numbers(normalized)

    def _merge_ordered_numbers(
        self,
        *number_groups: list[int],
    ) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()

        for numbers in number_groups:
            for number in numbers:
                if number in seen:
                    continue

                seen.add(number)
                result.append(number)

        return result

    def _build_explicit_sources_by_number(
        self,
        source_numbers: list[int],
        sources: list[dict],
    ) -> dict[int, dict]:
        result: dict[int, dict] = {}

        for index, source in enumerate(sources):
            source_number = source.get("source_number")

            if source_number is None and index < len(source_numbers):
                source_number = source_numbers[index]

            try:
                normalized_source_number = int(source_number)
            except (TypeError, ValueError):
                continue

            if normalized_source_number <= 0:
                continue

            result[normalized_source_number] = source

        return result

    def _find_source_by_number(
        self,
        source_number: int,
        retrieved_chunks: list[dict],
        explicit_sources_by_number: dict[int, dict],
    ) -> dict | None:
        explicit_source = explicit_sources_by_number.get(source_number)

        if explicit_source:
            return explicit_source

        index = source_number - 1

        if index < 0 or index >= len(retrieved_chunks):
            return None

        source = retrieved_chunks[index]

        if not isinstance(source, dict):
            return None

        return source

    def _replace_answer_citations(
        self,
        answer: str,
        old_to_local: dict[int, int],
    ) -> str:
        if not answer or not old_to_local:
            return answer

        def repl(match: re.Match) -> str:
            old_numbers: list[int] = []

            for raw_number in re.split(r"[,\s]+", match.group(1).strip()):
                if not raw_number:
                    continue

                try:
                    old_numbers.append(int(raw_number))
                except ValueError:
                    continue

            local_numbers: list[int] = []
            seen_local_numbers: set[int] = set()

            for old_number in old_numbers:
                local_number = old_to_local.get(old_number)

                if local_number is None:
                    continue

                if local_number in seen_local_numbers:
                    continue

                seen_local_numbers.add(local_number)
                local_numbers.append(local_number)

            if not local_numbers:
                return ""

            return "[" + ", ".join(str(number) for number in local_numbers) + "]"

        rewritten = re.sub(r"\[([0-9,\s]+)\]", repl, answer)
        rewritten = re.sub(r"\s+([.,;:!?])", r"\1", rewritten)
        rewritten = re.sub(r" {2,}", " ", rewritten)

        return rewritten.strip()

    def _format_source_urls(
        self,
        source_urls: list[str],
    ) -> str:
        if not source_urls:
            return ""

        lines = ["**Источники:**"]

        for index, url in enumerate(source_urls, start=1):
            lines.append(f"{index}. [{url}]({url})")

        return "\n".join(lines)

    def _format_http_error_response(
        self,
        exc: HTTPException,
    ) -> str:
        return (
            "Не удалось выполнить консультацию по базе знаний.\n\n"
            f"HTTP status: {exc.status_code}\n"
            f"Ошибка: {self._safe_inline(str(exc.detail))}"
        )

    def _send_markdown(
        self,
        telegram: TelegramBotApiClient,
        chat_id: int,
        reply_to_message_id: int,
        text: str,
    ) -> None:
        if not text or not text.strip():
            self._send_plain(
                telegram=telegram,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text="Ответ получился пустым. Попробуй спросить иначе.",
            )
            return

        try:
            markdown_v2_text = convert_to_md_v2(text)
            chunks = split_md_v2(
                markdown_v2_text,
                limit=self.max_message_chars,
            )
        except Exception:
            logger.exception("Telegram MarkdownV2 conversion failed")
            self._send_plain(
                telegram=telegram,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text=(
                    "Ответ был сформирован, но не удалось подготовить MarkdownV2. "
                    "Отправляю текст без форматирования."
                ),
            )
            self._send_plain(
                telegram=telegram,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text=text,
            )
            return

        if not chunks:
            self._send_plain(
                telegram=telegram,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text="Ответ получился пустым. Попробуй спросить иначе.",
            )
            return

        logger.info(
            "Telegram sending MarkdownV2 response: chat_id=%s, "
            "reply_to_message_id=%s, chunks=%s",
            chat_id,
            reply_to_message_id,
            len(chunks),
        )

        for chunk in chunks:
            try:
                telegram.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=reply_to_message_id,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                )
            except Exception:
                logger.exception(
                    "Telegram failed to send MarkdownV2 chunk. Falling back to plain text. "
                    "chat_id=%s, reply_to_message_id=%s",
                    chat_id,
                    reply_to_message_id,
                )

                self._send_plain(
                    telegram=telegram,
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                    text="Telegram не принял Markdown-разметку. Отправляю ответ без форматирования.",
                )
                self._send_plain(
                    telegram=telegram,
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                    text=text,
                )
                break

    def _send_plain(
        self,
        telegram: TelegramBotApiClient,
        chat_id: int,
        reply_to_message_id: int,
        text: str,
    ) -> None:
        chunks = split_plain_text(
            text,
            limit=self.max_message_chars,
        )

        logger.info(
            "Telegram sending plain response: chat_id=%s, "
            "reply_to_message_id=%s, chunks=%s",
            chat_id,
            reply_to_message_id,
            len(chunks),
        )

        for chunk in chunks:
            telegram.send_message(
                chat_id=chat_id,
                text=chunk,
                reply_to_message_id=reply_to_message_id,
            )

    def _delete_processing_message(
        self,
        telegram: TelegramBotApiClient,
        chat_id: int,
        processing_message: dict[str, Any] | None,
    ) -> None:
        if not processing_message:
            return

        message_id = processing_message.get("message_id")

        if message_id is None:
            return

        try:
            telegram.delete_message(
                chat_id=chat_id,
                message_id=int(message_id),
            )
        except Exception:
            logger.warning(
                "Failed to delete Telegram processing message: chat_id=%s, message_id=%s",
                chat_id,
                message_id,
                exc_info=True,
            )

    def _is_allowed_chat(
        self,
        chat_id: int,
    ) -> bool:
        if not self.allowed_chat_ids:
            return True

        return chat_id in self.allowed_chat_ids

    def _safe_inline(
        self,
        value: str,
    ) -> str:
        return " ".join(value.replace("`", "'").split())[:1000]
