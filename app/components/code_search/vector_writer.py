from uuid import UUID

from app.components.code_search.vector_store import QDRANT_UPSERT_BATCH_SIZE


class QdrantCodeChunkVectorWriter:
    def __init__(
        self,
        collection_name: str,
        url: str | None = None,
        api_key: str | None = None,
        location: str | None = None,
        prefer_grpc: bool = False,
    ):
        self.collection_name = collection_name
        self.url = url
        self.api_key = api_key
        self.location = location
        self.prefer_grpc = prefer_grpc
        self._client = None
        self._models = None

    def recreate_collection(self, vector_size: int) -> None:
        if vector_size <= 0:
            raise ValueError("Qdrant vector size must be positive")

        client = self._get_client()
        models = self._get_models()

        if client.collection_exists(self.collection_name):
            client.delete_collection(collection_name=self.collection_name)

        client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    def upsert_vectors(
        self,
        index_id: UUID,
        chunk_ids: list[UUID],
        vectors: list[list[float]],
    ) -> None:
        if len(chunk_ids) != len(vectors):
            raise ValueError(
                f"Chunk/vector count mismatch: chunks={len(chunk_ids)}, vectors={len(vectors)}"
            )

        if not vectors:
            return

        vector_size = len(vectors[0])
        for vector in vectors:
            if len(vector) != vector_size:
                raise ValueError("All vectors must have the same dimension")

        client = self._get_client()
        models = self._get_models()

        batch = []

        for chunk_id, vector in zip(chunk_ids, vectors):
            batch.append(
                models.PointStruct(
                    id=str(chunk_id),
                    vector=vector,
                    payload={
                        "payload_type": "code_chunk_vector",
                        "index_id": str(index_id),
                        "code_chunk_id": str(chunk_id),
                    },
                )
            )

            if len(batch) >= QDRANT_UPSERT_BATCH_SIZE:
                client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                )
                batch = []

        if batch:
            client.upsert(
                collection_name=self.collection_name,
                points=batch,
            )

    def _get_client(self):
        if self._client is not None:
            return self._client

        from qdrant_client import QdrantClient

        client_kwargs: dict = {"prefer_grpc": self.prefer_grpc}

        if self.url:
            client_kwargs["url"] = self.url
            if self.api_key:
                client_kwargs["api_key"] = self.api_key
        elif self.location:
            client_kwargs["path"] = self.location
        else:
            client_kwargs["location"] = ":memory:"

        self._client = QdrantClient(**client_kwargs)
        return self._client

    def _get_models(self):
        if self._models is None:
            from qdrant_client import models

            self._models = models

        return self._models
