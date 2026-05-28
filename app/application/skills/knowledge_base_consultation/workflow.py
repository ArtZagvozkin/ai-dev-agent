from __future__ import annotations

import logging
from typing import Any

from app.application.skills.knowledge_base_consultation.prompts import (
    ANSWER_SYSTEM_PROMPT,
)
from app.application.skills.knowledge_base_consultation.schemas import (
    KnowledgeBaseConsultationLLMResponse,
)
from app.components.knowledge_base.models import RetrievedKnowledgeBaseChunk
from app.schemas.knowledge_base_consultation import (
    KnowledgeBaseConsultationRequest,
)


logger = logging.getLogger(__name__)


class KnowledgeBaseConsultationWorkflow:
    def __init__(
        self,
        llm,
        persistent_index_loader,
    ):
        self.llm = llm
        self.persistent_index_loader = persistent_index_loader

    def run(
        self,
        data: KnowledgeBaseConsultationRequest,
    ) -> dict[str, Any]:
        bundle = self.persistent_index_loader.load(data.kb_key)

        top_k = data.top_k or bundle.top_k
        max_context_chars = data.max_context_chars or bundle.max_context_chars

        retrieved_chunks = bundle.index.search(
            query=data.question,
            top_k=top_k,
        )

        context_chunks = self._fit_context(
            chunks=retrieved_chunks,
            max_context_chars=max_context_chars,
        )

        user_message = self._build_user_message(
            kb_key=bundle.knowledge_base.kb_key,
            question=data.question,
            retrieved_chunks=context_chunks,
        )

        logger.info(
            "Knowledge base consultation prompt prepared: "
            "kb_key=%s, index_id=%s, question=%s, sources=%s, prompt_size=%s\n"
            "SYSTEM PROMPT:\n%s\n\nUSER PROMPT:\n%s",
            bundle.knowledge_base.kb_key,
            bundle.index_record.id,
            data.question,
            len(context_chunks),
            len(user_message),
            ANSWER_SYSTEM_PROMPT,
            user_message,
        )

        llm_result = self.llm.response(
            system_prompt=ANSWER_SYSTEM_PROMPT,
            user_message=user_message,
            response_model=KnowledgeBaseConsultationLLMResponse,
        )

        logger.info(
            "Knowledge base consultation LLM response received: "
            "kb_key=%s, index_id=%s, question=%s, llm_result=%s",
            bundle.knowledge_base.kb_key,
            bundle.index_record.id,
            data.question,
            llm_result,
        )

        selected_sources = self._resolve_sources(
            retrieved_chunks=context_chunks,
            source_numbers=llm_result.get("source_numbers", []),
        )

        return {
            "answer": llm_result["answer"],
            "limitations": llm_result.get("limitations") or "",
            "source_numbers": llm_result.get("source_numbers", []),
            "sources": [
                self._source_payload(source)
                for source in selected_sources
            ],
            "retrieved_chunks": [
                self._source_payload(
                    chunk,
                    include_scores=True,
                )
                for chunk in retrieved_chunks
            ],
            "index_stats": bundle.index.stats_payload(),
        }

    def _build_user_message(
        self,
        kb_key: str,
        question: str,
        retrieved_chunks: list[RetrievedKnowledgeBaseChunk],
    ) -> str:
        return (
            f"KNOWLEDGE BASE KEY:\n{kb_key}\n\n"
            f"QUESTION:\n{question}\n\n"
            f"RETRIEVED SOURCES:\n"
            f"{self._format_sources(retrieved_chunks)}"
        )

    def _format_sources(
        self,
        chunks: list[RetrievedKnowledgeBaseChunk],
    ) -> str:
        if not chunks:
            return "No relevant sources were found."

        sections: list[str] = []

        for index, retrieved in enumerate(chunks, start=1):
            chunk = retrieved.chunk
            section_path = " / ".join(chunk.section_path) if chunk.section_path else "n/a"

            neighbor_context = chunk.metadata.get("neighbor_context") or {}
            context_before = str(neighbor_context.get("before") or "").strip()
            context_after = str(neighbor_context.get("after") or "").strip()

            neighbor_parts: list[str] = []
            if context_before:
                neighbor_parts.append(f"Context before: {context_before}")
            if context_after:
                neighbor_parts.append(f"Context after: {context_after}")

            neighbor_text = "\n".join(neighbor_parts).strip()

            sections.append(
                "\n".join(
                    item
                    for item in [
                        f"SOURCE {index}",
                        f"Document: {chunk.title or 'n/a'}",
                        f"Full title: {chunk.full_title or 'n/a'}",
                        f"Section: {section_path}",
                        f"Source URL: {chunk.source_url or 'n/a'}",
                        f"Revision: {chunk.source_revision or 'n/a'}",
                        f"Updated at: {chunk.updated_at_src or 'n/a'}",
                        f"Score: {retrieved.combined_score:.6f}",
                        neighbor_text,
                        f"Text:\n{chunk.text}",
                    ]
                    if item
                )
            )

        return "\n\n".join(sections)

    def _fit_context(
        self,
        chunks: list[RetrievedKnowledgeBaseChunk],
        max_context_chars: int,
    ) -> list[RetrievedKnowledgeBaseChunk]:
        if not chunks:
            return []

        limit = max(1000, int(max_context_chars or 10000))
        selected: list[RetrievedKnowledgeBaseChunk] = []
        used_chars = 0

        for chunk in chunks:
            text_len = len(chunk.chunk.text or "")

            if selected and used_chars + text_len > limit:
                break

            selected.append(chunk)
            used_chars += text_len

        return selected or chunks[:1]

    def _resolve_sources(
        self,
        retrieved_chunks: list[RetrievedKnowledgeBaseChunk],
        source_numbers: list[int],
    ) -> list[RetrievedKnowledgeBaseChunk]:
        selected: list[RetrievedKnowledgeBaseChunk] = []
        seen = set()

        for source_number in source_numbers or []:
            try:
                index = int(source_number) - 1
            except (TypeError, ValueError):
                continue

            if index < 0 or index >= len(retrieved_chunks):
                continue

            chunk = retrieved_chunks[index]
            key = chunk.cache_key()

            if key in seen:
                continue

            seen.add(key)
            selected.append(chunk)

        return selected or retrieved_chunks[: min(3, len(retrieved_chunks))]

    def _source_payload(
        self,
        retrieved: RetrievedKnowledgeBaseChunk,
        include_scores: bool = False,
    ) -> dict[str, Any]:
        chunk = retrieved.chunk

        payload = {
            "chunk_db_id": chunk.db_id,
            "document_id": chunk.document_id,
            "chunk_id": chunk.chunk_id,
            "chunk_ord": chunk.chunk_ord,
            "title": chunk.title,
            "full_title": chunk.full_title,
            "source_url": chunk.source_url,
            "source_revision": chunk.source_revision,
            "updated_at_src": chunk.updated_at_src,
            "section_path": chunk.section_path,
            "block_types": chunk.block_types,
            "text": chunk.text,
            "contextualized_text": chunk.contextualized_text,
            "metadata": chunk.metadata,
            "score": round(retrieved.score, 6),
        }

        if include_scores:
            payload.update(
                {
                    "bm25_score": round(retrieved.bm25_score, 6),
                    "vector_score": round(retrieved.vector_score, 6),
                    "combined_score": round(retrieved.combined_score, 6),
                }
            )

        return payload
