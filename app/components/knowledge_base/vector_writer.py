import math
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    PointIdsList,
    PointStruct,
    VectorParams,
)


QDRANT_KB_UPSERT_BATCH_SIZE = 64


class QdrantKnowledgeBaseVectorWriter:
    def __init__(
        self,
        collection_name: str,
        url: str | None = None,
        api_key: str | None = None,
        location: str | None = None,
        prefer_grpc: bool = False,
    ):
        self.collection_name = collection_name
        self.client = self._build_client(
            url=url,
            api_key=api_key,
            location=location,
            prefer_grpc=prefer_grpc,
        )

    def recreate_collection(self, vector_size: int) -> None:
        if vector_size <= 0:
            raise ValueError(f"Invalid vector size: {vector_size}")

        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE,
            ),
        )

    def delete_collection(self) -> None:
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)

    def upsert_vectors(
        self,
        index_id: UUID,
        document_id: UUID,
        chunk_ids: list[UUID],
        vectors: list[list[float]],
    ) -> None:
        if len(chunk_ids) != len(vectors):
            raise ValueError(
                "chunk/vector count mismatch: "
                f"chunks={len(chunk_ids)}, vectors={len(vectors)}"
            )

        if not chunk_ids:
            return

        vector_size = self._validate_vectors(vectors)

        batch: list[PointStruct] = []

        for chunk_id, vector in zip(chunk_ids, vectors):
            point = PointStruct(
                id=str(chunk_id),
                vector=[float(value) for value in vector],
                payload={
                    "payload_type": "knowledge_base_chunk",
                    "index_id": str(index_id),
                    "document_id": str(document_id),
                    "chunk_db_id": str(chunk_id),
                    "vector_size": vector_size,
                },
            )

            batch.append(point)

            if len(batch) >= QDRANT_KB_UPSERT_BATCH_SIZE:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                )
                batch = []

        if batch:
            self.client.upsert(
                collection_name=self.collection_name,
                points=batch,
            )

    def delete_vectors(
        self,
        chunk_ids: list[UUID],
    ) -> None:
        if not chunk_ids:
            return

        self.client.delete(
            collection_name=self.collection_name,
            points_selector=PointIdsList(
                points=[str(chunk_id) for chunk_id in chunk_ids],
            ),
        )

    def _validate_vectors(self, vectors: list[list[float]]) -> int:
        if not vectors:
            raise ValueError("vectors must not be empty")

        vector_size = len(vectors[0])
        if vector_size <= 0:
            raise ValueError("vector size must be greater than 0")

        for vector_index, vector in enumerate(vectors):
            if len(vector) != vector_size:
                raise ValueError(
                    "vectors have different dimensions: "
                    f"expected={vector_size}, "
                    f"actual={len(vector)}, "
                    f"vector_index={vector_index}"
                )

            for value_index, value in enumerate(vector):
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "vector contains non-numeric value: "
                        f"vector_index={vector_index}, "
                        f"value_index={value_index}, "
                        f"value={value!r}"
                    ) from exc

                if not math.isfinite(numeric_value):
                    raise ValueError(
                        "vector contains non-finite value: "
                        f"vector_index={vector_index}, "
                        f"value_index={value_index}, "
                        f"value={value!r}"
                    )

        return vector_size

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
