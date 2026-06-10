from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from app.infrastructure.telegram.client import TelegramBotApiClient


logger = logging.getLogger(__name__)


class TelegramLongPollingBotRunner:
    def __init__(
        self,
        name: str,
        client: TelegramBotApiClient,
        on_update: Callable[[dict[str, Any], TelegramBotApiClient], None],
        reconnect_seconds: int = 5,
        poll_timeout: int = 30,
        drop_pending_updates: bool = False,
    ):
        self.name = name
        self.client = client
        self.on_update = on_update
        self.reconnect_seconds = max(reconnect_seconds, 1)
        self.poll_timeout = max(1, poll_timeout)
        self.drop_pending_updates = drop_pending_updates

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("Telegram bot runner is already running: name=%s", self.name)
            return

        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run_forever,
            name=f"telegram-long-polling-{self.name}",
            daemon=True,
        )
        self._thread.start()

        logger.info("Telegram bot runner started: name=%s", self.name)

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

        logger.info("Telegram bot runner stopped: name=%s", self.name)

    def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_polling_loop()
            except Exception:
                logger.exception(
                    "Telegram polling loop failed: name=%s",
                    self.name,
                )

            if not self._stop_event.is_set():
                logger.info(
                    "Telegram polling will restart: name=%s, reconnect_seconds=%s",
                    self.name,
                    self.reconnect_seconds,
                )
                time.sleep(self.reconnect_seconds)

    def _run_polling_loop(self) -> None:
        self._initialize_bot()

        offset: int | None = None

        if self.drop_pending_updates:
            updates = self.client.get_updates(
                offset=None,
                timeout=1,
                allowed_updates=self._allowed_updates(),
            )

            if updates:
                offset = max(int(update["update_id"]) for update in updates) + 1

            logger.info(
                "Telegram pending updates dropped: name=%s, dropped_count=%s, next_offset=%s",
                self.name,
                len(updates),
                offset,
            )

        logger.info(
            "Telegram long polling started: name=%s, poll_timeout=%s",
            self.name,
            self.poll_timeout,
        )

        while not self._stop_event.is_set():
            updates = self.client.get_updates(
                offset=offset,
                timeout=self.poll_timeout,
                allowed_updates=self._allowed_updates(),
            )

            if not updates:
                continue

            logger.info(
                "Telegram updates received: name=%s, count=%s, first_update_id=%s, last_update_id=%s",
                self.name,
                len(updates),
                updates[0].get("update_id"),
                updates[-1].get("update_id"),
            )

            for update in updates:
                update_id = int(update["update_id"])
                offset = update_id + 1

                try:
                    self._handle_update(update)
                except Exception:
                    logger.exception(
                        "Telegram update handling failed: name=%s, update=%s",
                        self.name,
                        self._safe_update_repr(update),
                    )

    def _initialize_bot(self) -> None:
        me = self.client.get_me()

        logger.info(
            "Telegram bot identity loaded: name=%s, bot_id=%s, username=%s, "
            "is_bot=%s, can_join_groups=%s, can_read_all_group_messages=%s",
            self.name,
            me.get("id"),
            me.get("username"),
            me.get("is_bot"),
            me.get("can_join_groups"),
            me.get("can_read_all_group_messages"),
        )

        webhook_info = self.client.get_webhook_info()
        webhook_url = webhook_info.get("url") or ""

        logger.info(
            "Telegram webhook info loaded: name=%s, has_webhook=%s, "
            "pending_update_count=%s, last_error_date=%s, last_error_message=%s",
            self.name,
            bool(webhook_url),
            webhook_info.get("pending_update_count"),
            webhook_info.get("last_error_date"),
            webhook_info.get("last_error_message"),
        )

        if webhook_url:
            logger.warning(
                "Telegram webhook is configured. Deleting webhook before long polling: "
                "name=%s, drop_pending_updates=%s",
                self.name,
                self.drop_pending_updates,
            )

            self.client.delete_webhook(
                drop_pending_updates=self.drop_pending_updates,
            )

            logger.info(
                "Telegram webhook deleted: name=%s",
                self.name,
            )

    def _handle_update(
        self,
        update: dict[str, Any],
    ) -> None:
        logger.info(
            "Telegram update received: name=%s, update=%s",
            self.name,
            self._safe_update_repr(update),
        )

        self.on_update(update, self.client)

    def _allowed_updates(self) -> list[str]:
        return [
            "message",
            "edited_message",
        ]

    def _safe_update_repr(
        self,
        update: dict[str, Any],
    ) -> str:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}

        return str(
            {
                "update_id": update.get("update_id"),
                "has_message": bool(update.get("message")),
                "has_edited_message": bool(update.get("edited_message")),
                "chat_id": chat.get("id"),
                "chat_type": chat.get("type"),
                "message_id": message.get("message_id"),
                "from_user_id": from_user.get("id"),
                "has_text": bool(message.get("text")),
                "has_caption": bool(message.get("caption")),
                "has_document": bool(message.get("document")),
                "has_photo": bool(message.get("photo")),
            }
        )
