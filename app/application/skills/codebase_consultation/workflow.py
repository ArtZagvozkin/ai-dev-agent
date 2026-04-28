from pathlib import Path

from app.application.skills.codebase_consultation.prompts import SYSTEM_PROMPT
from app.application.skills.codebase_consultation.schemas import CodebaseConsultationLLMResponse
from app.components.code_search.indexer import CodebaseIndexCache
from app.components.code_search.models import RetrievedChunk
from app.components.llm.structured_client import StructuredLLMClient
from app.schemas.api import CodebaseConsultationRequest


class CodebaseConsultationWorkflow:
    def __init__(
        self,
        llm: StructuredLLMClient,
        index_cache: CodebaseIndexCache | None = None,
    ):
        self.llm = llm
        self.index_cache = index_cache or CodebaseIndexCache()

    def run(self, data: CodebaseConsultationRequest) -> dict:
        index = self.index_cache.get_or_build(
            repository_path=data.repository_path,
            max_files=data.max_files,
            max_file_bytes=data.max_file_bytes,
            force_reindex=data.force_reindex,
        )
        retrieved_chunks = index.search(query=data.question, top_k=data.top_k)
        user_message = self._build_user_message(
            repository_path=Path(data.repository_path).resolve(),
            question=data.question,
            retrieved_chunks=retrieved_chunks,
            include_full_code_units=data.include_full_code_units,
        )

        llm_result = self.llm.response(
            system_prompt=SYSTEM_PROMPT,
            user_message=user_message,
            response_model=CodebaseConsultationLLMResponse,
        )
        citations = llm_result.get("citations", [])

        sources = self._resolve_sources(retrieved_chunks, citations)

        return {
            "answer": llm_result["answer"],
            "sources": [self._source_payload(chunk) for chunk in sources],
            "retrieved_chunks": [self._retrieved_chunk_payload(chunk) for chunk in retrieved_chunks],
            "index_stats": index.stats_payload(),
        }

    def _build_user_message(
        self,
        repository_path: Path,
        question: str,
        retrieved_chunks: list[RetrievedChunk],
        include_full_code_units: bool,
    ) -> str:
        source_sections = []

        for index, chunk in enumerate(retrieved_chunks, start=1):
            code_label, code_payload = self._code_payload(chunk, include_full_code_units)
            source_sections.append(
                (
                    f"SOURCE {index}\n"
                    f"Chunk ID: {chunk.chunk_id}\n"
                    f"Parent Chunk ID: {chunk.parent_chunk_id or 'None'}\n"
                    f"Chunk Type: {chunk.chunk_type}\n"
                    f"Path: {chunk.path}\n"
                    f"Language: {chunk.language}\n"
                    f"Lines: {chunk.start_line}-{chunk.end_line}\n"
                    f"Symbol: {chunk.symbol or 'n/a'}\n"
                    f"Parent Symbol: {chunk.parent_symbol or 'n/a'}\n"
                    f"Imports: {', '.join(chunk.imports or []) if chunk.imports else 'None'}\n"
                    f"References: {', '.join(chunk.references or []) if chunk.references else 'None'}\n"
                    f"Contextualized Text:\n{chunk.contextualized_text}\n"
                    f"{code_label}:\n{code_payload}"
                )
            )

        joined_sources = "\n\n".join(source_sections) if source_sections else "No relevant sources were found."

        return (
            f"REPOSITORY PATH:\n{repository_path}\n\n"
            f"QUESTION:\n{question}\n\n"
            f"RETRIEVED SOURCES:\n{joined_sources}\n"
        )

    def _resolve_sources(self, retrieved_chunks: list[RetrievedChunk], citations: list[int]) -> list[RetrievedChunk]:
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
        code_label, code_payload = self._code_payload(chunk, include_full_code_units=True)
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
        code_label, code_payload = self._code_payload(chunk, include_full_code_units=True)
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

    def _code_payload(self, chunk: RetrievedChunk, include_full_code_units: bool) -> tuple[str, str]:
        if include_full_code_units and chunk.is_full_code_unit():
            return ("Code Unit", chunk.content)

        return ("Snippet", chunk.content)
