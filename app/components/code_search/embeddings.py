import hashlib
import logging
import math
import re
from typing import Protocol

from fastapi import HTTPException
from openai import OpenAI


logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:\-]*")


def tokenize(text: str) -> list[str]:
    """Splits text into retrieval-friendly tokens and their common subparts."""
    tokens: list[str] = []

    for raw_token in TOKEN_RE.findall(text.lower()):
        tokens.append(raw_token)
        for split_token in re.split(r"[:_\-]+", raw_token):
            if split_token and split_token != raw_token:
                tokens.append(split_token)

    return tokens


class EmbeddingClient(Protocol):
    def embed_text(self, text: str) -> list[float]:
        """Builds a single embedding vector for one text input."""
        ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Builds embedding vectors for a batch of text inputs."""
        ...


class HashingEmbeddingClient:
    def __init__(self, dimensions: int = 256):
        """Initializes a deterministic local embedding fallback with fixed size."""
        self.dimensions = dimensions

    def embed_text(self, text: str) -> list[float]:
        """Encodes text into a normalized hashing-based vector without external APIs."""
        vector = [0.0] * self.dimensions

        features = tokenize(text)
        compact_text = re.sub(r"\s+", " ", text.lower())

        for index in range(max(len(compact_text) - 2, 0)):
            trigram = compact_text[index : index + 3]
            if trigram.strip():
                features.append(f"3g:{trigram}")

        for feature in features:
            digest = hashlib.md5(feature.encode("utf-8")).digest()
            slot = (
                int.from_bytes(digest[:4], byteorder="little", signed=False)
                % self.dimensions
            )
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[slot] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector

        return [value / norm for value in vector]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Encodes a batch of texts with the local hashing-based embedding strategy."""
        return [self.embed_text(text) for text in texts]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Calculates cosine similarity between two embedding vectors."""
    if not left or not right:
        return 0.0

    return sum(left_value * right_value for left_value, right_value in zip(left, right))


class OpenAIEmbeddingClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        dimensions: int | None = None,
        batch_size: int = 64,
        max_input_tokens: int = 7500,
        fallback_max_input_chars: int = 20000,
    ):
        """
        Configures an OpenAI-compatible embeddings client.

        max_input_tokens intentionally stays below common 8192-token embedding
        limits to leave room for tokenizer/model/provider differences.

        fallback_max_input_chars is used when tiktoken is unavailable.
        """
        self.model = model
        self.dimensions = dimensions
        self.batch_size = max(batch_size, 1)
        self.max_input_tokens = max(max_input_tokens, 1)
        self.fallback_max_input_chars = max(fallback_max_input_chars, 1000)
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def embed_text(self, text: str) -> list[float]:
        """Encodes one text by delegating to the batched embeddings request."""
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Requests embeddings for a batch of texts from an OpenAI-compatible API."""
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
        """
        Keeps embedding input inside a safe provider range.

        Important:
        - this does not mutate stored chunk content;
        - Postgres still keeps full contextualized_text/content;
        - only the remote embedding payload is shortened.
        """
        compact_text = text.strip()
        if not compact_text:
            return ""

        token_limited = self._truncate_with_tiktoken(compact_text)
        if token_limited is not None:
            if len(token_limited) < len(compact_text):
                logger.warning(
                    "Embedding input was token-truncated: model=%s, original_chars=%s, truncated_chars=%s, max_tokens=%s",
                    self.model,
                    len(compact_text),
                    len(token_limited),
                    self.max_input_tokens,
                )
            return token_limited

        if len(compact_text) > self.fallback_max_input_chars:
            logger.warning(
                "Embedding input was char-truncated because tiktoken is unavailable: "
                "model=%s, original_chars=%s, truncated_chars=%s",
                self.model,
                len(compact_text),
                self.fallback_max_input_chars,
            )
            return compact_text[: self.fallback_max_input_chars]

        return compact_text

    def _truncate_with_tiktoken(self, text: str) -> str | None:
        """
        Token-aware truncation when tiktoken is installed.

        Returns None when tiktoken is unavailable, so the caller can use a
        conservative char fallback without adding a hard dependency.
        """
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
        """
        Embeds a batch and recursively splits it when provider/gateway returns
        an invalid payload for a large batch.

        If a single item still fails, the error is raised with useful details.
        """
        try:
            return self._embed_batch(texts)
        except HTTPException:
            if len(texts) <= 1:
                raise

            middle = len(texts) // 2
            logger.warning(
                "Embedding batch failed; retrying with smaller batches: "
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
        """Requests one provider batch and validates that the response contains embeddings."""
        payload = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }

        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions

        try:
            response = self.client.embeddings.create(**payload)
        except Exception as error:
            raise HTTPException(
                status_code=502,
                detail=f"Embedding request failed: {error}",
            ) from error

        response_data = getattr(response, "data", None)
        if not response_data:
            first_input_size = len(texts[0]) if texts else 0
            raise HTTPException(
                status_code=502,
                detail=(
                    "Embedding provider returned an empty 'data' payload. "
                    f"Model={self.model}, batch_size={len(texts)}, "
                    f"first_input_size={first_input_size}"
                ),
            )

        ordered_items = sorted(
            response_data,
            key=lambda item: getattr(item, "index", 0),
        )

        vectors = []
        for item in ordered_items:
            embedding = getattr(item, "embedding", None)
            if embedding is None:
                continue

            vectors.append(list(embedding))

        if len(vectors) != len(texts):
            raise HTTPException(
                status_code=502,
                detail=(
                    "Embedding provider returned a partial embeddings payload. "
                    f"Model={self.model}, requested={len(texts)}, received={len(vectors)}"
                ),
            )

        return vectors


def build_embedding_client_from_options(
    provider: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    dimensions: int | None = None,
    batch_size: int = 64,
    allow_hashing: bool = True,
) -> EmbeddingClient:
    normalized_provider = provider.lower().strip()

    if normalized_provider in {"openai", "openai_compatible"}:
        if not model:
            raise ValueError("Embedding model is required for remote embeddings")

        if not api_key:
            raise ValueError("Embedding API key is required for remote embeddings")

        return OpenAIEmbeddingClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            dimensions=dimensions,
            batch_size=batch_size,
        )

    if normalized_provider == "hashing":
        if not allow_hashing:
            raise ValueError(
                "Hashing embeddings are disabled for persistent codebase indexes"
            )

        return HashingEmbeddingClient(dimensions=dimensions or 256)

    raise ValueError(f"Unsupported embedding provider: {provider}")


def build_embedding_client(settings) -> EmbeddingClient:
    """Selects the embedding client implementation from application settings."""
    return build_embedding_client_from_options(
        provider=getattr(settings, "embedding_provider", "hashing"),
        model=getattr(settings, "embedding_model", None),
        api_key=getattr(settings, "embedding_api_key", None),
        base_url=getattr(settings, "embedding_base_url", None),
        dimensions=getattr(settings, "embedding_dimensions", None),
        batch_size=getattr(settings, "embedding_batch_size", 64),
        allow_hashing=True,
    )
