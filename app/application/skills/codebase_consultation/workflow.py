import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from app.application.skills.codebase_consultation.prompts import (
    ANSWER_SYSTEM_PROMPT,
    QUERY_PLANNER_SYSTEM_PROMPT,
)
from app.application.skills.codebase_consultation.schemas import (
    CodebaseConsultationLLMResponse,
    CodebaseConsultationQueryPlan as QueryPlanLLMResponse,
)
from app.components.code_search.indexer import CodebaseIndexCache
from app.components.code_search.models import RetrievedChunk
from app.components.llm.structured_client import StructuredLLMClient
from app.schemas.api import CodebaseConsultationRequest

logger = logging.getLogger(__name__)


class CodebaseConsultationWorkflow:
    def __init__(
        self,
        llm: StructuredLLMClient,
        index_cache: CodebaseIndexCache | None = None,
        agent_context_path: str = "AGENT.md",
        retrieval_workers: int = 4,
    ):
        self.llm = llm
        self.index_cache = index_cache or CodebaseIndexCache()
        self.agent_context_path = agent_context_path
        self.retrieval_workers = max(retrieval_workers, 1)

    def run(self, data: CodebaseConsultationRequest) -> dict:
        repository_path = Path(data.repository_path).resolve()
        project_context, project_context_path = self._load_project_context(
            repository_path
        )

        index = self.index_cache.get_or_build(
            repository_path=data.repository_path,
            max_files=data.max_files,
            max_file_bytes=data.max_file_bytes,
            force_reindex=data.force_reindex,
        )
        query_plan = self._build_query_plan(
            repository_path=repository_path,
            question=data.question,
            project_context=project_context,
            project_context_path=project_context_path,
        )
        retrieved_chunks = self._retrieve_chunks(
            index=index,
            query_plan=query_plan,
            top_k=data.top_k,
        )

        user_message = self._build_user_message(
            repository_path=repository_path,
            question=data.question,
            project_context=project_context,
            query_plan=query_plan,
            retrieved_chunks=retrieved_chunks,
            include_full_code_units=data.include_full_code_units,
        )

        logger.info(
            "Codebase consultation final prompt prepared: repository_path=%s, question=%s, prompt_size=%s\n"
            "FINAL SYSTEM PROMPT:\n%s\n\nFINAL USER PROMPT:\n%s",
            repository_path,
            data.question,
            len(user_message),
            ANSWER_SYSTEM_PROMPT,
            user_message,
        )

        llm_result = self.llm.response(
            system_prompt=ANSWER_SYSTEM_PROMPT,
            user_message=user_message,
            response_model=CodebaseConsultationLLMResponse,
        )
        citations = llm_result.get("citations", [])

        sources = self._resolve_sources(retrieved_chunks, citations)

        return {
            "answer": llm_result["answer"],
            "query_plan": query_plan,
            "sources": [self._source_payload(chunk) for chunk in sources],
            "retrieved_chunks": [
                self._retrieved_chunk_payload(chunk) for chunk in retrieved_chunks
            ],
            "index_stats": index.stats_payload(),
        }

    def _load_project_context(self, repository_path: Path) -> tuple[str, str | None]:
        """Loads the configured project context markdown from a small set of local locations."""
        configured_path = Path(self.agent_context_path)
        candidates = []

        if configured_path.is_absolute():
            candidates.append(configured_path)
        else:
            candidates.append(Path.cwd() / configured_path)
            candidates.append(repository_path / configured_path)
            candidates.append(repository_path.parent / configured_path)

        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    return (
                        candidate.read_text(encoding="utf-8"),
                        str(candidate.resolve()),
                    )
            except OSError:
                logger.warning("Failed to read project context file: %s", candidate)

        return ("", None)

    def _build_query_plan(
        self,
        repository_path: Path,
        question: str,
        project_context: str,
        project_context_path: str | None,
    ) -> dict:
        """Builds a retrieval plan from project context and falls back to the raw question when needed."""
        retrieval_queries = [question]
        plan = {
            "project_context_path": project_context_path,
            "project_context_loaded": bool(project_context),
            "original_question": question,
            "intent": "",
            "subqueries": [],
            "retrieval_queries": retrieval_queries,
            "preferred_chunk_types": [],
            "path_hints": [],
            "retrieval_mode": "single_query",
        }

        if not project_context:
            return plan

        planner_message = (
            f"PROJECT CONTEXT PATH:\n{project_context_path or 'None'}\n\n"
            f"PROJECT CONTEXT:\n{project_context}\n\n"
            f"REPOSITORY PATH:\n{repository_path}\n\n"
            f"QUESTION:\n{question}\n"
        )

        try:
            planner_result = self.llm.response(
                system_prompt=QUERY_PLANNER_SYSTEM_PROMPT,
                user_message=planner_message,
                response_model=QueryPlanLLMResponse,
            )
        except Exception:
            logger.exception(
                "Query planner failed, falling back to single-query retrieval"
            )
            return plan

        subqueries = self._normalize_unique_strings(
            planner_result.get("subqueries", [])
        )
        preferred_chunk_types = self._normalize_chunk_types(
            planner_result.get("preferred_chunk_types", [])
        )
        path_hints = self._normalize_path_hints(planner_result.get("path_hints", []))
        retrieval_queries = self._normalize_unique_strings([question, *subqueries])

        return {
            "project_context_path": project_context_path,
            "project_context_loaded": True,
            "original_question": question,
            "intent": planner_result.get("intent", "").strip(),
            "subqueries": subqueries,
            "retrieval_queries": retrieval_queries,
            "preferred_chunk_types": preferred_chunk_types,
            "path_hints": path_hints,
            "retrieval_mode": (
                "multi_query_parallel" if len(retrieval_queries) > 1 else "single_query"
            ),
        }

    def _retrieve_chunks(
        self, index, query_plan: dict, top_k: int
    ) -> list[RetrievedChunk]:
        """Runs one or more retrieval queries and merges them with RRF plus lightweight metadata boosts."""
        retrieval_queries = query_plan["retrieval_queries"]
        per_query_top_k = max(top_k * 3, 10)

        if len(retrieval_queries) == 1:
            return index.search(query=retrieval_queries[0], top_k=top_k)

        query_results = self._search_queries_in_parallel(
            index=index, queries=retrieval_queries, top_k=per_query_top_k
        )
        return self._merge_query_results(
            query_results=query_results,
            top_k=top_k,
            preferred_chunk_types=query_plan["preferred_chunk_types"],
            path_hints=query_plan["path_hints"],
        )

    def _search_queries_in_parallel(
        self, index, queries: list[str], top_k: int
    ) -> list[tuple[str, list[RetrievedChunk]]]:
        """Executes independent retrieval queries concurrently while preserving query order."""
        max_workers = min(self.retrieval_workers, len(queries))
        ordered_results: dict[str, list[RetrievedChunk]] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_by_query = {
                executor.submit(index.search, query=query, top_k=top_k): query
                for query in queries
            }
            for future, query in future_by_query.items():
                ordered_results[query] = future.result()

        return [(query, ordered_results[query]) for query in queries]

    def _merge_query_results(
        self,
        query_results: list[tuple[str, list[RetrievedChunk]]],
        top_k: int,
        preferred_chunk_types: list[str],
        path_hints: list[str],
    ) -> list[RetrievedChunk]:
        """Merges per-query retrieval results with reciprocal rank fusion and small metadata boosts."""
        merged_by_key: dict[tuple[str, int, int], dict] = {}

        for _, chunks in query_results:
            for rank, chunk in enumerate(chunks, start=1):
                key = chunk.cache_key()
                entry = merged_by_key.setdefault(
                    key,
                    {
                        "chunk": chunk,
                        "rrf_score": 0.0,
                        "bm25_score": 0.0,
                        "vector_score": 0.0,
                    },
                )
                entry["rrf_score"] += self._rrf_score(rank)
                entry["bm25_score"] = max(entry["bm25_score"], chunk.bm25_score)
                entry["vector_score"] = max(entry["vector_score"], chunk.vector_score)

        merged_chunks: list[RetrievedChunk] = []
        for entry in merged_by_key.values():
            chunk = entry["chunk"]
            metadata_boost = self._metadata_boost(
                chunk=chunk,
                preferred_chunk_types=preferred_chunk_types,
                path_hints=path_hints,
            )
            combined_score = entry["rrf_score"] + metadata_boost
            merged_chunks.append(
                replace(
                    chunk,
                    score=combined_score,
                    bm25_score=entry["bm25_score"],
                    vector_score=entry["vector_score"],
                    combined_score=combined_score,
                )
            )

        merged_chunks.sort(
            key=lambda item: (
                item.combined_score,
                item.bm25_score,
                item.vector_score,
                -item.start_line,
            ),
            reverse=True,
        )
        return merged_chunks[:top_k]

    def _metadata_boost(
        self,
        chunk: RetrievedChunk,
        preferred_chunk_types: list[str],
        path_hints: list[str],
    ) -> float:
        """Adds small ranking nudges from planner hints without overwhelming retrieval scores."""
        boost = 0.0
        normalized_path = chunk.path.lower()

        if chunk.chunk_type in preferred_chunk_types:
            boost += 0.02

        for hint in path_hints:
            normalized_hint = hint.lower().strip("/")
            if normalized_hint and normalized_hint in normalized_path:
                boost += 0.03
                break

        return boost

    def _rrf_score(self, rank: int, k: int = 60) -> float:
        """Calculates reciprocal rank fusion contribution for one ranking position."""
        return 1.0 / (k + rank)

    def _build_user_message(
        self,
        repository_path: Path,
        question: str,
        project_context: str,
        query_plan: dict,
        retrieved_chunks: list[RetrievedChunk],
        include_full_code_units: bool,
    ) -> str:
        source_sections = []

        for index, chunk in enumerate(retrieved_chunks, start=1):
            code_label, code_payload = self._code_payload(
                chunk, include_full_code_units
            )
            source_sections.append(
                (
                    f"SOURCE {index}\n"
                    f"Path: {chunk.path}:{chunk.start_line}-{chunk.end_line}\n"
                    f"Type: {chunk.chunk_type}\n"
                    f"Symbol: {chunk.symbol or 'n/a'}\n"
                    f"Parent: {chunk.parent_symbol or 'n/a'}\n"
                    f"Imports: {self._compact_list(chunk.imports, limit=8)}\n"
                    f"Code:\n{code_payload}"
                )
            )

        joined_sources = (
            "\n\n".join(source_sections)
            if source_sections
            else "No relevant sources were found."
        )
        project_context_section = (
            project_context
            if project_context
            else "No project context file was loaded."
        )
        path_hints = (
            ", ".join(query_plan["path_hints"]) if query_plan["path_hints"] else "None"
        )
        preferred_chunk_types = (
            ", ".join(query_plan["preferred_chunk_types"])
            if query_plan["preferred_chunk_types"]
            else "None"
        )
        subqueries = (
            "\n".join(f"- {query}" for query in query_plan["subqueries"]) or "- None"
        )
        retrieval_queries = "\n".join(
            f"- {query}" for query in query_plan["retrieval_queries"]
        )

        return (
            f"PROJECT CONTEXT PATH:\n{query_plan['project_context_path'] or 'None'}\n\n"
            f"PROJECT CONTEXT:\n{project_context_section}\n\n"
            f"REPOSITORY PATH:\n{repository_path}\n\n"
            f"QUESTION:\n{question}\n\n"
            f"RETRIEVAL PLAN:\n"
            f"Intent: {query_plan['intent'] or 'n/a'}\n"
            f"Preferred Chunk Types: {preferred_chunk_types}\n"
            f"Path Hints: {path_hints}\n"
            f"Subqueries:\n{subqueries}\n"
            f"RETRIEVED SOURCES:\n{joined_sources}\n"
        )

    def _resolve_sources(
        self, retrieved_chunks: list[RetrievedChunk], citations: list[int]
    ) -> list[RetrievedChunk]:
        selected: list[RetrievedChunk] = []
        seen = set()

        for citation in citations:
            index = citation - 1
            if 0 <= index < len(retrieved_chunks):
                chunk = retrieved_chunks[index]
                key = (chunk.path, chunk.start_line, chunk.end_line)
                if key not in seen:
                    seen.add(key)
                    selected.append(chunk)

        if selected:
            return selected

        return retrieved_chunks[: min(3, len(retrieved_chunks))]

    def _source_payload(self, chunk: RetrievedChunk) -> dict:
        code_label, code_payload = self._code_payload(
            chunk, include_full_code_units=True
        )
        return {
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
            "score": round(chunk.score, 6),
            "snippet": chunk.content,
            "contextualized_text": chunk.contextualized_text,
            "code_unit": code_payload if code_label == "Code Unit" else None,
            "is_full_code_unit": chunk.is_full_code_unit(),
        }

    def _retrieved_chunk_payload(self, chunk: RetrievedChunk) -> dict:
        code_label, code_payload = self._code_payload(
            chunk, include_full_code_units=True
        )
        return {
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
            "score": round(chunk.score, 6),
            "snippet": chunk.content,
            "contextualized_text": chunk.contextualized_text,
            "code_unit": code_payload if code_label == "Code Unit" else None,
            "is_full_code_unit": chunk.is_full_code_unit(),
            "bm25_score": round(chunk.bm25_score, 6),
            "vector_score": round(chunk.vector_score, 6),
            "combined_score": round(chunk.combined_score, 6),
        }

    def _code_payload(
        self, chunk: RetrievedChunk, include_full_code_units: bool
    ) -> tuple[str, str]:
        if include_full_code_units and chunk.is_full_code_unit():
            return ("Code Unit", chunk.content)

        return ("Snippet", chunk.content)

    def _normalize_unique_strings(self, values: list[str], limit: int = 5) -> list[str]:
        """Cleans and deduplicates planner strings while preserving order."""
        normalized: list[str] = []
        seen = set()

        for value in values:
            cleaned = " ".join(value.split()).strip()
            lowered = cleaned.lower()
            if not cleaned or lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(cleaned)
            if len(normalized) >= limit:
                break

        return normalized

    def _normalize_chunk_types(self, values: list[str]) -> list[str]:
        """Filters planner chunk type hints down to the supported retrieval chunk types."""
        allowed = {"method", "class", "file"}
        return [
            value
            for value in self._normalize_unique_strings(values, limit=3)
            if value in allowed
        ]

    def _normalize_path_hints(self, values: list[str]) -> list[str]:
        """Normalizes planner path hints to slash-separated repository-style prefixes."""
        hints = []
        for value in self._normalize_unique_strings(values, limit=5):
            normalized = value.replace("\\", "/").strip()
            if normalized:
                hints.append(normalized)
        return hints

    def _compact_list(self, values: list[str] | None, limit: int = 8) -> str:
        """Formats a metadata list compactly for the final LLM prompt."""
        if not values:
            return "None"

        compacted = values[:limit]
        suffix = f", ... (+{len(values) - limit})" if len(values) > limit else ""
        return ", ".join(compacted) + suffix
