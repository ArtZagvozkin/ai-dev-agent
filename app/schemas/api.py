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


class CodebaseConsultationRequest(BaseModel):
    repository_path: str = Field(min_length=1)
    question: str = Field(min_length=1)
    top_k: int = Field(default=6, ge=1, le=20)
    max_files: int = Field(default=2_000, ge=1, le=10_000)
    max_file_bytes: int = Field(default=200_000, ge=1, le=5_000_000)
    force_reindex: bool = False
    include_full_code_units: bool = True


class CodebaseConsultationSource(BaseModel):
    chunk_id: str
    parent_chunk_id: str | None = None
    chunk_type: str
    path: str
    language: str
    start_line: int
    end_line: int
    symbol: str | None = None
    ast_node_type: str | None = None
    declaration_type: str | None = None
    parent_symbol: str | None = None
    keywords: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    top_level_symbols: list[str] = Field(default_factory=list)
    score: float
    snippet: str
    contextualized_text: str
    code_unit: str | None = None
    is_full_code_unit: bool = False


class CodebaseConsultationRetrievedChunk(CodebaseConsultationSource):
    bm25_score: float
    vector_score: float
    combined_score: float


class CodebaseConsultationIndexStats(BaseModel):
    repository_path: str
    files_indexed: int
    chunks_indexed: int


class CodebaseConsultationResponse(BaseModel):
    answer: str
    sources: list[CodebaseConsultationSource] = Field(default_factory=list)
    retrieved_chunks: list[CodebaseConsultationRetrievedChunk] = Field(default_factory=list)
    index_stats: CodebaseConsultationIndexStats


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
