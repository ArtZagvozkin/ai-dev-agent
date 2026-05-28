from __future__ import annotations

import logging
from uuid import UUID

from qdrant_client import QdrantClient

from app.components.knowledge_base.models import KnowledgeBaseVectorSearchHit
from app.domain.knowledge_base_chunks import KnowledgeBaseChunk


logger = logging.getLogger(__name__)


class PersistentQdrantKnowledgeBaseVectorStore:
    def __init__(
        self,
        index_id: UUID,
        collection_name: str,
        chunks_by_db_id: dict[str, KnowledgeBaseChunk],
        url: str | None = None,
        api_key: str | None = None,
        location: str | None = None,
        prefer_grpc: bool = False,
    ):
        self.index_id = index_id
        self.collection_name = collection_name
        self.chunks_by_db_id = chunks_by_db_id
        self.client = self._build_client(
            url=url,
            api_key=api_key,
            location=location,
            prefer_grpc=prefer_grpc,
        )

    def search(
        self,
        query_vector: list[float],
        limit: int,
    ) -> list[KnowledgeBaseVectorSearchHit]:
        if not query_vector:
            return []

        if limit <= 0:
            return []

        if not self.client.collection_exists(self.collection_name):
            logger.warning(
                "Knowledge base Qdrant collection does not exist: index_id=%s, collection=%s",
                self.index_id,
                self.collection_name,
            )
            return []

        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            points = response.points
        else:
            points = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )

        hits: list[KnowledgeBaseVectorSearchHit] = []

        for point in points:
            payload = point.payload or {}

            if payload.get("payload_type") != "knowledge_base_chunk":
                continue

            if payload.get("index_id") != str(self.index_id):
                continue

            chunk_db_id = str(payload.get("chunk_db_id") or point.id)
            if chunk_db_id not in self.chunks_by_db_id:
                continue

            try:
                parsed_chunk_db_id = UUID(chunk_db_id)
            except ValueError:
                continue

            hits.append(
                KnowledgeBaseVectorSearchHit(
                    chunk_db_id=parsed_chunk_db_id,
                    vector_score=float(point.score),
                )
            )

        return hits

    def _build_client(
        self,
        url: str | None,
        api_key: str | None,
        location: str | None,
        prefer_grpc: bool,
    ) -> QdrantClient:
        client_kwargs: dict = {
            "prefer_grpc": prefer_grpc,
        }

        if url:
            client_kwargs["url"] = url
            if api_key:
                client_kwargs["api_key"] = api_key
        elif location == ":memory:":
            client_kwargs["location"] = ":memory:"
        elif location:
            client_kwargs["path"] = location
        else:
            client_kwargs["location"] = ":memory:"

        return QdrantClient(**client_kwargs)
