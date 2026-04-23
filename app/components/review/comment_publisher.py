import logging

from fastapi import HTTPException

from app.components.diff.localizer import DiffLineLocalizer
from app.domain.reviews import ReviewIssue
from app.infrastructure.gitlab.client import GitLabClient


logger = logging.getLogger(__name__)


class ReviewCommentPublisher:
    def __init__(
        self,
        gitlab: GitLabClient,
        localizer: DiffLineLocalizer,
    ):
        self.gitlab = gitlab
        self.localizer = localizer

    def _build_note_body(self, issue: ReviewIssue) -> str:
        header = (
            f"AI review: {issue.problem_type}, "
            f"severity {issue.severity_score}/10, "
            f"confidence {issue.confidence_score}/10"
        )

        if issue.scope == "file" and issue.file_path:
            return f"{header}\n\nFile: `{issue.file_path}`\n\n{issue.comment}"

        if issue.scope == "mr":
            return f"{header}\n\n{issue.comment}"

        if issue.scope == "line" and issue.file_path:
            return (
                f"{header}\n\n"
                f"File: `{issue.file_path}`\n\n"
                f"Anchor: `{issue.anchor_text}`\n\n"
                f"{issue.comment}"
            )

        return f"{header}\n\n{issue.comment}"

    def _find_file_diff(
        self,
        issue: ReviewIssue,
        merge_request_data: dict,
    ) -> dict | None:
        if not issue.file_path:
            return None

        for file_diff in merge_request_data.get("diffs", []):
            if file_diff.get("new_path") == issue.file_path:
                return file_diff

        return None

    def _publish_failed(self, issue: ReviewIssue, error: HTTPException) -> dict:
        return {
            "scope": issue.scope,
            "file_path": issue.file_path,
            "comment": issue.comment,
            "publication_mode": "failed",
            "discussion": None,
            "publish_error": str(error.detail),
        }

    def _publish_mr_note(
        self,
        mr_iid: int,
        issue: ReviewIssue,
        publication_mode: str,
        fallback_reason: str | None = None,
    ) -> dict:
        body = self._build_note_body(issue)

        if fallback_reason:
            body += f"\n\nPublication fallback: {fallback_reason}"

        note = self.gitlab.create_merge_request_note(
            mr_iid=mr_iid,
            body=body,
        )

        logger.info(
            "Published MR review note: mr_iid=%s, scope=%s, file_path=%s, mode=%s",
            mr_iid,
            issue.scope,
            issue.file_path,
            publication_mode,
        )

        return {
            "scope": issue.scope,
            "file_path": issue.file_path,
            "comment": issue.comment,
            "publication_mode": publication_mode,
            "discussion": note,
            "publish_error": None,
        }

    def _publish_line_issue(
        self,
        mr_iid: int,
        issue: ReviewIssue,
        merge_request_data: dict,
    ) -> dict:
        file_diff = self._find_file_diff(issue, merge_request_data)

        if not file_diff:
            logger.warning(
                "Line issue fallback: file_path was not found in MR diff, mr_iid=%s, file_path=%s",
                mr_iid,
                issue.file_path,
            )

            return self._publish_mr_note(
                mr_iid=mr_iid,
                issue=issue,
                publication_mode="mr_note_fallback",
                fallback_reason="file_path was not found in MR diff",
            )

        localization = self.localizer.locate_line(
            file_path=issue.file_path,
            anchor_text=issue.anchor_text,
            before_anchor=issue.before_anchor,
            after_anchor=issue.after_anchor,
            file_diff=file_diff,
        )

        if not localization.ok or localization.new_line is None:
            logger.warning(
                "Line issue fallback: localization failed, mr_iid=%s, file_path=%s, reason=%s",
                mr_iid,
                issue.file_path,
                localization.reason,
            )

            return self._publish_mr_note(
                mr_iid=mr_iid,
                issue=issue,
                publication_mode="mr_note_fallback",
                fallback_reason=localization.reason,
            )

        if not localization.file_path:
            logger.warning(
                "Line issue fallback: localized file_path is empty, mr_iid=%s, original_file_path=%s",
                mr_iid,
                issue.file_path,
            )

            return self._publish_mr_note(
                mr_iid=mr_iid,
                issue=issue,
                publication_mode="mr_note_fallback",
                fallback_reason="localized file_path is empty",
            )

        discussion = self.gitlab.create_inline_comment(
            mr_iid=mr_iid,
            body=self._build_note_body(issue),
            new_path=localization.file_path,
            new_line=localization.new_line,
        )

        logger.info(
            "Published inline review comment: mr_iid=%s, file_path=%s, new_line=%s",
            mr_iid,
            localization.file_path,
            localization.new_line,
        )

        return {
            "scope": issue.scope,
            "file_path": issue.file_path,
            "comment": issue.comment,
            "publication_mode": "inline",
            "discussion": discussion,
            "publish_error": None,
        }

    def publish_issue(
        self,
        mr_iid: int,
        issue: ReviewIssue,
        merge_request_data: dict,
    ) -> dict:
        try:
            if issue.scope == "line":
                return self._publish_line_issue(
                    mr_iid=mr_iid,
                    issue=issue,
                    merge_request_data=merge_request_data,
                )

            return self._publish_mr_note(
                mr_iid=mr_iid,
                issue=issue,
                publication_mode="mr_note",
            )

        except HTTPException as e:
            logger.exception(
                "Failed to publish review comment: mr_iid=%s, scope=%s, file_path=%s",
                mr_iid,
                issue.scope,
                issue.file_path,
            )
            return self._publish_failed(issue, e)
