from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI


logger = logging.getLogger(__name__)

OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_EMBEDDING_BATCH_SIZE = 64
MAX_EMBEDDING_BATCH_SIZE = 256


class KnowledgeBaseEmbeddingError(RuntimeError):
    pass


class KnowledgeBaseEmbeddingClient(Protocol):
    def embed_text(self, text: str) -> list[float]:
        ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass(slots=True)
class KnowledgeBaseEmbeddingConfig:
    provider: str
    base_url: str
    model: str
    dimensions: int | None
    batch_size: int


class OpenRouterKnowledgeBaseEmbeddingClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int | None = None,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        max_input_tokens: int = 7500,
        fallback_max_input_chars: int = 20000,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.dimensions = dimensions
        self.batch_size = max(1, batch_size)
        self.max_input_tokens = max(1, max_input_tokens)
        self.fallback_max_input_chars = max(1000, fallback_max_input_chars)

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        normalized_texts = [
            self._normalize_embedding_input(text)
            for text in texts
        ]

        vectors: list[list[float]] = []

        for start in range(0, len(normalized_texts), self.batch_size):
            batch = normalized_texts[start : start + self.batch_size]
            vectors.extend(self._embed_batch_with_fallback(batch))

        return vectors

    def _normalize_embedding_input(self, text: str) -> str:
        compact_text = (text or "").strip()
        if not compact_text:
            return ""

        token_limited = self._truncate_with_tiktoken(compact_text)
        if token_limited is not None:
            if len(token_limited) < len(compact_text):
                logger.warning(
                    "Knowledge base embedding input was token-truncated: "
                    "model=%s, original_chars=%s, truncated_chars=%s, max_tokens=%s",
                    self.model,
                    len(compact_text),
                    len(token_limited),
                    self.max_input_tokens,
                )

            return token_limited

        if len(compact_text) > self.fallback_max_input_chars:
            logger.warning(
                "Knowledge base embedding input was char-truncated because tiktoken is unavailable: "
                "model=%s, original_chars=%s, truncated_chars=%s",
                self.model,
                len(compact_text),
                self.fallback_max_input_chars,
            )

            return compact_text[: self.fallback_max_input_chars]

        return compact_text

    def _truncate_with_tiktoken(self, text: str) -> str | None:
        try:
            import tiktoken
        except ImportError:
            return None

        try:
            encoding = tiktoken.encoding_for_model(self.model)
        except Exception:
            try:
                encoding = tiktoken.get_encoding("cl100k_base")
            except Exception:
                return None

        tokens = encoding.encode(text)
        if len(tokens) <= self.max_input_tokens:
            return text

        return encoding.decode(tokens[: self.max_input_tokens])

    def _embed_batch_with_fallback(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._embed_batch(texts)
        except KnowledgeBaseEmbeddingError:
            if len(texts) <= 1:
                raise

            middle = len(texts) // 2

            logger.warning(
                "Knowledge base embedding batch failed; retrying with smaller batches: "
                "model=%s, batch_size=%s, left=%s, right=%s",
                self.model,
                len(texts),
                middle,
                len(texts) - middle,
            )

            return [
                *self._embed_batch_with_fallback(texts[:middle]),
                *self._embed_batch_with_fallback(texts[middle:]),
            ]

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }

        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions

        try:
            response = self.client.embeddings.create(**payload)
        except Exception as exc:
            raise KnowledgeBaseEmbeddingError(
                f"OpenRouter embedding request failed: {exc}"
            ) from exc

        response_data = getattr(response, "data", None)
        if not response_data:
            first_input_size = len(texts[0]) if texts else 0
            raise KnowledgeBaseEmbeddingError(
                "OpenRouter returned an empty embeddings payload: "
                f"model={self.model}, batch_size={len(texts)}, "
                f"first_input_size={first_input_size}"
            )

        ordered_items = sorted(
            response_data,
            key=lambda item: getattr(item, "index", 0),
        )

        vectors: list[list[float]] = []

        for item in ordered_items:
            embedding = getattr(item, "embedding", None)
            if embedding is None:
                continue

            vectors.append([float(value) for value in embedding])

        if len(vectors) != len(texts):
            raise KnowledgeBaseEmbeddingError(
                "OpenRouter returned a partial embeddings payload: "
                f"model={self.model}, requested={len(texts)}, received={len(vectors)}"
            )

        return vectors


def normalize_knowledge_base_embedding_config(
    provider: str,
    base_url: str,
    model: str,
    batch_size: int,
    dimensions: int | None = None,
) -> KnowledgeBaseEmbeddingConfig:
    normalized_provider = (provider or "").strip().lower()
    normalized_base_url = (base_url or "").strip().rstrip("/")
    normalized_model = (model or "").strip()

    if normalized_provider != OPENROUTER_PROVIDER:
        raise ValueError("embedding_provider must be openrouter")

    if normalized_base_url != OPENROUTER_BASE_URL:
        raise ValueError(
            "embedding_base_url must be OpenRouter base URL: "
            f"{OPENROUTER_BASE_URL}"
        )

    if not normalized_model:
        raise ValueError("embedding_model is required")

    try:
        normalized_batch_size = int(batch_size)
    except (TypeError, ValueError) as exc:
        raise ValueError("embedding_batch_size must be an integer") from exc

    if normalized_batch_size <= 0:
        raise ValueError("embedding_batch_size must be greater than 0")

    if normalized_batch_size > MAX_EMBEDDING_BATCH_SIZE:
        raise ValueError(
            "embedding_batch_size is too large: "
            f"max={MAX_EMBEDDING_BATCH_SIZE}"
        )

    if dimensions is not None and dimensions <= 0:
        raise ValueError("embedding_dimensions must be greater than 0")

    return KnowledgeBaseEmbeddingConfig(
        provider=OPENROUTER_PROVIDER,
        base_url=normalized_base_url,
        model=normalized_model,
        dimensions=dimensions,
        batch_size=normalized_batch_size,
    )


def build_knowledge_base_embedding_client(
    api_key: str,
    config: KnowledgeBaseEmbeddingConfig,
) -> KnowledgeBaseEmbeddingClient:
    normalized_api_key = (api_key or "").strip()

    if not normalized_api_key:
        raise ValueError("OPENROUTER_API_KEY is required for knowledge base embeddings")

    return OpenRouterKnowledgeBaseEmbeddingClient(
        api_key=normalized_api_key,
        base_url=config.base_url,
        model=config.model,
        dimensions=config.dimensions,
        batch_size=config.batch_size,
    )
