import logging

from app.application.skills.code_review.context_builder import ContextBuilder
from app.application.skills.code_review.prompts import SYSTEM_PROMPT
from app.application.skills.code_review.schemas import ReviewResponse, TaskInfo
from app.components.llm.structured_client import StructuredLLMClient
from app.components.review.comment_publisher import ReviewCommentPublisher
from app.domain.reviews import ReviewIssue
from app.infrastructure.gitlab.client import GitLabClient
from app.infrastructure.jira.client import JiraClient
from app.schemas.api import ReviewRequest


logger = logging.getLogger(__name__)


class CodeReviewWorkflow:
    def __init__(
        self,
        llm: StructuredLLMClient,
        context_builder: ContextBuilder,
        gitlab: GitLabClient,
        jira: JiraClient,
        comment_publisher: ReviewCommentPublisher,
    ):
        self.llm = llm
        self.context_builder = context_builder
        self.gitlab = gitlab
        self.jira = jira
        self.comment_publisher = comment_publisher

    def run(self, agent_context_path: str, review_data: ReviewRequest):
        logger.info(
            "Code review started: jira_issue_key=%s, mr_iid=%s",
            review_data.jira_issue_key,
            review_data.mr_iid,
        )

        task_data_raw = self.jira.get_task(review_data.jira_issue_key)
        merge_request_data = self.gitlab.get_merge_request_data(review_data.mr_iid)

        logger.info(
            "Review input loaded: jira_issue_key=%s, mr_iid=%s, target_branch=%s, diff_files=%s",
            review_data.jira_issue_key,
            review_data.mr_iid,
            merge_request_data.get("target_branch"),
            len(merge_request_data.get("diffs", [])),
        )

        task_data = TaskInfo(
            id=task_data_raw["id"],
            type=task_data_raw["type"],
            title=task_data_raw["title"],
            description=task_data_raw["description"],
        )

        agent_context = self.gitlab.get_raw_file(
            file_path=agent_context_path,
            ref=merge_request_data["target_branch"],
        )

        logger.info(
            "Agent context loaded: path=%s, ref=%s, size=%s",
            agent_context_path,
            merge_request_data["target_branch"],
            len(agent_context),
        )

        user_message = self.context_builder.prompt_build(
            agent_context=agent_context,
            task_data=task_data,
            merge_request_data=merge_request_data,
        )

        llm_result = self.llm.response(
            system_prompt=SYSTEM_PROMPT,
            user_message=user_message,
            response_model=ReviewResponse,
        )

        raw_issues = llm_result.get("issues", [])

        logger.info(
            "LLM review completed: jira_issue_key=%s, mr_iid=%s, issues_count=%s",
            review_data.jira_issue_key,
            review_data.mr_iid,
            len(raw_issues),
        )

        published_comments = []
        validated_issues = []

        for raw_issue in raw_issues:
            issue = ReviewIssue.model_validate(raw_issue)
            validated_issues.append(issue.model_dump())

            published_comments.append(
                self.comment_publisher.publish_issue(
                    mr_iid=review_data.mr_iid,
                    issue=issue,
                    merge_request_data=merge_request_data,
                )
            )

        logger.info(
            "Code review finished: jira_issue_key=%s, mr_iid=%s, issues_count=%s, published_comments_count=%s",
            review_data.jira_issue_key,
            review_data.mr_iid,
            len(validated_issues),
            len(published_comments),
        )

        return {
            "task": task_data_raw,
            "merge_request": merge_request_data,
            "issues": validated_issues,
            "published_comments": published_comments,
        }
