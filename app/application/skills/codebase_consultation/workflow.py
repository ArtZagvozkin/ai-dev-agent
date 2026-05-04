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

DEFAULT_SUBQUERY_TOP_K = 15
MAX_SUBQUERIES = 4


class CodebaseConsultationWorkflow:
    def __init__(
        self,
        llm: StructuredLLMClient,
        index_cache: CodebaseIndexCache | None = None,
        agent_context_path: str = "info_cunsalt.md",
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
            final_top_k=data.top_k,
        )
        retrieved_chunks = self._retrieve_chunks(index, query_plan, data.top_k)
        user_message = self._build_user_message(
            repository_path=repository_path,
            question=data.question,
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
        logger.info(
            "Codebase consultation final LLM response received: repository_path=%s, question=%s, llm_result=%s",
            repository_path,
            data.question,
            llm_result,
        )

        sources = self._resolve_sources(retrieved_chunks, llm_result.get("citations", []))
        return {
            "answer": llm_result["answer"],
            "query_plan": query_plan,
            "sources": [self._chunk_payload(chunk) for chunk in sources],
            "retrieved_chunks": [
                self._chunk_payload(chunk, include_scores=True)
                for chunk in retrieved_chunks
            ],
            "index_stats": index.stats_payload(),
        }

    def _load_project_context(self, repository_path: Path) -> tuple[str, str | None]:
        configured_path = Path(self.agent_context_path)
        candidates = (
            [configured_path]
            if configured_path.is_absolute()
            else [
                Path.cwd() / configured_path,
                repository_path / configured_path,
                repository_path.parent / configured_path,
            ]
        )

        for candidate in candidates:
            try:
                if candidate.is_file():
                    return candidate.read_text(encoding="utf-8"), str(
                        candidate.resolve()
                    )
            except OSError:
                logger.warning("Failed to read project context file: %s", candidate)

        return "", None

    def _build_query_plan(
        self,
        repository_path: Path,
        question: str,
        project_context: str,
        project_context_path: str | None,
        final_top_k: int,
    ) -> dict:
        fallback = self._fallback_query_plan(
            question=question,
            project_context_path=project_context_path,
            project_context_loaded=bool(project_context),
            final_top_k=final_top_k,
        )
        if not project_context:
            return fallback

        try:
            planner_result = self.llm.response(
                system_prompt=QUERY_PLANNER_SYSTEM_PROMPT,
                user_message=(
                    f"CONFIGURED PROJECT CONTEXT PATH:\n{self.agent_context_path}\n\n"
                    f"PROJECT CONTEXT PATH:\n{project_context_path or 'None'}\n\n"
                    f"PROJECT CONTEXT:\n{project_context}\n\n"
                    f"REPOSITORY PATH:\n{repository_path}\n\n"
                    f"QUESTION:\n{question}\n"
                ),
                response_model=QueryPlanLLMResponse,
            )
            logger.info(
                "Codebase consultation planner response received: repository_path=%s, question=%s, planner_result=%s",
                repository_path,
                question,
                planner_result,
            )
        except Exception:
            logger.exception(
                "Query planner failed, falling back to single-query retrieval"
            )
            return fallback

        subqueries = self._normalize_subqueries(planner_result.get("subqueries", []))
        path_hints = self._paths(
            [
                *planner_result.get("path_hints", []),
                *[item for query in subqueries for item in query["path_hints"]],
            ]
        )
        extensions = self._extensions(
            [
                *planner_result.get("extensions", []),
                *[item for query in subqueries for item in query["extensions"]],
            ]
        )
        keywords = self._strings(
            [
                *planner_result.get("keywords", []),
                *[item for query in subqueries for item in query["keywords"]],
            ],
            limit=20,
        )
        retrieval_queries = self._strings(
            [
                question,
                *[
                    item
                    for query in subqueries
                    for item in (query["vector_query"], query["bm25_query"])
                ],
            ],
            limit=12,
        )

        return {
            **fallback,
            "project_context_loaded": True,
            "intent": planner_result.get("intent", "").strip(),
            "subqueries": subqueries,
            "answer_focus": self._strings(
                planner_result.get("answer_focus", []),
                limit=5,
            ),
            "retrieval_queries": retrieval_queries,
            "preferred_chunk_types": [
                item
                for item in self._strings(
                    planner_result.get("preferred_chunk_types", []),
                    limit=3,
                )
                if item in {"method", "class", "file_window", "file"}
            ],
            "path_hints": path_hints,
            "extensions": extensions,
            "keywords": keywords,
            "retrieval_mode": (
                "multi_query_parallel" if len(retrieval_queries) > 1 else "single_query"
            ),
        }

    def _fallback_query_plan(
        self,
        question: str,
        project_context_path: str | None,
        project_context_loaded: bool,
        final_top_k: int,
    ) -> dict:
        return {
            "project_context_path": project_context_path,
            "configured_project_context_path": self.agent_context_path,
            "project_context_loaded": project_context_loaded,
            "original_question": question,
            "intent": "",
            "subqueries": [],
            "answer_focus": [],
            "retrieval_queries": [question],
            "preferred_chunk_types": [],
            "path_hints": [],
            "extensions": [],
            "keywords": [],
            "final_top_k": final_top_k,
            "retrieval_mode": "single_query",
        }

    def _retrieve_chunks(
        self, index, query_plan: dict, top_k: int
    ) -> list[RetrievedChunk]:
        if not query_plan["subqueries"]:
            return index.search(query=query_plan["original_question"], top_k=top_k)

        search_specs = [
            {
                "id": "original_question",
                "vector_query": query_plan["original_question"],
                "bm25_query": query_plan["original_question"],
                "extensions": [],
                "keywords": [],
                "path_hints": [],
                "top_k": max(top_k, DEFAULT_SUBQUERY_TOP_K),
            },
            *query_plan["subqueries"],
        ]
        query_results = self._search_subqueries(index, search_specs)
        return self._merge_query_results(
            query_results=query_results,
            top_k=top_k,
            preferred_chunk_types=query_plan["preferred_chunk_types"],
            path_hints=query_plan["path_hints"],
            extensions=query_plan["extensions"],
            keywords=query_plan["keywords"],
        )

    def _search_subqueries(
        self, index, subqueries: list[dict]
    ) -> list[tuple[dict, list[RetrievedChunk]]]:
        results_by_index = {}
        max_workers = min(self.retrieval_workers, len(subqueries))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    index.search,
                    query=f"{subquery['vector_query']} {subquery['bm25_query']}".strip(),
                    top_k=subquery["top_k"],
                    bm25_query=subquery["bm25_query"],
                    vector_query=subquery["vector_query"],
                ): index_number
                for index_number, subquery in enumerate(subqueries)
            }
            for future, index_number in futures.items():
                results_by_index[index_number] = future.result()

        return [
            (subquery, results_by_index[index_number])
            for index_number, subquery in enumerate(subqueries)
        ]

    def _merge_query_results(
        self,
        query_results: list[tuple[dict, list[RetrievedChunk]]],
        top_k: int,
        preferred_chunk_types: list[str],
        path_hints: list[str],
        extensions: list[str],
        keywords: list[str],
    ) -> list[RetrievedChunk]:
        merged = {}
        for subquery, chunks in query_results:
            hints = {
                "path_hints": [*path_hints, *subquery["path_hints"]],
                "extensions": [*extensions, *subquery["extensions"]],
                "keywords": [*keywords, *subquery["keywords"]],
            }
            for rank, chunk in enumerate(chunks, start=1):
                entry = merged.setdefault(
                    chunk.cache_key(),
                    {
                        "chunk": chunk,
                        "rrf": 0.0,
                        "bm25": 0.0,
                        "vector": 0.0,
                        "boost": 0.0,
                    },
                )
                entry["rrf"] += 1.0 / (60 + rank)
                entry["bm25"] = max(entry["bm25"], chunk.bm25_score)
                entry["vector"] = max(entry["vector"], chunk.vector_score)
                entry["boost"] = max(
                    entry["boost"],
                    self._metadata_boost(chunk, preferred_chunk_types, **hints),
                )

        chunks = []
        for entry in merged.values():
            combined_score = entry["rrf"] + entry["boost"]
            chunks.append(
                replace(
                    entry["chunk"],
                    score=combined_score,
                    bm25_score=entry["bm25"],
                    vector_score=entry["vector"],
                    combined_score=combined_score,
                )
            )

        chunks.sort(
            key=lambda item: (
                item.combined_score,
                item.bm25_score,
                item.vector_score,
                -item.start_line,
            ),
            reverse=True,
        )
        return chunks[:top_k]

    def _metadata_boost(
        self,
        chunk: RetrievedChunk,
        preferred_chunk_types: list[str],
        path_hints: list[str],
        extensions: list[str],
        keywords: list[str],
    ) -> float:
        path = chunk.path.lower()
        text = " ".join(
            [
                path,
                chunk.symbol or "",
                chunk.parent_symbol or "",
                " ".join(chunk.keywords or []),
                " ".join(chunk.top_level_symbols or []),
            ]
        ).lower()

        boost = 0.02 if chunk.chunk_type in preferred_chunk_types else 0.0
        boost += 0.03 if any(hint.lower().strip("/") in path for hint in path_hints) else 0.0
        boost += 0.02 if Path(chunk.path).suffix.lower() in extensions else 0.0
        boost += min(
            sum(1 for keyword in keywords if keyword.lower().strip() in text) * 0.01,
            0.05,
        )
        return boost

    def _build_user_message(
        self,
        repository_path: Path,
        question: str,
        query_plan: dict,
        retrieved_chunks: list[RetrievedChunk],
        include_full_code_units: bool,
    ) -> str:
        return (
            f"REPOSITORY PATH:\n{repository_path}\n\n"
            f"QUESTION:\n{question}\n\n"
            f"RETRIEVAL PLAN:\n{self._format_query_plan(query_plan)}\n"
            f"RETRIEVED SOURCES:\n"
            f"{self._format_sources(retrieved_chunks, include_full_code_units)}\n"
        )

    def _format_query_plan(self, query_plan: dict) -> str:
        answer_focus = (
            "\n".join(f"- {item}" for item in query_plan["answer_focus"])
            if query_plan["answer_focus"]
            else "- None"
        )
        retrieval_queries = "\n".join(
            f"- {query}" for query in query_plan["retrieval_queries"]
        )
        return (
            f"Intent: {query_plan['intent'] or 'n/a'}\n"
            f"Final Top K: {query_plan['final_top_k']}\n"
            f"Preferred Chunk Types: {self._join(query_plan['preferred_chunk_types'])}\n"
            f"Path Hints: {self._join(query_plan['path_hints'])}\n"
            f"Extensions: {self._join(query_plan['extensions'])}\n"
            f"Keywords: {self._join(query_plan['keywords'])}\n"
            f"Answer Focus:\n{answer_focus}\n"
            f"Retrieval Queries:\n{retrieval_queries}"
        )

    def _format_sources(
        self, chunks: list[RetrievedChunk], include_full_code_units: bool
    ) -> str:
        if not chunks:
            return "No relevant sources were found."

        sections = []
        for index, chunk in enumerate(chunks, start=1):
            _, code = self._code_payload(chunk, include_full_code_units)
            sections.append(
                f"SOURCE {index}\n"
                f"Path: {chunk.path}:{chunk.start_line}-{chunk.end_line}\n"
                f"Type: {chunk.chunk_type}\n"
                f"Symbol: {chunk.symbol or 'n/a'}\n"
                f"Parent: {chunk.parent_symbol or 'n/a'}\n"
                f"Imports: {self._compact(chunk.imports)}\n"
                f"Code:\n{code}"
            )
        return "\n\n".join(sections)

    def _resolve_sources(
        self, retrieved_chunks: list[RetrievedChunk], citations: list[int]
    ) -> list[RetrievedChunk]:
        selected = []
        seen = set()
        for citation in citations:
            index = citation - 1
            if index < 0 or index >= len(retrieved_chunks):
                continue
            chunk = retrieved_chunks[index]
            key = (chunk.path, chunk.start_line, chunk.end_line)
            if key not in seen:
                seen.add(key)
                selected.append(chunk)

        return selected or retrieved_chunks[: min(3, len(retrieved_chunks))]

    def _chunk_payload(
        self, chunk: RetrievedChunk, include_scores: bool = False
    ) -> dict:
        payload = {
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
            "code_unit": chunk.content if chunk.is_full_code_unit() else None,
            "is_full_code_unit": chunk.is_full_code_unit(),
        }
        if include_scores:
            payload.update(
                {
                    "bm25_score": round(chunk.bm25_score, 6),
                    "vector_score": round(chunk.vector_score, 6),
                    "combined_score": round(chunk.combined_score, 6),
                }
            )
        return payload

    def _code_payload(
        self, chunk: RetrievedChunk, include_full_code_units: bool
    ) -> tuple[str, str]:
        if include_full_code_units and chunk.is_full_code_unit():
            return "Code Unit", chunk.content
        return "Snippet", chunk.content

    def _normalize_subqueries(self, values: list[dict]) -> list[dict]:
        subqueries = []
        seen_ids = set()
        for index, value in enumerate(values, start=1):
            vector_query = self._clean(value.get("vector_query", ""))
            bm25_query = self._clean(value.get("bm25_query", ""))
            if not vector_query and not bm25_query:
                continue

            subquery_id = self._subquery_id(value.get("id", ""), index, seen_ids)
            subqueries.append(
                {
                    "id": subquery_id,
                    "vector_query": vector_query or bm25_query,
                    "bm25_query": bm25_query or vector_query,
                    "extensions": self._extensions(value.get("extensions", [])),
                    "keywords": self._strings(value.get("keywords", []), limit=12),
                    "path_hints": self._paths(value.get("path_hints", [])),
                    "top_k": self._top_k(value.get("top_k", DEFAULT_SUBQUERY_TOP_K)),
                }
            )
            if len(subqueries) >= MAX_SUBQUERIES:
                break
        return subqueries

    def _subquery_id(self, value: str, index: int, seen_ids: set[str]) -> str:
        base = "_".join(self._clean(value or f"subquery_{index}").lower().split())[:64]
        subquery_id = base or f"subquery_{index}"
        while subquery_id in seen_ids:
            subquery_id = f"{base}_{index}"
        seen_ids.add(subquery_id)
        return subquery_id

    def _top_k(self, value) -> int:
        try:
            return min(max(int(value), 1), 50)
        except (TypeError, ValueError):
            return DEFAULT_SUBQUERY_TOP_K

    def _strings(self, values: list[str], limit: int) -> list[str]:
        result = []
        seen = set()
        for value in values:
            cleaned = self._clean(value)
            lowered = cleaned.lower()
            if not cleaned or lowered in seen:
                continue
            seen.add(lowered)
            result.append(cleaned)
            if len(result) >= limit:
                break
        return result

    def _paths(self, values: list[str]) -> list[str]:
        return [value.replace("\\", "/") for value in self._strings(values, limit=5)]

    def _extensions(self, values: list[str]) -> list[str]:
        extensions = []
        for value in self._strings(values, limit=10):
            normalized = value.lower()
            extensions.append(
                normalized if normalized.startswith(".") else f".{normalized}"
            )
        return extensions

    def _clean(self, value: str) -> str:
        return " ".join(str(value).split()).strip()

    def _join(self, values: list[str]) -> str:
        return ", ".join(values) if values else "None"

    def _compact(self, values: list[str] | None, limit: int = 8) -> str:
        if not values:
            return "None"
        suffix = f", ... (+{len(values) - limit})" if len(values) > limit else ""
        return ", ".join(values[:limit]) + suffix

    def _format_subqueries(self, subqueries: list[dict]) -> str:
        if not subqueries:
            return "- None"

        return "\n".join(
            "\n".join(
                [
                    f"- {subquery['id']}",
                    f"  vector_query: {subquery['vector_query']}",
                    f"  bm25_query: {subquery['bm25_query']}",
                    f"  extensions: {self._join(subquery['extensions'])}",
                    f"  keywords: {self._join(subquery['keywords'])}",
                    f"  path_hints: {self._join(subquery['path_hints'])}",
                    f"  top_k: {subquery['top_k']}",
                ]
            )
            for subquery in subqueries
        )
