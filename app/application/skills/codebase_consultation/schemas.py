from pydantic import BaseModel, Field


class CodebaseConsultationLLMResponse(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[int] = Field(default_factory=list)
