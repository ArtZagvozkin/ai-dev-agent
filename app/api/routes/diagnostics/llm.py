from fastapi import APIRouter, Depends

from app.api.dependencies import get_app_settings, get_llm_client
from app.components.llm.structured_client import StructuredLLMClient
from app.core.config import Settings
from app.schemas.api import (
    LLMDiagnosticLLMResponse,
    LLMDiagnosticRequest,
    LLMDiagnosticResponse,
)


router = APIRouter(
    prefix="/diagnostics/llm",
    tags=["diagnostics: llm"],
)


SYSTEM_PROMPT = """
Ты diagnostic endpoint для проверки LLM-интеграции.

Задача:
- принять текст пользователя;
- коротко ответить, что LLM работает;
- не выполнять сложный анализ;
- вернуть только JSON по заданной схеме.

Поле output должно содержать короткий человекочитаемый ответ.
"""


@router.post("/check", response_model=LLMDiagnosticResponse)
def check_llm(
    data: LLMDiagnosticRequest,
    settings: Settings = Depends(get_app_settings),
    llm: StructuredLLMClient = Depends(get_llm_client),
):
    result = llm.response(
        system_prompt=SYSTEM_PROMPT,
        user_message=data.message,
        response_model=LLMDiagnosticLLMResponse,
    )

    return {
        "ok": True,
        "model": settings.model_llm,
        "input": data.message,
        "output": result["output"],
    }
