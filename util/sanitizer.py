#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanitizer for KBPublisher HTML pages:
- extracts #kbp_article_body
- removes junk structurally
- converts HTML to RAG-friendly blocks
- extracts images + captions heuristically from HTML
- writes clean_data/<ENTRY_ID>/article.json + article.md

No CLI flags: edit constants below if needed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag, NavigableString


BASE_DIR = "/opt/ai-dev-agent/tmp/docs.axel.pro.20"
DATA_ROOT = Path(BASE_DIR + "/entries")
OUT_ROOT = Path(BASE_DIR + "/clean_data")


# If you want to test quickly, set to e.g. {10, 11, 12}. Empty => process all.
ONLY_ENTRY_IDS: set[int] = set()

WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n{3,}")

CAPTION_HINT_RE = re.compile(
    r"^\s*(рис(унок)?|figure|табл(ица)?|table|схема)\b",
    re.IGNORECASE,
)

# Structural media cleanup.
# Do not remove <picture>: it may contain useful <img>.
MEDIA_CONTAINER_TAGS = (
    "video",
    "audio",
    "iframe",
    "embed",
    "object",
    "canvas",
)

MEDIA_CHILD_TAGS = (
    "source",
    "track",
)


def norm_text(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = WS_RE.sub(" ", s)
    s = NL_RE.sub("\n\n", s)
    return s.strip()


def tag_text(tag: Tag) -> str:
    text = tag.get_text("\n", strip=True)
    return norm_text(text)


def tag_inline_text(tag: Tag) -> str:
    text = tag.get_text(" ", strip=True)
    return norm_text(text)


def is_empty_text(t: str) -> bool:
    return not t or not t.strip()


def is_likely_caption(text: str) -> bool:
    t = norm_text(text)

    if len(t) < 3:
        return False

    if len(t) > 240:
        return False

    if "\n" in t:
        return False

    if CAPTION_HINT_RE.search(t):
        return True

    return len(t) <= 140


def remove_media_markup(tag: Tag) -> None:
    """
    Remove HTML media containers and their fallback text structurally.

    This avoids indexing browser fallback text from tags such as <video> or <audio>.
    The logic is tag-based, not phrase-based.
    """
    for media in tag.find_all(MEDIA_CONTAINER_TAGS):
        media.decompose()

    for media_child in tag.find_all(MEDIA_CHILD_TAGS):
        media_child.decompose()


def clean_inline(tag: Tag) -> None:
    for bad in tag.select("script, style, noscript, template"):
        bad.decompose()

    remove_media_markup(tag)

    toc = tag.select_one("#toc_container")
    if toc:
        toc.decompose()

    for a in tag.find_all("a"):
        if a.get("id") and not a.get_text(strip=True) and not a.get("href"):
            a.decompose()

    for c in tag.find_all(
        string=lambda x: isinstance(x, NavigableString)
        and "x-tinymce/html" in str(x)
    ):
        c.extract()


@dataclass
class ImageRef:
    local_path: str
    alt: Optional[str] = None
    caption: Optional[str] = None


@dataclass
class Block:
    type: str  # heading|paragraph|list|code|table|callout
    text: str
    level: Optional[int] = None
    ordered: Optional[bool] = None
    items: Optional[list[str]] = None
    images: Optional[list[ImageRef]] = None


def _normalize_src(src: str) -> str:
    src = (src or "").strip().replace("\\", "/")
    return src.removeprefix("./")


def _is_local_asset_src(src: str) -> bool:
    src = _normalize_src(src)
    return src.startswith("assets/")


def _next_meaningful_sibling(tag: Tag) -> Optional[Tag]:
    nxt = tag.next_sibling

    while nxt is not None:
        if isinstance(nxt, NavigableString):
            nxt = nxt.next_sibling
            continue

        if not isinstance(nxt, Tag):
            nxt = nxt.next_sibling
            continue

        if nxt.name == "br":
            nxt = nxt.next_sibling
            continue

        if nxt.name == "p" and is_empty_text(tag_text(nxt)):
            nxt = nxt.next_sibling
            continue

        return nxt

    return None


def image_from_tag(img: Tag) -> Optional[ImageRef]:
    src = _normalize_src(img.get("src") or "")
    if not src or not _is_local_asset_src(src):
        return None

    alt = (img.get("alt") or "").strip() or None
    caption = None

    fig = img.find_parent("figure")
    if fig:
        fc = fig.find("figcaption")
        if fc:
            t = tag_text(fc)
            if t:
                caption = t

    if not caption:
        p = img.find_parent("p") or img.parent
        if isinstance(p, Tag):
            nxt = _next_meaningful_sibling(p)
            if isinstance(nxt, Tag) and nxt.name == "p":
                t = tag_text(nxt)
                if is_likely_caption(t):
                    caption = t

    return ImageRef(local_path=src, alt=alt, caption=caption)


def render_table_md(table: Tag) -> str:
    rows: list[list[str]] = []

    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue

        row = [norm_text(c.get_text(" ", strip=True)) for c in cells]
        rows.append(row)

    if not rows:
        return ""

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    header = rows[0]
    sep = ["---"] * width
    body = rows[1:] if len(rows) > 1 else []

    def md_row(r: list[str]) -> str:
        return "| " + " | ".join((c or "").replace("\n", " ") for c in r) + " |"

    out = [md_row(header), md_row(sep)]
    out.extend(md_row(r) for r in body)

    return "\n".join(out).strip()


def render_code(pre: Tag) -> str:
    code = pre.get_text("\n", strip=False)
    code = code.replace("\xa0", " ").strip("\n")
    return code.rstrip()


def callout_kind(tag: Tag) -> str:
    cls = " ".join(tag.get("class") or [])

    if "redBox" in cls:
        return "warning"

    if "greenBox" in cls:
        return "note"

    return "note"


def _collect_images(tag: Tag) -> list[ImageRef]:
    imgs: list[ImageRef] = []

    for img in tag.find_all("img"):
        ir = image_from_tag(img)
        if ir:
            imgs.append(ir)

    return imgs


def _li_own_text(li: Tag) -> str:
    """
    Return text from <li> without nested lists.

    This prevents nested lists from being duplicated inside the parent item.
    """
    li_copy = BeautifulSoup(str(li), "html.parser").find("li")
    if not isinstance(li_copy, Tag):
        return ""

    for nested in li_copy.find_all(["ul", "ol"]):
        nested.decompose()

    return tag_inline_text(li_copy)


def _direct_child_lists(li: Tag) -> list[Tag]:
    lists: list[Tag] = []

    for child in li.children:
        if isinstance(child, Tag) and child.name in ("ul", "ol"):
            lists.append(child)

    return lists


def _render_nested_list_lines(list_tag: Tag, depth: int) -> list[str]:
    """
    Render nested HTML lists into readable Markdown-like lines.

    The function is structural:
    - it does not depend on document topic;
    - it does not search for domain words;
    - it preserves hierarchy instead of flattening nested items into one line.
    """
    lines: list[str] = []
    ordered = list_tag.name == "ol"
    indent = "  " * depth

    for index, li in enumerate(list_tag.find_all("li", recursive=False), start=1):
        own_text = _li_own_text(li)
        marker = f"{index}." if ordered else "-"

        if own_text:
            lines.append(f"{indent}{marker} {own_text}")

        for child_list in _direct_child_lists(li):
            lines.extend(_render_nested_list_lines(child_list, depth + 1))

    return lines


def _list_item_text(li: Tag) -> str:
    """
    Convert one <li> into text while preserving nested list structure.

    The outer marker is not included here. It will be added later by the block
    renderer/chunker. Nested markers are included because they belong to the
    item content itself.
    """
    own_text = _li_own_text(li)
    nested_lines: list[str] = []

    for child_list in _direct_child_lists(li):
        nested_lines.extend(_render_nested_list_lines(child_list, depth=1))

    if own_text and nested_lines:
        return norm_text(own_text + "\n" + "\n".join(nested_lines))

    if own_text:
        return norm_text(own_text)

    return norm_text("\n".join(nested_lines))


def list_items_from_tag(list_tag: Tag) -> list[str]:
    items: list[str] = []

    for li in list_tag.find_all("li", recursive=False):
        text = _list_item_text(li)
        if text:
            items.append(text)

    return items


def _attach_caption_to_prev_images_if_any(blocks: list[Block], caption: str) -> bool:
    """Attach caption to previous block images if missing, or drop duplicate caption paragraphs."""
    cap = norm_text(caption)
    if not cap or not blocks:
        return False

    prev = blocks[-1]
    if not prev.images:
        return False

    changed = False

    for im in prev.images:
        if im.caption:
            if norm_text(im.caption) == cap:
                changed = True
                continue
        else:
            im.caption = cap
            changed = True

    return changed


def parse_body_to_blocks(body: Tag) -> list[Block]:
    blocks: list[Block] = []

    # Atomic nodes: when we emit them, we must skip their descendants later.
    # This prevents table internals becoming stray paragraphs.
    atomic_roots: set[int] = set()

    def mark_atomic(tag: Tag) -> None:
        atomic_roots.add(id(tag))

    def is_inside_atomic(tag: Tag) -> bool:
        parent = tag.parent

        while isinstance(parent, Tag):
            if id(parent) in atomic_roots:
                return True

            if parent is body:
                break

            parent = parent.parent

        return False

    def is_inside_callout(tag: Tag) -> bool:
        parent = tag.parent

        while isinstance(parent, Tag):
            if parent.name == "div" and any(
                c in (parent.get("class") or [])
                for c in ("greenBox", "redBox")
            ):
                return True

            if parent is body:
                break

            parent = parent.parent

        return False

    for node in body.descendants:
        if not isinstance(node, Tag):
            continue

        if is_inside_atomic(node):
            continue

        name = (node.name or "").lower().strip()

        # Media should normally already be removed by clean_inline().
        # Keep this guard so parser remains safe if called directly.
        if name in MEDIA_CONTAINER_TAGS or name in MEDIA_CHILD_TAGS:
            mark_atomic(node)
            continue

        # Callouts: atomic
        if name == "div" and any(
            c in (node.get("class") or [])
            for c in ("greenBox", "redBox")
        ):
            kind = callout_kind(node)
            text = tag_text(node)
            imgs = _collect_images(node)

            if text or imgs:
                blocks.append(
                    Block(
                        type="callout",
                        text=f"{kind.upper()}: {text}" if text else f"{kind.upper()}:",
                        images=imgs,
                    )
                )

            mark_atomic(node)
            continue

        if is_inside_callout(node):
            continue

        # Headings
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = tag_text(node)
            if text:
                blocks.append(
                    Block(
                        type="heading",
                        text=text,
                        level=int(name[1]),
                        images=_collect_images(node),
                    )
                )

            mark_atomic(node)
            continue

        # Tables: atomic
        if name == "table":
            md = render_table_md(node)
            imgs = _collect_images(node)

            if md or imgs:
                blocks.append(Block(type="table", text=md, images=imgs))

            mark_atomic(node)
            continue

        # Code blocks: atomic
        if name == "pre":
            code = render_code(node)
            imgs = _collect_images(node)

            if code or imgs:
                blocks.append(Block(type="code", text=code, images=imgs))

            mark_atomic(node)
            continue

        # Figures: atomic
        if name == "figure":
            imgs: list[ImageRef] = []

            for img in node.find_all("img"):
                ir = image_from_tag(img)
                if ir:
                    imgs.append(ir)

            fc = node.find("figcaption")
            if fc:
                cap = tag_text(fc)
                if is_likely_caption(cap):
                    for im in imgs:
                        if not im.caption:
                            im.caption = cap

            if imgs:
                blocks.append(Block(type="paragraph", text="", images=imgs))

            mark_atomic(node)
            continue

        # Lists: atomic, top-level only.
        #
        # Nested lists are rendered into the parent item as indented
        # Markdown-like lines. This keeps the semantic structure and avoids
        # artifacts such as "::", ";;", or duplicated list markers.
        if name in ("ul", "ol"):
            parent_list = node.find_parent(["ul", "ol"])
            if parent_list and parent_list is not node:
                continue

            ordered = name == "ol"
            items = list_items_from_tag(node)
            imgs = _collect_images(node)

            if items or imgs:
                blocks.append(
                    Block(
                        type="list",
                        text="",
                        ordered=ordered,
                        items=items or None,
                        images=imgs,
                    )
                )

            mark_atomic(node)
            continue

        # Paragraphs
        if name == "p":
            txt = tag_text(node)
            imgs = _collect_images(node)

            if txt and is_likely_caption(txt):
                if _attach_caption_to_prev_images_if_any(blocks, txt):
                    mark_atomic(node)
                    continue

            if txt or imgs:
                blocks.append(Block(type="paragraph", text=txt, images=imgs))

            mark_atomic(node)
            continue

        # Standalone images, not inside already handled structures
        if name == "img":
            if node.find_parent(["figure", "p", "pre", "table", "li"]):
                continue

            ir = image_from_tag(node)
            if ir:
                blocks.append(Block(type="paragraph", text="", images=[ir]))

            mark_atomic(node)
            continue

    for block in blocks:
        if block.images is None:
            block.images = []

    return blocks


def block_to_plain_lines(block: Block) -> list[str]:
    out: list[str] = []

    if block.type == "heading" and block.text:
        out.append(block.text)

    elif block.type == "paragraph":
        if block.text:
            out.append(block.text)

    elif block.type == "callout" and block.text:
        out.append(block.text)

    elif block.type == "list":
        if block.items:
            if block.ordered:
                n = 1
                for item in block.items:
                    out.append(f"{n}. {item}")
                    n += 1
            else:
                for item in block.items:
                    out.append(f"- {item}")

    elif block.type == "code" and block.text:
        out.append(block.text)

    elif block.type == "table" and block.text:
        out.append(block.text)

    # Add image captions/alts as text anchors. This helps retrieval.
    if block.images:
        for image in block.images:
            caption = image.caption or image.alt
            if caption:
                out.append(caption)

    return [line for line in (norm_text(x) for x in out) if line]


def blocks_to_plain_text(blocks: list[Block]) -> str:
    parts: list[str] = []

    for block in blocks:
        lines = block_to_plain_lines(block)
        if lines:
            parts.append("\n".join(lines))

    return "\n\n".join(parts).strip()


def blocks_to_markdown(meta: dict[str, Any], blocks: list[Block]) -> str:
    lines: list[str] = []

    title = meta.get("title") or f"Entry {meta.get('entry_id')}"
    lines.append(f"# {title}")

    if meta.get("source_url"):
        lines.append(f"Источник: {meta['source_url']}")

    if meta.get("updated_at") or meta.get("revision"):
        lines.append(
            f"Обновлено: {meta.get('updated_at')} | Ревизия: {meta.get('revision')}"
        )

    lines.append("")

    img_idx = 0

    for block in blocks:
        if block.type == "heading":
            level = block.level or 1
            lines.append("#" * min(max(level, 1), 6) + " " + block.text)
            lines.append("")

        elif block.type == "paragraph":
            if block.text:
                lines.append(block.text)
                lines.append("")

        elif block.type == "callout":
            lines.append(f"> {block.text}")
            lines.append("")

        elif block.type == "list":
            if block.items:
                if block.ordered:
                    n = 1
                    for item in block.items:
                        lines.append(f"{n}. {item}")
                        n += 1
                else:
                    for item in block.items:
                        lines.append(f"- {item}")

                lines.append("")

        elif block.type == "code":
            lines.append("```")
            lines.append(block.text)
            lines.append("```")
            lines.append("")

        elif block.type == "table":
            lines.append(block.text)
            lines.append("")

        if block.images:
            for image in block.images:
                img_idx += 1

                caption = image.caption or image.alt or ""
                caption = f" — {caption}" if caption else ""

                lines.append(f"![img{img_idx}{caption}]({image.local_path})")

            lines.append("")

    return "\n".join(lines).strip() + "\n"


def main() -> None:
    if not DATA_ROOT.exists():
        raise SystemExit(f"DATA_ROOT not found: {DATA_ROOT}")

    entries = sorted(
        [p for p in DATA_ROOT.iterdir() if p.is_dir() and p.name.isdigit()],
        key=lambda p: int(p.name),
    )

    if ONLY_ENTRY_IDS:
        entries = [p for p in entries if int(p.name) in ONLY_ENTRY_IDS]

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    total = 0

    for entry_dir in entries:
        entry_id = int(entry_dir.name)
        meta_path = entry_dir / "meta.json"
        html_path = entry_dir / "page.html"

        if not meta_path.exists() or not html_path.exists():
            continue

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        html = html_path.read_text(encoding="utf-8", errors="replace")

        soup = BeautifulSoup(html, "html.parser")
        body = soup.select_one("#kbp_article_body")

        if not body:
            print(f"[WARN] entry {entry_id}: #kbp_article_body not found")
            continue

        clean_inline(body)
        blocks = parse_body_to_blocks(body)

        article = {
            "entry_id": meta.get("entry_id", entry_id),
            "source_url": meta.get("source_url"),
            "title": meta.get("title"),
            "full_title": meta.get("full_title"),
            "updated_at": meta.get("updated_at"),
            "revision": meta.get("revision"),
            "blocks": [
                {
                    **{
                        k: v
                        for k, v in asdict(block).items()
                        if v not in (None, "", [], {})
                    },
                    "images": [
                        {
                            k: v
                            for k, v in asdict(image).items()
                            if v not in (None, "", [], {})
                        }
                        for image in (block.images or [])
                    ]
                    if block.images is not None
                    else [],
                }
                for block in blocks
            ],
            "plain_text": blocks_to_plain_text(blocks),
        }

        out_dir = OUT_ROOT / str(entry_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "article.json").write_text(
            json.dumps(article, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        (out_dir / "article.md").write_text(
            blocks_to_markdown(meta, blocks),
            encoding="utf-8",
        )

        total += 1

        if total % 25 == 0:
            print(f"[OK] sanitized {total} entries...")

    print(f"[DONE] sanitized entries: {total}")
    print(f"Output: {OUT_ROOT}")


if __name__ == "__main__":
    main()
