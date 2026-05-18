import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from fastapi import HTTPException

from app.application.skills.code_review.workflow import CodeReviewWorkflow
from app.core.config import Settings
from app.infrastructure.gitlab.client import GitLabClient
from app.infrastructure.mattermost.client import MattermostClient
from app.infrastructure.mattermost.websocket_bot import MattermostDirectMessage
from app.schemas.api import ReviewRequest


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MergeRequestReference:
    project_id: str
    mr_iid: int
    web_url: str


class MattermostCodeReviewBot:
    def __init__(
        self,
        mattermost: MattermostClient,
        settings: Settings,
        workflow_factory: Callable[[GitLabClient], CodeReviewWorkflow],
        max_post_chars: int = 12000,
    ):
        self.mattermost = mattermost
        self.settings = settings
        self.workflow_factory = workflow_factory
        self.max_post_chars = max(max_post_chars, 1000)
        self.jira_key_re = re.compile(settings.mattermost_code_reviewer_jira_key_pattern)

    def handle_direct_message(self, message: MattermostDirectMessage) -> None:
        request_text = message.message.strip()

        if not request_text:
            return

        logger.info(
            "Code review Mattermost request started: "
            "channel_id=%s, user_id=%s, post_id=%s, message_size=%s",
            message.channel_id,
            message.user_id,
            message.post_id,
            len(request_text),
        )

        reference = self._parse_merge_request_reference(request_text)

        if reference is None:
            self._send_long_message(
                channel_id=message.channel_id,
                message=(
                    "Не удалось найти ссылку на GitLab Merge Request.\n\n"
                    "Пришли ссылку в формате:\n"
                    "`https://gitlab.example.com/group/project/-/merge_requests/123`"
                ),
                root_id=message.post_id,
            )
            return

        gitlab = GitLabClient(
            base_url=self.settings.gitlab_url,
            token=self.settings.gitlab_token,
            project_id=reference.project_id,
        )

        try:
            start_message = (
                "Принял Merge Request на ревью.\n\n"
                f"MR: {reference.web_url}\n"
                f"Project: `{reference.project_id}`\n"
                f"MR IID: `!{reference.mr_iid}`\n\n"
                "Загружаю данные MR и Jira-задачу, затем опубликую найденные замечания в GitLab."
            )

            self.mattermost.create_post(
                channel_id=message.channel_id,
                message=start_message,
                root_id=message.post_id,
            )

            merge_request_data = gitlab.get_merge_request_data(reference.mr_iid)
            jira_issue_key = self._extract_jira_issue_key(
                request_text=request_text,
                merge_request_data=merge_request_data,
                reference=reference,
            )

            if not jira_issue_key:
                self._send_long_message(
                    channel_id=message.channel_id,
                    message=self._format_jira_key_not_found_response(
                        reference=reference,
                        merge_request_data=merge_request_data,
                    ),
                    root_id=message.post_id,
                )
                return

            workflow = self.workflow_factory(gitlab)

            result = workflow.run(
                agent_context_path=self.settings.agent_context_path,
                review_data=ReviewRequest(
                    jira_issue_key=jira_issue_key,
                    mr_iid=reference.mr_iid,
                ),
            )

            response_message = self._format_success_response(
                reference=reference,
                jira_issue_key=jira_issue_key,
                result=result,
            )

            self._send_long_message(
                channel_id=message.channel_id,
                message=response_message,
                root_id=message.post_id,
            )

            logger.info(
                "Code review Mattermost request completed: "
                "project_id=%s, mr_iid=%s, jira_issue_key=%s, channel_id=%s, user_id=%s, post_id=%s",
                reference.project_id,
                reference.mr_iid,
                jira_issue_key,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

        except HTTPException as exc:
            logger.exception(
                "Code review Mattermost request failed with HTTPException: "
                "project_id=%s, mr_iid=%s, channel_id=%s, user_id=%s, post_id=%s",
                reference.project_id,
                reference.mr_iid,
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
            logger.exception(
                "Code review Mattermost request failed: "
                "project_id=%s, mr_iid=%s, channel_id=%s, user_id=%s, post_id=%s",
                reference.project_id,
                reference.mr_iid,
                message.channel_id,
                message.user_id,
                message.post_id,
            )

            self._send_long_message(
                channel_id=message.channel_id,
                message=(
                    "Не удалось выполнить ревью Merge Request.\n\n"
                    f"Ошибка: `{self._safe_inline(str(exc))}`"
                ),
                root_id=message.post_id,
            )

    def _parse_merge_request_reference(
        self,
        text: str,
    ) -> MergeRequestReference | None:
        for raw_url in self._extract_urls(text):
            reference = self._parse_merge_request_url(raw_url)
            if reference is not None:
                return reference

        return None

    def _extract_urls(self, text: str) -> list[str]:
        urls = []

        for match in re.finditer(r"https?://\S+", text):
            value = match.group(0).strip()

            if "|" in value:
                value = value.split("|", 1)[0]

            value = value.strip("<>()[]{}.,;:'\"")

            while value.endswith((")", "]", "}", ".", ",", ";", ":")):
                value = value[:-1]

            if value:
                urls.append(value)

        return urls

    def _parse_merge_request_url(
        self,
        raw_url: str,
    ) -> MergeRequestReference | None:
        parsed = urlparse(raw_url)
        base = urlparse(self.settings.gitlab_url)

        if parsed.scheme not in {"http", "https"}:
            return None

        if base.netloc and parsed.netloc != base.netloc:
            logger.warning(
                "Ignoring GitLab MR URL from another host: expected=%s, actual=%s, url=%s",
                base.netloc,
                parsed.netloc,
                raw_url,
            )
            return None

        path = parsed.path

        base_path = base.path.rstrip("/")
        if base_path and path.startswith(f"{base_path}/"):
            path = path[len(base_path) :]

        match = re.match(
            r"^/(?P<project_path>.+)/-/merge_requests/(?P<mr_iid>\d+)(?:/.*)?$",
            path,
        )

        if not match:
            return None

        project_path = unquote(match.group("project_path")).strip("/")
        mr_iid = int(match.group("mr_iid"))

        if not project_path or mr_iid <= 0:
            return None

        web_url = raw_url.split("?", 1)[0].split("#", 1)[0]

        return MergeRequestReference(
            project_id=project_path,
            mr_iid=mr_iid,
            web_url=web_url,
        )

    def _extract_jira_issue_key(
        self,
        request_text: str,
        merge_request_data: dict,
        reference: MergeRequestReference,
    ) -> str | None:
        candidates = [
            request_text,
            reference.web_url,
            merge_request_data.get("title", ""),
            merge_request_data.get("source_branch", ""),
            merge_request_data.get("description", ""),
        ]

        for candidate in candidates:
            match = self.jira_key_re.search(candidate or "")
            if match:
                return match.group(0)

        return None

    def _format_jira_key_not_found_response(
        self,
        reference: MergeRequestReference,
        merge_request_data: dict,
    ) -> str:
        title = merge_request_data.get("title") or "n/a"
        source_branch = merge_request_data.get("source_branch") or "n/a"

        return (
            "Не удалось определить Jira issue key для ревью.\n\n"
            f"MR: {reference.web_url}\n"
            f"Project: `{reference.project_id}`\n"
            f"MR IID: `!{reference.mr_iid}`\n"
            f"MR title: `{self._safe_inline(title)}`\n"
            f"Source branch: `{self._safe_inline(source_branch)}`\n\n"
            "Сейчас бот ищет Jira key в тексте сообщения, ссылке, названии MR, "
            "source branch и описании MR.\n\n"
            "Можно отправить сообщение так:\n"
            f"`{reference.web_url} JIRA-123`\n\n"
            "Или настроить регулярное выражение через "
            "`MATTERMOST_CODE_REVIEWER_JIRA_KEY_PATTERN`."
        )

    def _format_success_response(
        self,
        reference: MergeRequestReference,
        jira_issue_key: str,
        result: dict,
    ) -> str:
        issues = result.get("issues") or []
        published_comments = result.get("published_comments") or []
        merge_request = result.get("merge_request") or {}

        mr_url = merge_request.get("web_url") or reference.web_url

        sections = [
            self._format_summary(
                mr_url=mr_url,
                reference=reference,
                jira_issue_key=jira_issue_key,
                issues=issues,
                published_comments=published_comments,
            )
        ]

        if issues:
            sections.append(
                self._format_issues(
                    issues=issues,
                    published_comments=published_comments,
                )
            )

        return "\n\n".join(section for section in sections if section)

    def _format_summary(
        self,
        mr_url: str,
        reference: MergeRequestReference,
        jira_issue_key: str,
        issues: list[dict],
        published_comments: list[dict],
    ) -> str:
        issues_count = len(issues)
        published_count = sum(
            1
            for item in published_comments
            if not item.get("publish_error")
        )
        failed_count = sum(
            1
            for item in published_comments
            if item.get("publish_error")
        )

        inline_count = sum(
            1
            for item in published_comments
            if item.get("publication_mode") == "inline"
        )
        mr_note_count = sum(
            1
            for item in published_comments
            if item.get("publication_mode") in {"mr_note", "mr_note_fallback"}
        )

        lines = [
            "✅ **Code review выполнен.**",
            "",
            f"MR: {mr_url}",
            f"Project: `{reference.project_id}`",
            f"MR IID: `!{reference.mr_iid}`",
            f"Jira: `{jira_issue_key}`",
            "",
            f"Найдено замечаний: `{issues_count}`",
        ]

        if issues_count == 0:
            lines.append("Проблем по результатам ревью не найдено.")
            return "\n".join(lines)

        lines.extend(
            [
                f"Опубликовано комментариев в GitLab: `{published_count}`",
                f"Inline comments: `{inline_count}`",
                f"MR notes: `{mr_note_count}`",
            ]
        )

        if failed_count:
            lines.append(f"Не удалось опубликовать: `{failed_count}`")

        return "\n".join(lines)

    def _format_issues(
        self,
        issues: list[dict],
        published_comments: list[dict],
    ) -> str:
        lines = ["**Сводка замечаний:**"]

        for index, issue in enumerate(issues, start=1):
            published = (
                published_comments[index - 1]
                if index - 1 < len(published_comments)
                else {}
            )

            scope = issue.get("scope") or "unknown"
            problem_type = issue.get("problem_type") or "other"
            severity = issue.get("severity_score")
            confidence = issue.get("confidence_score")
            file_path = issue.get("file_path")
            comment = issue.get("comment") or ""

            publication_mode = published.get("publication_mode") or "unknown"
            publish_error = published.get("publish_error")
            discussion = published.get("discussion") or {}
            discussion_url = discussion.get("web_url")

            header = (
                f"{index}. `{problem_type}` / `{scope}` "
                f"severity `{severity}/10`, confidence `{confidence}/10`"
            )

            lines.append(header)

            if file_path:
                lines.append(f"   Файл: `{file_path}`")

            if discussion_url:
                lines.append(f"   GitLab comment: {discussion_url}")
            else:
                lines.append(f"   Publication mode: `{publication_mode}`")

            if publish_error:
                lines.append(f"   Publish error: `{self._safe_inline(str(publish_error))}`")

            if comment:
                lines.append(f"\n   Комментарий: {self._safe_inline(comment)}")

        return "\n".join(lines)

    def _format_error_response(self, exc: HTTPException) -> str:
        detail = exc.detail

        return (
            "Не удалось выполнить ревью Merge Request.\n\n"
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
        return " ".join(value.replace("`", "'").split())[:1000]
