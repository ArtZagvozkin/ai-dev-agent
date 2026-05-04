import hashlib
import re
from pathlib import Path
from typing import Protocol

from app.components.code_search.embeddings import cosine_similarity
from app.components.code_search.models import CodeChunk, VectorSearchHit


class VectorStore(Protocol):
    def has_index(self, expected_points: int, index_fingerprint: str | None = None) -> bool:
        """Checks whether the store already contains a reusable index for this chunk set."""
        ...

    def upsert(
        self,
        chunks: list[CodeChunk],
        vectors: list[list[float]],
        index_fingerprint: str | None = None,
    ) -> None:
        """Stores chunk vectors so they can be queried later by similarity."""
        ...

    def search(self, query_vector: list[float], limit: int) -> list[VectorSearchHit]:
        """Returns the nearest stored chunks for a query vector."""
        ...


class InMemoryVectorStore:
    def __init__(self):
        """Initializes a lightweight in-process vector store for local use and tests."""
        self._chunks: list[CodeChunk] = []
        self._vectors: list[list[float]] = []
        self._index_fingerprint: str | None = None

    def upsert(
        self,
        chunks: list[CodeChunk],
        vectors: list[list[float]],
        index_fingerprint: str | None = None,
    ) -> None:
        """Replaces the current in-memory vector set with the latest indexed chunks."""
        self._chunks = chunks
        self._vectors = vectors
        self._index_fingerprint = index_fingerprint

    def has_index(self, expected_points: int, index_fingerprint: str | None = None) -> bool:
        """Reports whether the in-memory store already has vectors for the expected chunk count."""
        if len(self._chunks) != expected_points or len(self._vectors) != expected_points:
            return False
        return index_fingerprint is None or self._index_fingerprint == index_fingerprint

    def search(self, query_vector: list[float], limit: int) -> list[VectorSearchHit]:
        """Computes cosine similarity in memory and returns the top matching chunks."""
        hits: list[VectorSearchHit] = []

        for chunk, vector in zip(self._chunks, self._vectors):
            score = cosine_similarity(query_vector, vector)
            if score <= 0:
                continue

            hits.append(
                VectorSearchHit(
                    chunk_id=chunk.chunk_id,
                    parent_chunk_id=chunk.parent_chunk_id,
                    chunk_type=chunk.chunk_type,
                    path=chunk.path,
                    language=chunk.language,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    contextualized_text=chunk.contextualized_text,
                    symbol=chunk.symbol,
                    vector_score=score,
                    ast_node_type=chunk.ast_node_type,
                    declaration_type=chunk.declaration_type,
                    parent_symbol=chunk.parent_symbol,
                    keywords=chunk.keywords or [],
                    imports=chunk.imports or [],
                    references=chunk.references or [],
                    top_level_symbols=chunk.top_level_symbols or [],
                    code_unit=chunk.code_unit,
                )
            )

        hits.sort(key=lambda item: item.vector_score, reverse=True)
        return hits[:limit]


