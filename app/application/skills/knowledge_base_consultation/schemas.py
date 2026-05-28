from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgeBaseConsultationLLMResponse(BaseModel):
    answer: str = Field(
        description="Final answer based only on retrieved knowledge base sources.",
    )
    source_numbers: list[int] = Field(
        description="1-based source numbers used to produce the answer.",
    )
    limitations: str = Field(
        description="Missing information or uncertainty. Empty string if there are no limitations.",
    )
