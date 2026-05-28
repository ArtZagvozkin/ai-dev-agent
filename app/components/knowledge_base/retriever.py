from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict

from app.components.knowledge_base.embeddings import KnowledgeBaseEmbeddingClient
from app.components.knowledge_base.models import (
    KnowledgeBaseIndexStats,
    RetrievedKnowledgeBaseChunk,
)
from app.components.knowledge_base.vector_store import (
    PersistentQdrantKnowledgeBaseVectorStore,
)
from app.domain.knowledge_base_chunks import KnowledgeBaseChunk


logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_][A-Za-zА-Яа-яЁё0-9_:\-]*")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []

    for raw_token in TOKEN_RE.findall((text or "").lower()):
        tokens.append(raw_token)

        for split_token in re.split(r"[:_\-]+", raw_token):
            if split_token and split_token != raw_token:
                tokens.append(split_token)

    return tokens


class BM25Index:
    def __init__(
        self,
        tokenized_documents: list[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ):
        self.tokenized_documents = tokenized_documents
        self.k1 = k1
        self.b = b
        self.document_lengths = [
            len(document)
            for document in tokenized_documents
        ]
        self.average_document_length = (
            sum(self.document_lengths) / len(self.document_lengths)
            if self.document_lengths
            else 0.0
        )
        self.document_frequencies = defaultdict(int)

        for document in tokenized_documents:
            for token in set(document):
                self.document_frequencies[token] += 1

        self.document_count = len(tokenized_documents)

    def scores(self, query: str) -> list[float]:
        query_tokens = tokenize(query)
        query_terms = Counter(query_tokens)

        scores: list[float] = []

        for document, document_length in zip(
            self.tokenized_documents,
            self.document_lengths,
        ):
            term_frequencies = Counter(document)
            score = 0.0

            for token, query_weight in query_terms.items():
                frequency = term_frequencies.get(token, 0)
                if frequency == 0:
                    continue

                document_frequency = self.document_frequencies.get(token, 0)
                inverse_document_frequency = math.log(
                    1
                    + (
                        self.document_count
                        - document_frequency
                        + 0.5
                    )
                    / (document_frequency + 0.5)
                )

                denominator = frequency + self.k1 * (
                    1
                    - self.b
                    + self.b
                    * document_length
                    / max(self.average_document_length, 1.0)
                )

                score += query_weight * inverse_document_frequency * (
                    (frequency * (self.k1 + 1))
                    / max(denominator, 1e-9)
                )

            scores.append(score)

        return scores


class KnowledgeBaseSearchIndex:
    def __init__(
        self,
        chunks: list[KnowledgeBaseChunk],
        stats: KnowledgeBaseIndexStats,
        embedding_client: KnowledgeBaseEmbeddingClient,
        vector_store: PersistentQdrantKnowledgeBaseVectorStore,
    ):
        self.chunks = chunks
        self.stats = stats
        self.embedding_client = embedding_client
        self.vector_store = vector_store

        self._searchable_texts = [
            self._searchable_text(chunk)
            for chunk in chunks
        ]
        self._tokenized_chunks = [
            tokenize(text)
            for text in self._searchable_texts
        ]
        self._bm25 = BM25Index(self._tokenized_chunks)

    def search(
        self,
        query: str,
        top_k: int = 6,
        bm25_query: str | None = None,
        vector_query: str | None = None,
    ) -> list[RetrievedKnowledgeBaseChunk]:
        if not self.chunks:
            return []

        normalized_top_k = max(1, min(int(top_k or 6), 50))

        bm25_text = bm25_query or query
        vector_text = vector_query or query

        bm25_scores = self._bm25.scores(bm25_text)

        query_vector = self.embedding_client.embed_text(vector_text)

        vector_hits = self.vector_store.search(
            query_vector=query_vector,
            limit=max(normalized_top_k * 4, 20),
        )

        vector_scores_by_db_id = {
            hit.chunk_db_id: hit.vector_score
            for hit in vector_hits
        }

        bm25_ranks = self._rank_map(bm25_scores)

        vector_ranks = {
            hit.chunk_db_id: rank
            for rank, hit in enumerate(vector_hits, start=1)
            if hit.vector_score > 0
        }

        retrieved: list[RetrievedKnowledgeBaseChunk] = []

        for index, chunk in enumerate(self.chunks):
            bm25_score = bm25_scores[index]
            vector_score = vector_scores_by_db_id.get(chunk.db_id, 0.0)

            if bm25_score <= 0 and vector_score <= 0:
                continue

            combined_score = self._rrf_score(
                bm25_rank=bm25_ranks.get(index),
                vector_rank=vector_ranks.get(chunk.db_id),
            )

            retrieved.append(
                RetrievedKnowledgeBaseChunk(
                    chunk=chunk,
                    score=combined_score,
                    bm25_score=bm25_score,
                    vector_score=vector_score,
                    combined_score=combined_score,
                )
            )

        retrieved.sort(
            key=lambda item: (
                item.combined_score,
                item.bm25_score,
                item.vector_score,
                -item.chunk.chunk_ord,
            ),
            reverse=True,
        )

        return retrieved[:normalized_top_k]

    def stats_payload(self) -> dict:
        return self.stats.to_payload()

    def _searchable_text(self, chunk: KnowledgeBaseChunk) -> str:
        return chunk.contextualized_text or chunk.text

    def _rank_map(self, scores: list[float]) -> dict[int, int]:
        ranked_indices = sorted(
            range(len(scores)),
            key=lambda index: scores[index],
            reverse=True,
        )

        return {
            index: rank
            for rank, index in enumerate(ranked_indices, start=1)
            if scores[index] > 0
        }

    def _rrf_score(
        self,
        bm25_rank: int | None,
        vector_rank: int | None,
        k: int = 60,
    ) -> float:
        score = 0.0

        if bm25_rank is not None:
            score += 1.0 / (k + bm25_rank)

        if vector_rank is not None:
            score += 1.0 / (k + vector_rank)

        return score
