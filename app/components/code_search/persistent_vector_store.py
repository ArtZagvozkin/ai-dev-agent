from uuid import UUID

from fastapi import HTTPException, status

from app.components.code_search.models import CodeChunk, VectorSearchHit


class PersistentQdrantVectorStore:
    """
    VectorStore для persistent codebase index.

    В Postgres хранятся chunks.
    В Qdrant хранятся только vectors, где point id = agent.code_chunks.id.

    Этот store возвращает VectorSearchHit, совместимый со старым HybridRetriever.
    """

    def __init__(
        self,
        index_id: UUID,
        collection_name: str,
        chunks_by_db_id: dict[str, CodeChunk],
        url: str | None = None,
        api_key: str | None = None,
        location: str | None = None,
        prefer_grpc: bool = False,
    ):
        self.index_id = index_id
        self.collection_name = collection_name
        self.chunks_by_db_id = chunks_by_db_id
        self.url = url
        self.api_key = api_key
        self.location = location
        self.prefer_grpc = prefer_grpc
        self._client = None

    def has_index(
        self,
        expected_points: int,
        index_fingerprint: str | None = None,
    ) -> bool:
        """
        Для persistent index мы не строим vectors заново внутри HybridRetriever.

        Если Qdrant collection не готова, лучше упасть сразу, чем тихо
        перейти на неполный retrieval.
        """
        try:
            client = self._get_client()

            if not client.collection_exists(self.collection_name):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Qdrant collection not found: {self.collection_name}",
                )

            count_response = client.count(
                collection_name=self.collection_name,
                exact=True,
            )
            points_count = getattr(count_response, "count", None)

            if points_count != expected_points:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Qdrant vector count does not match Postgres chunk count: "
                        f"collection={self.collection_name}, "
                        f"expected={expected_points}, actual={points_count}"
                    ),
                )

            return True

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Qdrant vector index is unavailable: {exc}",
            ) from exc

    def upsert(
        self,
        chunks: list[CodeChunk],
        vectors: list[list[float]],
        index_fingerprint: str | None = None,
    ) -> None:
        raise RuntimeError(
            "PersistentQdrantVectorStore is read-only during consultation. "
            "Use /code-projects/{project_key}/index/build or /index/rebuild."
        )

    def search(self, query_vector: list[float], limit: int) -> list[VectorSearchHit]:
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

            if payload.get("payload_type") != "code_chunk_vector":
                continue

            if payload.get("index_id") != str(self.index_id):
                continue

            code_chunk_id = payload.get("code_chunk_id") or str(point.id)
            chunk = self.chunks_by_db_id.get(str(code_chunk_id))

            if chunk is None:
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
                    vector_score=float(point.score),
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

        return hits

    def _get_client(self):
        if self._client is not None:
            return self._client

        from qdrant_client import QdrantClient

        client_kwargs: dict = {"prefer_grpc": self.prefer_grpc}

        if self.url:
            client_kwargs["url"] = self.url

            if self.api_key:
                client_kwargs["api_key"] = self.api_key

        elif self.location and self.location != ":memory:":
            client_kwargs["path"] = self.location

        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Persistent codebase consultation requires QDRANT_URL "
                    "or durable QDRANT_LOCAL_PATH"
                ),
            )

        self._client = QdrantClient(**client_kwargs)
        return self._client
