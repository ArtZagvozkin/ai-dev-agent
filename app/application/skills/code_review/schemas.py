from pydantic import BaseModel, Field

from app.domain.reviews import ReviewIssue


class ReviewResponse(BaseModel):
    issues: list[ReviewIssue] = Field(default_factory=list)


class TaskInfo(BaseModel):
    id: str
    type: str
    title: str
    description: str
