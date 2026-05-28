from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class KnowledgeBaseChunkerConfig:
    target_chars: int = 2300
    max_chars: int = 3000
    min_chars: int = 750

    # Важно:
    # overlap больше не добавляется в сам text/contextualized_text.
    # Соседний контекст хранится в metadata и может использоваться RAG-слоем
    # при сборке ответа.
    neighbor_context_max_chars: int = 220
    neighbor_context_max_sentences: int = 1
    neighbor_context_same_section_only: bool = True

    merge_small_callouts: bool = True
    callout_merge_max_chars: int = 260

    allow_inline_atomic_in_buffer: bool = True
    inline_atomic_max_chars: int = 420

    merge_tiny_chunks: bool = True

    ws_re: str = r"[ \t\r\f\v]+"
    nl3_re: str = r"\n{3,}"
    sent_end_re: str = r"(?<=[.!?])\s+"

    md_table_row_re: str = r"^\s*\|.*\|\s*$"
    md_table_min_rows: int = 3
    md_table_scan_lines: int = 10

    code_fence_re: str = r"(^```|^~~~)"
    code_indent_min_ratio: float = 0.35
    code_indent_scan_lines: int = 30
    code_indent_prefixes: tuple[str, ...] = ("    ", "\t")

    max_heading_level: int = 6


