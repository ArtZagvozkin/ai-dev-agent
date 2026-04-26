from pydantic import BaseModel, Field

from app.domain.reviews import ReviewIssue, ReviewScope


class JiraReviewer(BaseModel):
    account_id: str = ""
    display_name: str = ""
    email: str | None = None
    active: bool = False


class JiraTaskResponse(BaseModel):
    id: str
    type: str
    title: str
    description: str
    status: str = ""
    mr_url: str | None = None
    reviewers: list[JiraReviewer] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    jira_issue_key: str
    mr_iid: int


class GitLabUserInfo(BaseModel):
    id: int | None = None
    username: str = ""
    name: str = ""
    web_url: str | None = None


class GitLabMRResponse(BaseModel):
    id: int
    iid: int
    title: str
    description: str
    author: GitLabUserInfo
    created_at: str
    source_branch: str
    target_branch: str
    diff: str


class CreateInlineCommentRequest(BaseModel):
    body: str
    new_path: str
    new_line: int


class GitLabDiscussionResponse(BaseModel):
    id: str
    individual_note: bool
    notes_count: int
    web_url: str | None = None


class PublishedIssueComment(BaseModel):
    scope: ReviewScope
    file_path: str | None = None
    comment: str
    publication_mode: str
    discussion: dict | None = None
    publish_error: str | None = None


class ReviewWithPublishResponse(BaseModel):
    task: JiraTaskResponse
    merge_request: GitLabMRResponse
    issues: list[ReviewIssue] = Field(default_factory=list)
    published_comments: list[PublishedIssueComment] = Field(default_factory=list)

class LLMDiagnosticRequest(BaseModel):
    message: str = Field(min_length=1)


class LLMDiagnosticLLMResponse(BaseModel):
    output: str


class LLMDiagnosticResponse(BaseModel):
    ok: bool
    model: str
    input: str
    output: str


class MattermostTestMessageRequest(BaseModel):
    channel_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class MattermostPostResponse(BaseModel):
    id: str
    channel_id: str
    message: str
    user_id: str | None = None
    create_at: int | None = None
