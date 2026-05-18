import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.infrastructure.mattermost.client import MattermostClient


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MattermostDirectMessage:
    post_id: str
    channel_id: str
    user_id: str
    message: str
    create_at: int | None = None
    raw_post: dict[str, Any] | None = None


class MattermostWebSocketBotRunner:
    def __init__(
        self,
        name: str,
        client: MattermostClient,
        on_direct_message: Callable[[MattermostDirectMessage], None],
        reconnect_seconds: int = 5,
        worker_count: int = 2,
    ):
        self.name = name
        self.client = client
        self.on_direct_message = on_direct_message
        self.reconnect_seconds = max(reconnect_seconds, 1)
        self.worker_count = max(worker_count, 1)

        self.bot_user_id: str | None = None

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=self.worker_count,
            thread_name_prefix=f"mattermost-{self.name}",
        )
        self._websocket_app = None
        self._seq = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("Mattermost bot runner is already running: name=%s", self.name)
            return

        me = self.client.get_me()
        self.bot_user_id = me.get("id")

        if not self.bot_user_id:
            raise RuntimeError(f"Mattermost bot user id was not returned: name={self.name}")

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_forever,
            name=f"mattermost-ws-{self.name}",
            daemon=True,
        )
        self._thread.start()

        logger.info(
            "Mattermost bot runner started: name=%s, bot_user_id=%s",
            self.name,
            self.bot_user_id,
        )

    def stop(self) -> None:
        self._stop_event.set()

        if self._websocket_app is not None:
            try:
                self._websocket_app.close()
            except Exception:
                logger.exception(
                    "Failed to close Mattermost websocket app: name=%s",
                    self.name,
                )

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

        self._executor.shutdown(wait=False, cancel_futures=True)

        logger.info("Mattermost bot runner stopped: name=%s", self.name)

    def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_once()
            except Exception:
                logger.exception("Mattermost websocket runner failed: name=%s", self.name)

            if not self._stop_event.is_set():
                time.sleep(self.reconnect_seconds)

    def _run_once(self) -> None:
        from websocket import WebSocketApp

        websocket_url = self._websocket_url()

        logger.info(
            "Mattermost websocket connecting: name=%s, url=%s",
            self.name,
            websocket_url,
        )

        self._websocket_app = WebSocketApp(
            websocket_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._websocket_app.run_forever(
            ping_interval=30,
            ping_timeout=10,
        )

    def _websocket_url(self) -> str:
        base_url = self.client.base_url

        if base_url.startswith("https://"):
            websocket_base = "wss://" + base_url[len("https://") :]
        elif base_url.startswith("http://"):
            websocket_base = "ws://" + base_url[len("http://") :]
        else:
            websocket_base = "wss://" + base_url

        return f"{websocket_base}/api/v4/websocket"

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _on_open(self, websocket) -> None:
        logger.info("Mattermost websocket opened: name=%s", self.name)

        websocket.send(
            json.dumps(
                {
                    "seq": self._next_seq(),
                    "action": "authentication_challenge",
                    "data": {
                        "token": self.client.token,
                    },
                }
            )
        )

    def _on_message(self, websocket, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(
                "Mattermost websocket returned non-json message: name=%s, message=%s",
                self.name,
                message,
            )
            return

        event = payload.get("event")

        if event == "hello":
            logger.info("Mattermost websocket hello received: name=%s", self.name)
            return

        if event != "posted":
            return

        self._handle_posted_event(payload)

    def _handle_posted_event(self, payload: dict[str, Any]) -> None:
        data = payload.get("data") or {}

        if data.get("channel_type") != "D":
            return

        raw_post = data.get("post")
        if not raw_post:
            return

        try:
            post = json.loads(raw_post)
        except json.JSONDecodeError:
            logger.warning(
                "Mattermost posted event has invalid post json: name=%s, post=%s",
                self.name,
                raw_post,
            )
            return

        post_type = post.get("type") or ""
        if post_type:
            return

        post_id = post.get("id") or ""
        channel_id = post.get("channel_id") or ""
        user_id = post.get("user_id") or ""
        message = (post.get("message") or "").strip()
        create_at = post.get("create_at")

        if not post_id or not channel_id or not user_id:
            return

        if self.bot_user_id and user_id == self.bot_user_id:
            return

        if not message:
            return

        direct_message = MattermostDirectMessage(
            post_id=post_id,
            channel_id=channel_id,
            user_id=user_id,
            message=message,
            create_at=create_at,
            raw_post=post,
        )

        logger.info(
            "Mattermost direct message received: name=%s, channel_id=%s, "
            "user_id=%s, post_id=%s, message_size=%s",
            self.name,
            channel_id,
            user_id,
            post_id,
            len(message),
        )

        self._executor.submit(self._safe_handle_direct_message, direct_message)

    def _safe_handle_direct_message(self, message: MattermostDirectMessage) -> None:
        try:
            self.on_direct_message(message)
        except Exception:
            logger.exception(
                "Mattermost direct message handler failed: name=%s, channel_id=%s, "
                "user_id=%s, post_id=%s",
                self.name,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

    def _on_error(self, websocket, error) -> None:
        logger.error(
            "Mattermost websocket error: name=%s, error=%s",
            self.name,
            error,
        )

    def _on_close(self, websocket, close_status_code, close_msg) -> None:
        logger.warning(
            "Mattermost websocket closed: name=%s, status=%s, message=%s",
            self.name,
            close_status_code,
            close_msg,
        )
