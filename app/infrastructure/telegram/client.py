from __future__ import annotations

import logging
from typing import Any

import requests


logger = logging.getLogger(__name__)


class TelegramBotApiClient:
    def __init__(
        self,
        token: str,
        api_base_url: str = "https://api.telegram.org/bot",
        api_file_base_url: str = "https://api.telegram.org/file/bot",
        proxy_url: str | None = None,
        connect_timeout: float = 30.0,
        read_timeout: float = 60.0,
    ):
        if not token:
            raise RuntimeError("Telegram bot token is not configured")

        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.api_file_base_url = api_file_base_url.rstrip("/")
        self.proxy_url = (proxy_url or "").strip()
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

        self.session = requests.Session()

        self.bot_id: int | None = None
        self.bot_username: str | None = None

    def get_me(self) -> dict[str, Any]:
        result = self._request(
            method="get",
            endpoint="getMe",
        )

        user = result.get("result") or {}

        self.bot_id = user.get("id")
        self.bot_username = user.get("username")

        return user

    def get_webhook_info(self) -> dict[str, Any]:
        result = self._request(
            method="get",
            endpoint="getWebhookInfo",
        )

        return result.get("result") or {}

    def delete_webhook(
        self,
        drop_pending_updates: bool = False,
    ) -> dict[str, Any]:
        result = self._request(
            method="post",
            endpoint="deleteWebhook",
            json={
                "drop_pending_updates": drop_pending_updates,
            },
        )

        return result.get("result") or {}

    def get_updates(
        self,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
        }

        if offset is not None:
            payload["offset"] = offset

        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates

        result = self._request(
            method="post",
            endpoint="getUpdates",
            json=payload,
            timeout=(
                self.connect_timeout,
                max(self.read_timeout, timeout + 10),
            ),
        )

        updates = result.get("result") or []

        if not isinstance(updates, list):
            logger.warning(
                "Telegram getUpdates returned non-list result: result_type=%s",
                type(updates).__name__,
            )
            return []

        return updates

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }

        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True

        if parse_mode:
            payload["parse_mode"] = parse_mode

        result = self._request(
            method="post",
            endpoint="sendMessage",
            json=payload,
        )

        return result.get("result") or {}

    def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> bool:
        result = self._request(
            method="post",
            endpoint="deleteMessage",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )

        return bool(result.get("result"))

    def send_chat_action(
        self,
        chat_id: int,
        action: str,
    ) -> bool:
        result = self._request(
            method="post",
            endpoint="sendChatAction",
            json={
                "chat_id": chat_id,
                "action": action,
            },
        )

        return bool(result.get("result"))

    def _request(
        self,
        method: str,
        endpoint: str,
        json: dict[str, Any] | None = None,
        timeout: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_base_url}{self.token}/{endpoint}"

        request_timeout = timeout or (
            self.connect_timeout,
            self.read_timeout,
        )

        proxies = None
        if self.proxy_url:
            proxies = {
                "http": self.proxy_url,
                "https": self.proxy_url,
            }

        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                json=json,
                timeout=request_timeout,
                proxies=proxies,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Telegram API request failed: endpoint={endpoint}, error={exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Telegram API returned non-JSON response: "
                f"endpoint={endpoint}, status_code={response.status_code}"
            ) from exc

        if not response.ok or not payload.get("ok"):
            description = payload.get("description") or response.text

            raise RuntimeError(
                "Telegram API request returned error: "
                f"endpoint={endpoint}, status_code={response.status_code}, "
                f"description={description}"
            )

        return payload
