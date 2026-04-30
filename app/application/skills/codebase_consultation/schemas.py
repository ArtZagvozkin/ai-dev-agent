from pydantic import BaseModel, Field


class CodebaseConsultationQueryPlan(BaseModel):
    intent: str = Field(default="")
    subqueries: list[str] = Field(default_factory=list)
    preferred_chunk_types: list[str] = Field(default_factory=list)
    path_hints: list[str] = Field(default_factory=list)


class CodebaseConsultationLLMResponse(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[int] = Field(default_factory=list)