@dataclass
class KnowledgeBaseChunkDraft:
    chunk_id: str
    chunk_ord: int

    external_id: str
    source_url: Optional[str]
    title: Optional[str]
    full_title: Optional[str]
    updated_at_src: Optional[str]
    source_revision: Optional[str]

    section_path: list[str]
    block_types: list[str]
    images: list[dict[str, Any]]

    text: str
    contextualized_text: str

    char_len: int
    approx_tokens: int
    chunk_hash: str

    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chunk_ord": self.chunk_ord,
            "external_id": self.external_id,
            "source_url": self.source_url,
            "title": self.title,
            "full_title": self.full_title,
            "updated_at_src": self.updated_at_src,
            "source_revision": self.source_revision,
            "section_path": self.section_path,
            "block_types": self.block_types,
            "images": self.images,
            "text": self.text,
            "contextualized_text": self.contextualized_text,
            "char_len": self.char_len,
            "approx_tokens": self.approx_tokens,
            "chunk_hash": self.chunk_hash,
            "metadata": self.metadata,
        }


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_sha256_hex(payload: dict[str, Any]) -> str:
    return sha256_hex(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def approx_tokens(chars: int) -> int:
    return max(1, math.ceil(chars / 4))


def build_regexes(
    cfg: KnowledgeBaseChunkerConfig,
) -> dict[str, re.Pattern[str]]:
    return {
        "ws": re.compile(cfg.ws_re),
        "nl3": re.compile(cfg.nl3_re),
        "sent_end": re.compile(cfg.sent_end_re),
        "md_table_row": re.compile(cfg.md_table_row_re, re.MULTILINE),
        "code_fence": re.compile(cfg.code_fence_re, re.MULTILINE),
    }


def norm_text(s: str, rx: dict[str, re.Pattern[str]]) -> str:
    s = (s or "").replace("\xa0", " ")
    s = rx["ws"].sub(" ", s)
    s = rx["nl3"].sub("\n\n", s)
    return s.strip()


def join_nonempty(
    parts: list[str],
    rx: dict[str, re.Pattern[str]],
    sep: str = "\n\n",
) -> str:
    cleaned = [p for p in (norm_text(x, rx) for x in parts) if p]
    return sep.join(cleaned).strip()


def compact_block_types(block_types: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for block_type in block_types:
        value = (block_type or "").strip()
        if not value or value in seen:
            continue

        seen.add(value)
        result.append(value)

    return result


def dedupe_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for image in images or []:
        local_path = (image.get("local_path") or "").strip()
        if not local_path or local_path in seen:
            continue

        seen.add(local_path)

        cleaned = {
            key: value
            for key, value in image.items()
            if key in {"local_path", "source_url", "original_url", "alt", "caption"}
            and value not in (None, "", [], {})
        }

        out.append(cleaned)

    return out


def block_images(block: dict[str, Any]) -> list[dict[str, Any]]:
    return dedupe_images(block.get("images") or [])


def is_tableish_text(
    text: str,
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
) -> bool:
    value = text.strip()
    if not value:
        return False

    lines = value.splitlines()[: cfg.md_table_scan_lines]
    if not lines:
        return False

    hits = sum(1 for line in lines if rx["md_table_row"].match(line))
    return hits >= cfg.md_table_min_rows


def is_codeish_text(
    text: str,
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
) -> bool:
    value = text.strip()
    if not value:
        return False

    if rx["code_fence"].search(value):
        return True

    lines = value.splitlines()
    scan = (
        lines[-cfg.code_indent_scan_lines :]
        if len(lines) > cfg.code_indent_scan_lines
        else lines
    )

    if not scan:
        return False

    indented = sum(1 for line in scan if line.startswith(cfg.code_indent_prefixes))
    ratio = indented / max(1, len(scan))

    return ratio >= cfg.code_indent_min_ratio


def split_into_paragraphs(
    text: str,
    rx: dict[str, re.Pattern[str]],
) -> list[str]:
    value = norm_text(text, rx)
    if not value:
        return []

    return [part.strip() for part in value.split("\n\n") if part.strip()]


def looks_list_like(text: str) -> bool:
    lines = text.splitlines()
    if len(lines) < 2:
        return False

    markers = ("- ", "• ", "* ")
    return any(line.lstrip().startswith(markers) for line in lines)


def split_into_sentences(
    text: str,
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
) -> list[str]:
    value = norm_text(text, rx)
    if not value:
        return []

    if (
        is_tableish_text(value, cfg, rx)
        or is_codeish_text(value, cfg, rx)
        or looks_list_like(value)
    ):
        return [value]

    parts = [part.strip() for part in rx["sent_end"].split(value) if part.strip()]
    return parts or [value]


def hard_cut(text: str, max_chars: int) -> list[str]:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return [value] if value else []

    out: list[str] = []
    current = value

    while len(current) > max_chars:
        cut = current.rfind("\n\n", 0, max_chars)

        if cut < int(max_chars * 0.60):
            cut = current.rfind("\n", 0, max_chars)

        if cut < int(max_chars * 0.60):
            cut = current.rfind(" ", 0, max_chars)

        if cut < int(max_chars * 0.60):
            cut = max_chars

        part = current[:cut].strip()
        if part:
            out.append(part)

        current = current[cut:].strip()

    if current:
        out.append(current)

    return out


def split_best_effort(
    text: str,
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
    max_chars: int,
) -> list[str]:
    value = norm_text(text, rx)
    if not value:
        return []

    if len(value) <= max_chars:
        return [value]

    out: list[str] = []

    paragraphs = split_into_paragraphs(value, rx)
    if len(paragraphs) > 1:
        current: list[str] = []
        current_len = 0

        for paragraph in paragraphs:
            if current_len and current_len + 2 + len(paragraph) > max_chars:
                out.append(join_nonempty(current, rx))
                current = []
                current_len = 0

            if len(paragraph) > max_chars:
                if current:
                    out.append(join_nonempty(current, rx))
                    current = []
                    current_len = 0

                out.extend(split_best_effort(paragraph, cfg, rx, max_chars))
                continue

            current.append(paragraph)
            current_len += len(paragraph) + 2

        if current:
            out.append(join_nonempty(current, rx))

        return [item for item in out if item]

    sentences = split_into_sentences(value, cfg, rx)
    if len(sentences) > 1:
        current = ""

        for sentence in sentences:
            if not current:
                if len(sentence) <= max_chars:
                    current = sentence
                else:
                    out.extend(hard_cut(sentence, max_chars))
                    current = ""
                continue

            if len(current) + 1 + len(sentence) <= max_chars:
                current = f"{current} {sentence}"
            else:
                out.append(current.strip())

                if len(sentence) <= max_chars:
                    current = sentence
                else:
                    out.extend(hard_cut(sentence, max_chars))
                    current = ""

        if current:
            out.append(current.strip())

        return [item for item in out if item]

    return hard_cut(value, max_chars)


def build_trailing_context(
    text: str,
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
) -> str:
    value = norm_text(text, rx)
    if not value:
        return ""

    if is_tableish_text(value, cfg, rx) or is_codeish_text(value, cfg, rx):
        return ""

    paragraphs = split_into_paragraphs(value, rx)
    base = paragraphs[-1] if paragraphs else value

    sentences = split_into_sentences(base, cfg, rx)
    if not sentences:
        return ""

    picked: list[str] = []

    for sentence in reversed(sentences):
        sentence = sentence.strip()
        if not sentence:
            continue

        candidate = " ".join(reversed(picked + [sentence])).strip()

        if len(candidate) > cfg.neighbor_context_max_chars:
            break

        picked.append(sentence)

        if len(picked) >= cfg.neighbor_context_max_sentences:
            break

    return " ".join(reversed(picked)).strip()


def build_leading_context(
    text: str,
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
) -> str:
    value = norm_text(text, rx)
    if not value:
        return ""

    if is_tableish_text(value, cfg, rx) or is_codeish_text(value, cfg, rx):
        return ""

    paragraphs = split_into_paragraphs(value, rx)
    base = paragraphs[0] if paragraphs else value

    sentences = split_into_sentences(base, cfg, rx)
    if not sentences:
        return ""

    picked: list[str] = []

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        candidate = " ".join(picked + [sentence]).strip()

        if len(candidate) > cfg.neighbor_context_max_chars:
            break

        picked.append(sentence)

        if len(picked) >= cfg.neighbor_context_max_sentences:
            break

    return " ".join(picked).strip()


def block_to_text(
    block: dict[str, Any],
    rx: dict[str, re.Pattern[str]],
) -> str:
    block_type = (block.get("type") or "").strip()

    if block_type == "heading":
        return norm_text(block.get("text") or "", rx)

    if block_type in {"paragraph", "callout"}:
        return norm_text(block.get("text") or "", rx)

    if block_type == "list":
        items = block.get("items") or []
        ordered = bool(block.get("ordered"))
        lines: list[str] = []

        if ordered:
            n = 1
            for item in items:
                item_text = norm_text(item, rx)
                if item_text:
                    lines.append(f"{n}. {item_text}")
                    n += 1
        else:
            for item in items:
                item_text = norm_text(item, rx)
                if item_text:
                    lines.append(f"- {item_text}")

        return "\n".join(lines).strip()

    if block_type in {"code", "table"}:
        return (block.get("text") or "").rstrip()

    return norm_text(block.get("text") or "", rx)


def apply_heading_path(
    heading_stack: list[str],
    block: dict[str, Any],
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
) -> list[str]:
    level = int(block.get("level") or 1)
    text = norm_text(block.get("text") or "", rx)

    if not text:
        return heading_stack

    level = max(1, min(level, cfg.max_heading_level))
    index = level - 1

    if len(heading_stack) <= index:
        heading_stack.extend([""] * (index + 1 - len(heading_stack)))

    heading_stack[index] = text
    del heading_stack[index + 1 :]

    return [heading for heading in heading_stack if heading]


def make_chunk_id(entry_id: int, chunk_ord: int) -> str:
    return f"{entry_id:06d}-{chunk_ord:04d}"


def build_article_content(
    article: dict[str, Any],
    rx: dict[str, re.Pattern[str]],
) -> str:
    blocks = article.get("blocks") or []
    parts: list[str] = []

    for block in blocks:
        text = block_to_text(block, rx)
        if text:
            parts.append(text)

    return join_nonempty(parts, rx, sep="\n\n")


def compute_article_hash(article: dict[str, Any], content: str) -> str:
    payload = {
        "external_id": str(article.get("entry_id") or ""),
        "source_url": article.get("source_url") or "",
        "title": article.get("title") or "",
        "full_title": article.get("full_title") or "",
        "revision": article.get("revision") or "",
        "updated_at": article.get("updated_at") or "",
        "content": content or "",
    }

    return json_sha256_hex(payload)


def compute_chunk_hash(
    article: dict[str, Any],
    chunk: dict[str, Any],
) -> str:
    payload = {
        "chunk_id": chunk["chunk_id"],
        "chunk_ord": int(chunk["chunk_ord"]),
        "external_id": str(article.get("entry_id") or ""),
        "source_url": article.get("source_url") or "",
        "title": article.get("title") or "",
        "full_title": article.get("full_title") or "",
        "revision": article.get("revision") or "",
        "updated_at": article.get("updated_at") or "",
        "section_path": chunk.get("section_path", []),
        "block_types": chunk.get("block_types", []),
        "images": chunk.get("images", []),
        "text": chunk.get("text", ""),
    }

    return json_sha256_hex(payload)


def build_contextualized_text(
    article: dict[str, Any],
    chunk: dict[str, Any],
    rx: dict[str, re.Pattern[str]],
) -> str:
    title = norm_text(article.get("title") or "", rx)
    full_title = norm_text(article.get("full_title") or "", rx)

    section_path = chunk.get("section_path") or []
    section = " / ".join(str(value) for value in section_path if str(value).strip())

    text = norm_text(chunk.get("text") or "", rx)

    parts: list[str] = []

    if title:
        parts.append(f"Документ: {title}")

    if full_title:
        parts.append(f"Полный путь: {full_title}")

    if section:
        parts.append(f"Раздел: {section}")

    if text:
        parts.append(text)

    return join_nonempty(parts, rx)


def chunk_article(
    article: dict[str, Any],
    cfg: Optional[KnowledgeBaseChunkerConfig] = None,
) -> tuple[list[KnowledgeBaseChunkDraft], str]:
    cfg = cfg or KnowledgeBaseChunkerConfig()
    rx = build_regexes(cfg)

    entry_id_raw = article.get("entry_id")
    if entry_id_raw is None:
        raise ValueError("article.entry_id is required")

    entry_id = int(entry_id_raw)
    external_id = str(entry_id)

    blocks: list[dict[str, Any]] = article.get("blocks") or []

    chunks: list[dict[str, Any]] = []
    heading_stack: list[str] = []
    section_path: list[str] = []

    buf_parts: list[str] = []
    buf_types: list[str] = []
    buf_images: list[dict[str, Any]] = []
    buf_len = 0

    chunk_ord = 1

    pending_small_callout: Optional[tuple[str, list[dict[str, Any]]]] = None

    def flush_buf_if_any() -> None:
        nonlocal chunk_ord
        nonlocal buf_parts
        nonlocal buf_types
        nonlocal buf_images
        nonlocal buf_len

        text = join_nonempty(buf_parts, rx)

        if not text:
            buf_parts = []
            buf_types = []
            buf_images = []
            buf_len = 0
            return

        chunks.append(
            {
                "chunk_id": make_chunk_id(entry_id, chunk_ord),
                "chunk_ord": chunk_ord,
                "section_path": section_path.copy(),
                "block_types": compact_block_types(buf_types.copy()),
                "images": dedupe_images(buf_images),
                "text": text,
                "char_len": len(text),
            }
        )

        chunk_ord += 1

        buf_parts = []
        buf_types = []
        buf_images = []
        buf_len = 0

    def buf_add(
        text: str,
        block_types: list[str],
        images: list[dict[str, Any]],
    ) -> None:
        nonlocal buf_len

        text = norm_text(text, rx)
        if not text:
            return

        buf_parts.append(text)
        buf_types.extend(block_types)
        buf_len += len(text)

        if images:
            buf_images.extend(images)

    def emit_as_own_chunks(
        block_types: list[str],
        block_text: str,
        images: list[dict[str, Any]],
    ) -> None:
        nonlocal chunk_ord

        parts = split_best_effort(block_text, cfg, rx, cfg.max_chars)
        clean_images = dedupe_images(images)

        for part in parts:
            part = norm_text(part, rx)

            if not part:
                continue

            chunks.append(
                {
                    "chunk_id": make_chunk_id(entry_id, chunk_ord),
                    "chunk_ord": chunk_ord,
                    "section_path": section_path.copy(),
                    "block_types": compact_block_types(block_types),
                    "images": clean_images,
                    "text": part,
                    "char_len": len(part),
                }
            )

            chunk_ord += 1

    def add_or_merge_pending_callout(
        callout_text: str,
        images: list[dict[str, Any]],
    ) -> None:
        nonlocal pending_small_callout

        callout_text = norm_text(callout_text, rx)
        if not callout_text:
            return

        if not pending_small_callout:
            pending_small_callout = (callout_text, images)
            return

        previous_text, previous_images = pending_small_callout
        merged_text = join_nonempty([previous_text, callout_text], rx)
        merged_images = dedupe_images((previous_images or []) + (images or []))

        pending_small_callout = (merged_text, merged_images)

    def attach_pending_callout(
        text: str,
        images: list[dict[str, Any]],
        block_types: list[str],
    ) -> tuple[str, list[dict[str, Any]], list[str]]:
        nonlocal pending_small_callout

        if not pending_small_callout:
            return text, images, block_types

        callout_text, callout_images = pending_small_callout
        pending_small_callout = None

        merged_text = join_nonempty([callout_text, text], rx)
        merged_images = dedupe_images((callout_images or []) + (images or []))
        merged_block_types = compact_block_types(["callout"] + block_types)

        return merged_text, merged_images, merged_block_types

    def emit_pending_callout_if_any() -> None:
        nonlocal pending_small_callout
        nonlocal chunk_ord

        if not pending_small_callout:
            return

        callout_text, callout_images = pending_small_callout
        pending_small_callout = None

        flush_buf_if_any()

        text = norm_text(callout_text, rx)
        if not text:
            return

        chunks.append(
            {
                "chunk_id": make_chunk_id(entry_id, chunk_ord),
                "chunk_ord": chunk_ord,
                "section_path": section_path.copy(),
                "block_types": ["callout"],
                "images": dedupe_images(callout_images),
                "text": text,
                "char_len": len(text),
            }
        )

        chunk_ord += 1

    def can_inline_atomic(block_type: str, block_text: str) -> bool:
        if not cfg.allow_inline_atomic_in_buffer:
            return False

        if block_type not in {"code", "table"}:
            return False

        return len(block_text or "") <= cfg.inline_atomic_max_chars

    for block in blocks:
        block_type = (block.get("type") or "").strip()

        if block_type == "heading":
            emit_pending_callout_if_any()
            flush_buf_if_any()

            heading_stack = apply_heading_path(heading_stack, block, cfg, rx)
            section_path = heading_stack.copy()
            continue

        block_text = block_to_text(block, rx)
        images = block_images(block)

        if not block_text and not images:
            continue

        if cfg.merge_small_callouts and block_type == "callout":
            callout_text = norm_text(block_text, rx)

            if callout_text and len(callout_text) <= cfg.callout_merge_max_chars:
                add_or_merge_pending_callout(callout_text, images)
                continue

            emit_pending_callout_if_any()
            flush_buf_if_any()
            emit_as_own_chunks(["callout"], block_text, images)
            continue

        block_types = [block_type]

        if pending_small_callout:
            block_text, images, block_types = attach_pending_callout(
                text=block_text,
                images=images,
                block_types=block_types,
            )

        if not block_text:
            continue

        if len(block_text) > cfg.max_chars:
            flush_buf_if_any()
            emit_as_own_chunks(block_types, block_text, images)
            continue

        if block_type in {"code", "table"}:
            if can_inline_atomic(block_type, block_text) and (
                buf_len + 2 + len(block_text) <= cfg.max_chars
            ):
                buf_add(block_text, block_types, images)

                if buf_len >= cfg.target_chars:
                    flush_buf_if_any()
            else:
                flush_buf_if_any()
                emit_as_own_chunks(block_types, block_text, images)

            continue

        if buf_len and (buf_len + 2 + len(block_text)) > cfg.max_chars:
            flush_buf_if_any()

        buf_add(block_text, block_types, images)

        if buf_len >= cfg.target_chars:
            flush_buf_if_any()

    emit_pending_callout_if_any()
    flush_buf_if_any()

    if cfg.merge_tiny_chunks:
        chunks = merge_tiny_chunks(entry_id, chunks, cfg, rx)

    article_content = build_article_content(article, rx)
    chunks = add_neighbor_context(chunks, cfg, rx)
    finalized = finalize_chunks(article, external_id, chunks, rx)

    return finalized, article_content


def merge_tiny_chunks(
    entry_id: int,
    chunks: list[dict[str, Any]],
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    index = 0

    while index < len(chunks):
        current = chunks[index]
        current_text = norm_text(current.get("text") or "", rx)

        if not current_text:
            index += 1
            continue

        if (
            index + 1 < len(chunks)
            and len(current_text) < cfg.min_chars
            and len(current_text) + 2 + len(chunks[index + 1].get("text") or "") <= cfg.max_chars
            and current.get("section_path") == chunks[index + 1].get("section_path")
        ):
            next_chunk = chunks[index + 1]
            next_text = norm_text(next_chunk.get("text") or "", rx)

            if next_text:
                merged_text = join_nonempty(
                    [current_text, next_text],
                    rx,
                )

                merged_images = dedupe_images(
                    (current.get("images") or []) + (next_chunk.get("images") or [])
                )

                merged.append(
                    {
                        "chunk_id": current["chunk_id"],
                        "chunk_ord": current["chunk_ord"],
                        "section_path": current.get("section_path") or [],
                        "block_types": compact_block_types(
                            (current.get("block_types") or [])
                            + (next_chunk.get("block_types") or [])
                        ),
                        "images": merged_images,
                        "text": merged_text,
                        "char_len": len(merged_text),
                    }
                )

                index += 2
                continue

        merged.append(
            {
                **current,
                "text": current_text,
                "char_len": len(current_text),
            }
        )
        index += 1

    out: list[dict[str, Any]] = []
    for chunk_ord, chunk in enumerate(merged, start=1):
        text = norm_text(chunk.get("text") or "", rx)
        if not text:
            continue

        out.append(
            {
                **chunk,
                "chunk_id": make_chunk_id(entry_id, chunk_ord),
                "chunk_ord": chunk_ord,
                "text": text,
                "char_len": len(text),
            }
        )

    return out


def add_neighbor_context(
    chunks: list[dict[str, Any]],
    cfg: KnowledgeBaseChunkerConfig,
    rx: dict[str, re.Pattern[str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for index, chunk in enumerate(chunks):
        prev_chunk = chunks[index - 1] if index > 0 else None
        next_chunk = chunks[index + 1] if index + 1 < len(chunks) else None

        context_before = ""
        context_after = ""

        if prev_chunk and can_use_neighbor_context(prev_chunk, chunk, cfg):
            context_before = build_trailing_context(prev_chunk.get("text") or "", cfg, rx)

        if next_chunk and can_use_neighbor_context(chunk, next_chunk, cfg):
            context_after = build_leading_context(next_chunk.get("text") or "", cfg, rx)

        metadata = dict(chunk.get("metadata") or {})
        metadata["neighbor_context"] = {
            "before": context_before,
            "after": context_after,
            "same_section_only": cfg.neighbor_context_same_section_only,
        }

        out.append(
            {
                **chunk,
                "metadata": metadata,
            }
        )

    return out


def can_use_neighbor_context(
    left: dict[str, Any],
    right: dict[str, Any],
    cfg: KnowledgeBaseChunkerConfig,
) -> bool:
    if not cfg.neighbor_context_same_section_only:
        return True

    return (left.get("section_path") or []) == (right.get("section_path") or [])


def finalize_chunks(
    article: dict[str, Any],
    external_id: str,
    chunks: list[dict[str, Any]],
    rx: dict[str, re.Pattern[str]],
) -> list[KnowledgeBaseChunkDraft]:
    out: list[KnowledgeBaseChunkDraft] = []

    source_revision = article.get("revision")
    source_revision_text = str(source_revision) if source_revision is not None else None

    for chunk in chunks:
        text = norm_text(chunk.get("text") or "", rx)
        if not text:
            continue

        contextualized_text = build_contextualized_text(article, chunk, rx)
        if not contextualized_text:
            continue

        chunk_payload = {
            **chunk,
            "text": text,
            "contextualized_text": contextualized_text,
        }

        chunk_hash = compute_chunk_hash(article, chunk_payload)

        metadata = {
            "entry_id": article.get("entry_id"),
            "revision": article.get("revision"),
            "updated_at": article.get("updated_at"),
            **dict(chunk.get("metadata") or {}),
        }

        out.append(
            KnowledgeBaseChunkDraft(
                chunk_id=chunk["chunk_id"],
                chunk_ord=int(chunk["chunk_ord"]),
                external_id=external_id,
                source_url=article.get("source_url"),
                title=article.get("title"),
                full_title=article.get("full_title"),
                updated_at_src=article.get("updated_at"),
                source_revision=source_revision_text,
                section_path=list(chunk.get("section_path") or []),
                block_types=list(chunk.get("block_types") or []),
                images=dedupe_images(chunk.get("images") or []),
                text=text,
                contextualized_text=contextualized_text,
                char_len=len(text),
                approx_tokens=approx_tokens(len(text)),
                chunk_hash=chunk_hash,
                metadata=metadata,
            )
        )

    return out
