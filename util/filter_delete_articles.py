#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any


BASE_DIR = Path("/opt/ai-dev-agent/tmp/docs.axel.pro.max.axelnac")

CLEAN_DATA_DIR = BASE_DIR / "clean_data"

DELETE_IF_FULL_TITLE_CONTAINS = [
    "База знаний Логикор",
    "Версия 1.0.0 / AxelNAC",
    "Версия 1.2.0 / AxelNAC",
    "Версия 2.0.1 / AxelNAC",
]

DELETED_CSV_PATH = BASE_DIR / "deleted_articles.csv"
KEPT_CSV_PATH = BASE_DIR / "kept_articles.csv"
STATE_CRAWL_PATH = BASE_DIR / "state_crawl.json"

ARTICLE_JSON_NAME = "article.json"


def normalize_text(value: str) -> str:
    return " ".join(value.strip().casefold().replace("ё", "е").split())


def should_delete_article(full_title: str) -> bool:
    normalized_full_title = normalize_text(full_title)

    for keyword in DELETE_IF_FULL_TITLE_CONTAINS:
        normalized_keyword = normalize_text(keyword)

        if normalized_keyword and normalized_keyword in normalized_full_title:
            return True

    return False


def iter_article_dirs(clean_data_dir: Path) -> list[Path]:
    if not clean_data_dir.exists():
        raise SystemExit(f"CLEAN_DATA_DIR does not exist: {clean_data_dir}")

    if not clean_data_dir.is_dir():
        raise SystemExit(f"CLEAN_DATA_DIR is not a directory: {clean_data_dir}")

    return sorted(
        [
            path
            for path in clean_data_dir.iterdir()
            if path.is_dir() and path.name.isdigit()
        ],
        key=lambda path: int(path.name),
    )


def load_article(article_dir: Path) -> dict[str, Any] | None:
    article_path = article_dir / ARTICLE_JSON_NAME

    if not article_path.exists():
        print(f"[WARN] article.json not found: {article_path}")
        return None

    try:
        return json.loads(article_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"[WARN] invalid JSON: {article_path}: {error}")
        return None


def article_id_from(article_dir: Path, article: dict[str, Any]) -> str:
    entry_id = article.get("entry_id")

    if entry_id is not None:
        return str(entry_id)

    return article_dir.name


def full_title_from(article: dict[str, Any]) -> str:
    value = article.get("full_title")

    if value is None:
        return ""

    return str(value)


def source_url_from(article: dict[str, Any]) -> str:
    value = article.get("source_url")

    if value is None:
        return ""

    return str(value)


def append_deleted_row(article_id: str, full_title: str) -> None:
    need_header = (
        not DELETED_CSV_PATH.exists()
        or DELETED_CSV_PATH.stat().st_size == 0
    )

    with DELETED_CSV_PATH.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)

        if need_header:
            writer.writerow(["article_id", "full_title"])

        writer.writerow([article_id, full_title])


def write_kept_rows(rows: list[tuple[str, str]]) -> None:
    with KEPT_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["article_id", "full_title"])

        for article_id, full_title in rows:
            writer.writerow([article_id, full_title])


def write_state_crawl(state: dict[str, str]) -> None:
    ordered_state = {
        article_id: state[article_id]
        for article_id in sorted(state.keys(), key=lambda value: int(value))
    }

    STATE_CRAWL_PATH.write_text(
        json.dumps(ordered_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    deleted_count = 0
    kept_rows: list[tuple[str, str]] = []
    state_crawl: dict[str, str] = {}

    for article_dir in iter_article_dirs(CLEAN_DATA_DIR):
        article = load_article(article_dir)
        if article is None:
            continue

        article_id = article_id_from(article_dir, article)
        full_title = full_title_from(article)
        source_url = source_url_from(article)

        if should_delete_article(full_title):
            shutil.rmtree(article_dir)
            append_deleted_row(article_id, full_title)

            deleted_count += 1
            print(f"[DELETE] {article_id}: {full_title}")
            continue

        kept_rows.append((article_id, full_title))

        if source_url:
            state_crawl[article_id] = source_url

    write_kept_rows(kept_rows)
    write_state_crawl(state_crawl)

    print()
    print("[DONE]")
    print(f"Deleted articles: {deleted_count}")
    print(f"Kept articles: {len(kept_rows)}")
    print(f"Deleted CSV: {DELETED_CSV_PATH}")
    print(f"Kept CSV: {KEPT_CSV_PATH}")
    print(f"State crawl JSON: {STATE_CRAWL_PATH}")


if __name__ == "__main__":
    main()
