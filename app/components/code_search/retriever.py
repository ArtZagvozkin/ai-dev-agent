import math
import hashlib
from collections import Counter, defaultdict
import logging

from app.components.code_search.embeddings import EmbeddingClient, tokenize
from app.components.code_search.models import CodeChunk, RetrievedChunk
from app.components.code_search.vector_store import InMemoryVectorStore, VectorStore


logger = logging.getLogger(__name__)
INDEX_SCHEMA_VERSION = "code-search-v2-file-window"


class BM25Index:
    def __init__(self, tokenized_documents: list[list[str]], k1: float = 1.5, b: float = 0.75):
        """Precomputes BM25 statistics for a tokenized chunk collection."""
        self.tokenized_documents = tokenized_documents
        self.k1 = k1
        self.b = b
        self.document_lengths = [len(document) for document in tokenized_documents]
        self.average_document_length = (
            sum(self.document_lengths) / len(self.document_lengths) if self.document_lengths else 0.0
        )
        self.document_frequencies = defaultdict(int)

        for document in tokenized_documents:
            for token in set(document):
                self.document_frequencies[token] += 1

        self.document_count = len(tokenized_documents)

    def scores(self, query: str) -> list[float]:
        """Calculates BM25 relevance scores for every indexed document against the query."""
        query_tokens = tokenize(query)
        query_terms = Counter(query_tokens)
        scores: list[float] = []

        for document, document_length in zip(self.tokenized_documents, self.document_lengths):
            term_frequencies = Counter(document)
            score = 0.0

            for token, query_weight in query_terms.items():
                frequency = term_frequencies.get(token, 0)
                if frequency == 0:
                    continue

                document_frequency = self.document_frequencies.get(token, 0)
                inverse_document_frequency = math.log(
                    1 + (self.document_count - document_frequency + 0.5) / (document_frequency + 0.5)
                )

                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * document_length / max(self.average_document_length, 1.0)
                )
                score += query_weight * inverse_document_frequency * (
                    (frequency * (self.k1 + 1)) / max(denominator, 1e-9)
                )

            scores.append(score)

        return scores


class HybridRetriever:
    def __init__(
        self,
        chunks: list[CodeChunk],
        embedding_client: EmbeddingClient,
        vector_store: VectorStore | None = None,
        force_reindex: bool = False,
    ):
        """Combines lexical BM25 and vector search into one hybrid retriever."""
        self.chunks = chunks
        self.embedding_client = embedding_client
        self.vector_store = vector_store or InMemoryVectorStore()
        self._searchable_texts = [self._searchable_text(chunk) for chunk in chunks]
        self._tokenized_chunks = [tokenize(text) for text in self._searchable_texts]
        self._bm25 = BM25Index(self._tokenized_chunks)
        self._vectors: list[list[float]] = []
        self._index_fingerprint = self._build_index_fingerprint(chunks)

        if not force_reindex and self.vector_store.has_index(
            expected_points=len(self.chunks),
            index_fingerprint=self._index_fingerprint,
        ):
            logger.info(
                "Hybrid retriever reused existing vector index: chunks=%s",
                len(self.chunks),
            )
        else:
            logger.info(
                "Hybrid retriever building vector index: chunks=%s",
                len(self.chunks),
            )
            self._vectors = self.embedding_client.embed_texts(self._searchable_texts)
            self.vector_store.upsert(
                chunks=self.chunks,
                vectors=self._vectors,
                index_fingerprint=self._index_fingerprint,
            )

    def search(
        self,
        query: str,
        top_k: int = 5,
        bm25_query: str | None = None,
        vector_query: str | None = None,
    ) -> list[RetrievedChunk]:
        """Executes hybrid retrieval and merges lexical and vector ranks with RRF."""
        if not self.chunks:
            return []

        bm25_text = bm25_query or query
        vector_text = vector_query or query

        bm25_scores = self._bm25.scores(bm25_text)
        query_vector = self.embedding_client.embed_text(vector_text)
        vector_hits = self.vector_store.search(query_vector=query_vector, limit=max(top_k * 4, 20))
        vector_scores_by_key = {hit.cache_key(): hit.vector_score for hit in vector_hits}

        bm25_ranks = self._rank_map(bm25_scores)
        vector_ranks = {
            hit.cache_key(): rank
            for rank, hit in enumerate(vector_hits, start=1)
            if hit.vector_score > 0
        }

        retrieved: list[RetrievedChunk] = []
        for index, chunk in enumerate(self.chunks):
            bm25_score = bm25_scores[index]
            cache_key = chunk.cache_key()
            vector_score = vector_scores_by_key.get(cache_key, 0.0)
            combined_score = self._rrf_score(bm25_ranks.get(index), vector_ranks.get(cache_key))

            if bm25_score <= 0 and vector_score <= 0:
                continue

            retrieved.append(
                RetrievedChunk(
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
                    score=combined_score,
                    bm25_score=bm25_score,
                    vector_score=vector_score,
                    combined_score=combined_score,
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

        retrieved.sort(
            key=lambda item: (
                item.combined_score,
                item.bm25_score,
                item.vector_score,
                -item.start_line,
            ),
            reverse=True,
        )

        return retrieved[:top_k]

    def _searchable_text(self, chunk: CodeChunk) -> str:
        """Builds the text representation used for tokenization and embeddings."""
        return chunk.contextualized_text

    def _build_index_fingerprint(self, chunks: list[CodeChunk]) -> str:
        """Builds a stable fingerprint for the current chunk schema and repository contents."""
        digest = hashlib.sha256()
        digest.update(INDEX_SCHEMA_VERSION.encode("utf-8"))
        for chunk in chunks:
            digest.update(
                "\x1f".join(
                    [
                        chunk.chunk_id,
                        chunk.chunk_type,
                        chunk.path,
                        str(chunk.start_line),
                        str(chunk.end_line),
                        chunk.contextualized_text,
                    ]
                ).encode("utf-8")
            )
            digest.update(b"\x1e")
        return digest.hexdigest()

    def _rank_map(self, scores: list[float]) -> dict[int, int]:
        """Converts positive scores into 1-based ranking positions."""
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

    def _rrf_score(self, bm25_rank: int | None, vector_rank: int | None, k: int = 60) -> float:
        """Combines rank positions from multiple retrievers using reciprocal rank fusion."""
        score = 0.0

        if bm25_rank is not None:
            score += 1.0 / (k + bm25_rank)

        if vector_rank is not None:
            score += 1.0 / (k + vector_rank)

        return score
