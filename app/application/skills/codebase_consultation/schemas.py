from pydantic import BaseModel, Field


class CodebaseConsultationRetrievalSubquery(BaseModel):
    id: str = Field(default="")
    vector_query: str = Field(default="")
    bm25_query: str = Field(default="")
    extensions: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    path_hints: list[str] = Field(default_factory=list)
    top_k: int = Field(default=15, ge=1, le=50)


class CodebaseConsultationQueryPlan(BaseModel):
    intent: str = Field(default="")
    subqueries: list[CodebaseConsultationRetrievalSubquery] = Field(default_factory=list)
    answer_focus: list[str] = Field(default_factory=list)
    preferred_chunk_types: list[str] = Field(default_factory=list)
    path_hints: list[str] = Field(default_factory=list)


class CodebaseConsultationLLMResponse(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[int] = Field(default_factory=list)
