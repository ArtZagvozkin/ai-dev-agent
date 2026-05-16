import logging
from typing import Type

from fastapi import HTTPException
from openai import OpenAI
from pydantic import BaseModel


logger = logging.getLogger(__name__)


class StructuredLLMClient:
    def __init__(self, model: str, api_key: str, base_url: str, max_tokens: int = 5000):
        self.model = model
        self.max_tokens = max_tokens
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def response(
        self,
        system_prompt: str,
        user_message: str,
        response_model: Type[BaseModel],
    ) -> dict:
        response_schema = response_model.model_json_schema()

        logger.info(
            "LLM request started: model=%s, response_model=%s, prompt_size=%s, max_tokens=%s",
            self.model,
            response_model.__name__,
            len(user_message),
            self.max_tokens,
        )

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_response_schema",
                        "schema": response_schema,
                        "strict": True,
                    },
                },
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )

            if not completion or not completion.choices:
                raise ValueError(f"LLM returned empty completion: {completion}")

            message = completion.choices[0].message
            result = message.content

            if not result:
                raise ValueError(
                    "LLM returned empty message.content. "
                    "Try removing reasoning options or use a model that supports structured JSON output."
                )

            parsed_response = response_model.model_validate_json(result)

        except Exception as e:
            logger.exception(
                "LLM request failed: model=%s, response_model=%s",
                self.model,
                response_model.__name__,
            )
            raise HTTPException(status_code=500, detail=str(e))

        logger.info(
            "LLM request completed: model=%s, response_model=%s",
            self.model,
            response_model.__name__,
        )

        return parsed_response.model_dump()