class QdrantVectorStore:
    METADATA_POINT_ID = 0

    def __init__(
        self,
        collection_name: str,
        url: str | None = None,
        api_key: str | None = None,
        location: str | None = None,
        prefer_grpc: bool = False,
    ):
        """Configures a Qdrant-backed vector store for local or remote persistence."""
        self.collection_name = collection_name
        self.url = url
        self.api_key = api_key
        self.location = location
        self.prefer_grpc = prefer_grpc
        self._client = None
        self._models = None
        self._vector_size: int | None = None

    def has_index(self, expected_points: int, index_fingerprint: str | None = None) -> bool:
        """Checks whether the Qdrant collection already contains the expected number of points."""
        client = self._get_client()
        if not client.collection_exists(self.collection_name):
            return False

        count_response = client.count(collection_name=self.collection_name, exact=True)
        points_count = getattr(count_response, "count", None)
        if points_count != expected_points + 1:
            return False
        if not index_fingerprint:
            return True

        metadata = self._retrieve_metadata_payload(client)
        return metadata.get("index_fingerprint") == index_fingerprint

    def upsert(
        self,
        chunks: list[CodeChunk],
        vectors: list[list[float]],
        index_fingerprint: str | None = None,
    ) -> None:
        """Creates the collection if needed and uploads the current chunk vectors to Qdrant."""
        if not chunks or not vectors:
            return

        client = self._get_client()
        models = self._get_models()
        vector_size = len(vectors[0])
        self._ensure_collection(client, models, vector_size=vector_size, recreate=True)

        points = [
            self._metadata_point(models, vector_size, index_fingerprint),
            *[
            models.PointStruct(
                id=index + 1,
                vector=vector,
                payload=self._payload_from_chunk(chunk),
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
            ],
        ]

        client.upsert(collection_name=self.collection_name, points=points)

    def search(self, query_vector: list[float], limit: int) -> list[VectorSearchHit]:
        """Queries Qdrant for the nearest stored vectors and maps payloads back to hits."""
        client = self._get_client()

        if hasattr(client, "query_points"):
            response = client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            points = response.points
        else:
            points = client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )

        hits: list[VectorSearchHit] = []
        for point in points:
            payload = point.payload or {}
            if payload.get("payload_type") != "chunk":
                continue
            hits.append(
                VectorSearchHit(
                    chunk_id=payload["chunk_id"],
                    parent_chunk_id=payload.get("parent_chunk_id"),
                    chunk_type=payload["chunk_type"],
                    path=payload["path"],
                    language=payload["language"],
                    start_line=payload["start_line"],
                    end_line=payload["end_line"],
                    content=payload["snippet"],
                    contextualized_text=payload["contextualized_text"],
                    symbol=payload.get("symbol"),
                    vector_score=float(point.score),
                    ast_node_type=payload.get("ast_node_type"),
                    declaration_type=payload.get("declaration_type"),
                    parent_symbol=payload.get("parent_symbol"),
                    keywords=list(payload.get("keywords", [])),
                    imports=list(payload.get("imports", [])),
                    references=list(payload.get("references", [])),
                    top_level_symbols=list(payload.get("top_level_symbols", [])),
                    code_unit=payload.get("code_unit"),
                )
            )

        return hits

    def _ensure_collection(self, client, models, vector_size: int, recreate: bool = False) -> None:
        """Ensures that the target Qdrant collection exists with the correct vector size."""
        exists = client.collection_exists(self.collection_name)
        if recreate and exists:
            client.delete_collection(collection_name=self.collection_name)
            exists = False
            self._vector_size = None

        if self._vector_size == vector_size and exists:
            return

        if not exists:
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                ),
            )

        self._vector_size = vector_size

    def _metadata_point(self, models, vector_size: int, index_fingerprint: str | None):
        """Builds a marker point used to detect stale persistent indexes."""
        return models.PointStruct(
            id=self.METADATA_POINT_ID,
            vector=[1.0] + [0.0] * (vector_size - 1),
            payload={
                "payload_type": "index_metadata",
                "index_fingerprint": index_fingerprint,
                "chunk_schema": "code-search-v2-file-window",
            },
        )

    def _retrieve_metadata_payload(self, client) -> dict:
        """Fetches the persistent index metadata point when it exists."""
        if not hasattr(client, "retrieve"):
            return {}

        points = client.retrieve(
            collection_name=self.collection_name,
            ids=[self.METADATA_POINT_ID],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return {}

        return getattr(points[0], "payload", None) or {}

    def _get_client(self):
        """Lazily constructs the Qdrant client with the configured transport settings."""
        if self._client is not None:
            return self._client

        from qdrant_client import QdrantClient

        client_kwargs: dict = {"prefer_grpc": self.prefer_grpc}
        if self.url:
            client_kwargs["url"] = self.url
            if self.api_key:
                client_kwargs["api_key"] = self.api_key
        elif self.location == ":memory:":
            client_kwargs["location"] = ":memory:"
        elif self.location:
            client_kwargs["path"] = self.location
        else:
            client_kwargs["location"] = ":memory:"

        self._client = QdrantClient(**client_kwargs)
        return self._client

    def _get_models(self):
        """Lazily imports Qdrant model classes used for collection and point payloads."""
        if self._models is None:
            from qdrant_client import models

            self._models = models

        return self._models

    def _payload_from_chunk(self, chunk: CodeChunk) -> dict:
        """Serializes chunk metadata into the payload stored alongside each vector."""
        return {
            "payload_type": "chunk",
            "chunk_id": chunk.chunk_id,
            "parent_chunk_id": chunk.parent_chunk_id,
            "chunk_type": chunk.chunk_type,
            "path": chunk.path,
            "language": chunk.language,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "symbol": chunk.symbol,
            "ast_node_type": chunk.ast_node_type,
            "declaration_type": chunk.declaration_type,
            "parent_symbol": chunk.parent_symbol,
            "keywords": chunk.keywords or [],
            "imports": chunk.imports or [],
            "references": chunk.references or [],
            "top_level_symbols": chunk.top_level_symbols or [],
            "contextualized_text": chunk.contextualized_text,
            "code_unit": chunk.code_unit,
            "snippet": chunk.content,
        }


def build_vector_store_factory(settings):
    """Builds a repository-aware vector store factory from application settings."""
    provider = getattr(settings, "vector_store_provider", "memory").lower()

    if provider == "qdrant":
        def factory(repository_path: Path):
            collection_name = build_collection_name(
                base_name=settings.qdrant_collection_name,
                repository_path=repository_path,
            )
            return QdrantVectorStore(
                collection_name=collection_name,
                url=settings.qdrant_url or None,
                api_key=settings.qdrant_api_key or None,
                location=settings.qdrant_local_path or None,
                prefer_grpc=settings.qdrant_prefer_grpc,
            )
    else:
        def factory(repository_path: Path):
            return InMemoryVectorStore()

    return factory


def build_collection_name(base_name: str, repository_path: Path) -> str:
    """Generates a deterministic and filesystem-safe Qdrant collection name."""
    slug_base = re.sub(r"[^a-zA-Z0-9_]+", "_", base_name).strip("_").lower() or "codebase_chunks"
    repo_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", repository_path.name).strip("_").lower() or "repo"
    digest = hashlib.md5(str(repository_path).encode("utf-8")).hexdigest()[:12]
    return f"{slug_base}_{repo_slug}_{digest}"
