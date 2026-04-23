from typing import Literal

from pydantic import BaseModel, Field, model_validator


ReviewScope = Literal["line", "file", "mr"]

ProblemType = Literal[
    "bug",
    "regression",
    "task_mismatch",
    "security",
    "performance",
    "reliability",
    "compatibility",
    "maintainability",
    "other",
]


class ReviewIssue(BaseModel):
    scope: ReviewScope
    severity_score: int = Field(ge=1, le=10)
    confidence_score: int = Field(ge=1, le=10)
    problem_type: ProblemType
    file_path: str | None = None
    comment: str
    anchor_text: str | None = None
    before_anchor: str | None = None
    after_anchor: str | None = None

    @model_validator(mode="after")
    def normalize(self):
        if self.scope == "line":
            if not self.file_path:
                self.scope = "mr"
            elif not self.anchor_text:
                self.scope = "file"

        if self.scope == "file" and not self.file_path:
            self.scope = "mr"

        if self.scope != "line":
            self.anchor_text = None
            self.before_anchor = None
            self.after_anchor = None

        if self.scope == "mr":
            self.file_path = None

        return self
