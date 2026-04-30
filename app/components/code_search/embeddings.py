import hashlib
import math
import re
from typing import Protocol

from fastapi import HTTPException
from openai import OpenAI


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
            slot = int.from_bytes(digest[:4], byteorder="little", signed=False) % self.dimensions
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
    ):
        """Configures an OpenAI-compatible embeddings client for remote vectorization."""
        self.model = model
        self.dimensions = dimensions
        self.batch_size = max(batch_size, 1)
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def embed_text(self, text: str) -> list[float]:
        """Encodes one text by delegating to the batched embeddings request."""
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Requests embeddings for a batch of texts from an OpenAI-compatible API."""
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self._embed_batch(batch))

        return vectors

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
            raise HTTPException(status_code=502, detail=f"Embedding request failed: {error}")

        response_data = getattr(response, "data", None)
        if not response_data:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Embedding provider returned an empty 'data' payload. "
                    f"Model={self.model}, batch_size={len(texts)}"
                ),
            )

        vectors = [list(item.embedding) for item in response_data if getattr(item, "embedding", None) is not None]
        if len(vectors) != len(texts):
            raise HTTPException(
                status_code=502,
                detail=(
                    "Embedding provider returned a partial embeddings payload. "
                    f"Model={self.model}, requested={len(texts)}, received={len(vectors)}"
                ),
            )

        return vectors


def build_embedding_client(settings) -> EmbeddingClient:
    """Selects the embedding client implementation from application settings."""
    provider = getattr(settings, "embedding_provider", "hashing").lower()
    if provider in {"openai", "openai_compatible"}:
        return OpenAIEmbeddingClient(
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            dimensions=settings.embedding_dimensions,
            batch_size=getattr(settings, "embedding_batch_size", 64),
        )

    return HashingEmbeddingClient(dimensions=settings.embedding_dimensions or 256)
