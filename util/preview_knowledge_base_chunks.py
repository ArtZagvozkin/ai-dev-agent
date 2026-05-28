#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from app.components.knowledge_base.chunker import (  # noqa: E402
    KnowledgeBaseChunkerConfig,
    chunk_article,
    compute_article_hash,
)


DEFAULT_INPUT_DIR = Path("/opt/ai-dev-agent/tmp/docs.axel.pro.20/clean_data")
DEFAULT_OUTPUT_MD = Path("/opt/ai-dev-agent/tmp/docs.axel.pro.20/chunks_preview.md")
DEFAULT_OUTPUT_JSONL = Path("/opt/ai-dev-agent/tmp/docs.axel.pro.20/chunks.jsonl")


def parse_only(value: Optional[str]) -> set[int]:
    if not value:
        return set()

    result: set[int] = set()

    for part in value.split(","):
        part = part.strip()
        if not part:
            continue

        if not part.isdigit():
            raise ValueError(f"Invalid entry id in --only: {part!r}")

        result.add(int(part))

    return result


def iter_article_paths(input_dir: Path, only_entry_ids: set[int]) -> list[Path]:
    if not input_dir.exists():
        raise SystemExit(f"Input directory not found: {input_dir}")

    entries = sorted(
        [path for path in input_dir.iterdir() if path.is_dir() and path.name.isdigit()],
        key=lambda path: int(path.name),
    )

    if only_entry_ids:
        entries = [path for path in entries if int(path.name) in only_entry_ids]

    article_paths: list[Path] = []

    for entry_dir in entries:
        article_path = entry_dir / "article.json"
        if article_path.exists():
            article_paths.append(article_path)

    return article_paths


def load_article(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def section_path_to_text(section_path: list[str]) -> str:
    if not section_path:
        return "(без раздела)"

    return " / ".join(section_path)


def write_preview_header(
    f_md,
    input_dir: Path,
    cfg: KnowledgeBaseChunkerConfig,
) -> None:
    f_md.write("# Knowledge base chunks preview\n\n")
    f_md.write(f"- Input: `{input_dir}`\n")
    f_md.write(f"- target_chars: `{cfg.target_chars}`\n")
    f_md.write(f"- max_chars: `{cfg.max_chars}`\n")
    f_md.write(f"- min_chars: `{cfg.min_chars}`\n")
    f_md.write(f"- neighbor_context_max_chars: `{cfg.neighbor_context_max_chars}`\n")
    f_md.write(f"- neighbor_context_same_section_only: `{cfg.neighbor_context_same_section_only}`\n\n")


def write_article_preview(
    f_md,
    article: dict[str, Any],
    article_hash: str,
    chunks: list,
    article_content: str,
) -> None:
    entry_id = article.get("entry_id")
    title = article.get("title") or ""
    source_url = article.get("source_url") or ""
    full_title = article.get("full_title") or ""
    updated_at = article.get("updated_at")
    revision = article.get("revision")

    f_md.write("\n---\n\n")
    f_md.write(f"## Entry {entry_id} — {title}\n\n")

    if source_url:
        f_md.write(f"- Source: {source_url}\n")

    if full_title:
        f_md.write(f"- Full title: {full_title}\n")

    if updated_at or revision:
        f_md.write(f"- Updated: {updated_at} | Revision: {revision}\n")

    f_md.write(f"- Article hash: `{article_hash}`\n")
    f_md.write(f"- Article content chars: `{len(article_content)}`\n")
    f_md.write(f"- Chunks: `{len(chunks)}`\n\n")

    for chunk in chunks:
        neighbor_context = (chunk.metadata or {}).get("neighbor_context") or {}
        context_before = neighbor_context.get("before") or ""
        context_after = neighbor_context.get("after") or ""

        f_md.write(
            f"### {chunk.chunk_id} "
            f"({chunk.char_len} chars, ~{chunk.approx_tokens} tokens)\n\n"
        )

        f_md.write(f"- Section: {section_path_to_text(chunk.section_path)}\n")
        f_md.write(f"- Types: {', '.join(chunk.block_types) or '-'}\n")
        f_md.write(f"- Images: {len(chunk.images)}\n")
        f_md.write(f"- Chunk hash: `{chunk.chunk_hash}`\n")

        if context_before:
            f_md.write(f"- Neighbor before: {context_before}\n")

        if context_after:
            f_md.write(f"- Neighbor after: {context_after}\n")

        f_md.write("\n")

        f_md.write("#### Text\n\n")
        f_md.write(chunk.text.replace("\n", "\n\n"))
        f_md.write("\n\n")

        f_md.write("#### Contextualized text\n\n")
        f_md.write("```text\n")
        f_md.write(chunk.contextualized_text)
        f_md.write("\n```\n\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview chunks for sanitized knowledge base articles."
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory with <entry_id>/article.json files.",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_OUTPUT_MD,
        help="Path to markdown preview file.",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=DEFAULT_OUTPUT_JSONL,
        help="Path to JSONL chunks file.",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated entry ids, e.g. 11,17,528.",
    )

    parser.add_argument("--target-chars", type=int, default=2300)
    parser.add_argument("--max-chars", type=int, default=3000)
    parser.add_argument("--min-chars", type=int, default=750)
    parser.add_argument("--neighbor-context-max-chars", type=int, default=220)

    args = parser.parse_args()

    only_entry_ids = parse_only(args.only)

    cfg = KnowledgeBaseChunkerConfig(
        target_chars=args.target_chars,
        max_chars=args.max_chars,
        min_chars=args.min_chars,
        neighbor_context_max_chars=args.neighbor_context_max_chars,
    )

    article_paths = iter_article_paths(args.input, only_entry_ids)

    if not article_paths:
        raise SystemExit(f"No article.json files found in: {args.input}")

    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    total_articles = 0
    total_chunks = 0

    with args.output_md.open("w", encoding="utf-8") as f_md, args.output_jsonl.open(
        "w",
        encoding="utf-8",
    ) as f_jsonl:
        write_preview_header(f_md, args.input, cfg)

        for article_path in article_paths:
            article = load_article(article_path)
            chunks, article_content = chunk_article(article, cfg)
            article_hash = compute_article_hash(article, article_content)

            if not chunks:
                continue

            total_articles += 1
            total_chunks += len(chunks)

            for chunk in chunks:
                payload = chunk.to_dict()
                payload["article_hash"] = article_hash
                f_jsonl.write(json.dumps(payload, ensure_ascii=False) + "\n")

            write_article_preview(
                f_md=f_md,
                article=article,
                article_hash=article_hash,
                chunks=chunks,
                article_content=article_content,
            )

        f_md.write("\n---\n\n")
        f_md.write("## Summary\n\n")
        f_md.write(f"- Articles: `{total_articles}`\n")
        f_md.write(f"- Chunks: `{total_chunks}`\n")

    print(f"[DONE] articles={total_articles}, chunks={total_chunks}")
    print(f"[OK] markdown preview: {args.output_md}")
    print(f"[OK] jsonl chunks:     {args.output_jsonl}")


if __name__ == "__main__":
    main()
