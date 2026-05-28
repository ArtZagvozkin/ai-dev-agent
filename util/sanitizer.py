#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sanitizer for KBPublisher HTML pages:
- extracts #kbp_article_body
- removes junk (TOC, scripts/styles)
- converts to RAG-friendly blocks (keeps original flow)
- extracts images + captions heuristics from HTML (NOT meta.json)
- writes clean_data/entries/<ENTRY_ID>/article.json + article.md

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
    re.IGNORECASE
)

def norm_text(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = WS_RE.sub(" ", s)
    s = NL_RE.sub("\n\n", s)
    return s.strip()

def tag_text(tag: Tag) -> str:
    text = tag.get_text("\n", strip=True)
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

def clean_inline(tag: Tag) -> None:
    for bad in tag.select("script, style, noscript"):
        bad.decompose()

    toc = tag.select_one("#toc_container")
    if toc:
        toc.decompose()

    for a in tag.find_all("a"):
        if a.get("id") and not a.get_text(strip=True) and not a.get("href"):
            a.decompose()

    for c in tag.find_all(string=lambda x: isinstance(x, NavigableString) and "x-tinymce/html" in str(x)):
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
    nxt = tag.find_next_sibling()
    while isinstance(nxt, Tag):
        if nxt.name == "br":
            nxt = nxt.find_next_sibling()
            continue
        if nxt.name == "p" and is_empty_text(tag_text(nxt)):
            nxt = nxt.find_next_sibling()
            continue
        break
    return nxt if isinstance(nxt, Tag) else None

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

def _li_text(li: Tag) -> str:
    # avoid nested list duplication
    for n in li.find_all(["ul", "ol"]):
        n.extract()
    return norm_text(li.get_text(" ", strip=True))

def _attach_caption_to_prev_images_if_any(blocks: list[Block], caption: str) -> bool:
    """Attach caption to previous block images (if missing) or drop duplicate caption paragraphs."""
    cap = norm_text(caption)
    if not cap or not blocks:
        return False

    prev = blocks[-1]
    if not prev.images:
        return False

    changed = False
    for im in prev.images:
        if im.caption:
            # if same caption already present -> treat as duplicate paragraph
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
    # This prevents table internals becoming stray paragraphs (your "Аутентификатор/NAS" bug).
    atomic_roots: set[int] = set()

    def mark_atomic(tag: Tag) -> None:
        atomic_roots.add(id(tag))

    def is_inside_atomic(tag: Tag) -> bool:
        p = tag.parent
        while isinstance(p, Tag):
            if id(p) in atomic_roots:
                return True
            if p is body:
                break
            p = p.parent
        return False

    def is_inside_callout(tag: Tag) -> bool:
        p = tag.parent
        while isinstance(p, Tag):
            if p.name == "div" and any(c in (p.get("class") or []) for c in ("greenBox", "redBox")):
                return True
            if p is body:
                break
            p = p.parent
        return False

    # Walk in document order
    for node in body.descendants:
        if not isinstance(node, Tag):
            continue

        # Skip anything inside an already-emitted atomic root
        if is_inside_atomic(node):
            continue

        name = (node.name or "").lower().strip()

        # Callouts: atomic
        if name == "div" and any(c in (node.get("class") or []) for c in ("greenBox", "redBox")):
            kind = callout_kind(node)
            text = tag_text(node)
            imgs = _collect_images(node)
            if text or imgs:
                blocks.append(Block(type="callout", text=f"{kind.upper()}: {text}" if text else f"{kind.upper()}:", images=imgs))
            mark_atomic(node)
            continue

        if is_inside_callout(node):
            continue

        # Headings
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = tag_text(node)
            if text:
                blocks.append(Block(type="heading", text=text, level=int(name[1]), images=_collect_images(node)))
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

        # Lists: atomic (top-level only)
        if name in ("ul", "ol"):
            parent_list = node.find_parent(["ul", "ol"])
            if parent_list and parent_list is not node:
                continue

            ordered = (name == "ol")
            items: list[str] = []
            imgs: list[ImageRef] = []

            for li in node.find_all("li", recursive=False):
                imgs.extend(_collect_images(li))
                t = _li_text(li)
                if t:
                    items.append(t)

                for sub in li.find_all(["ul", "ol"], recursive=False):
                    sub_ordered = (sub.name == "ol")
                    for sub_li in sub.find_all("li", recursive=False):
                        imgs.extend(_collect_images(sub_li))
                        st = _li_text(sub_li)
                        if st:
                            prefix = "1." if sub_ordered else "-"
                            items.append(f"  {prefix} {st}")

            if items or imgs:
                blocks.append(Block(type="list", text="", ordered=ordered, items=items or None, images=imgs))
            mark_atomic(node)
            continue

        # Paragraphs
        if name == "p":
            txt = tag_text(node)
            imgs = _collect_images(node)

            # If this paragraph looks like a caption, try to attach to previous images
            if txt and is_likely_caption(txt):
                if _attach_caption_to_prev_images_if_any(blocks, txt):
                    # Drop caption paragraph (either attached or duplicate)
                    mark_atomic(node)
                    continue

            if txt or imgs:
                blocks.append(Block(type="paragraph", text=txt, images=imgs))
            mark_atomic(node)
            continue

        # Standalone images (not inside p/figure/table/pre/li)
        if name == "img":
            if node.find_parent(["figure", "p", "pre", "table", "li"]):
                continue
            ir = image_from_tag(node)
            if ir:
                blocks.append(Block(type="paragraph", text="", images=[ir]))
            mark_atomic(node)
            continue

        # Generic divs/sections/articles: ignore (avoid duplicates)
        # Anything else: ignore as block root

    for b in blocks:
        if b.images is None:
            b.images = []
    return blocks

def block_to_plain_lines(b: Block) -> list[str]:
    out: list[str] = []

    if b.type == "heading" and b.text:
        out.append(b.text)
    elif b.type == "paragraph":
        if b.text:
            out.append(b.text)
    elif b.type == "callout" and b.text:
        out.append(b.text)
    elif b.type == "list" and b.items:
        for it in b.items:
            out.append(f"- {it}" if not it.lstrip().startswith(("1.", "-")) else it)
    elif b.type == "code" and b.text:
        out.append(b.text)
    elif b.type == "table" and b.text:
        out.append(b.text)

    # Add image captions/alts as text anchors (helps retrieval)
    if b.images:
        for im in b.images:
            cap = im.caption or im.alt
            if cap:
                out.append(cap)

    return [line for line in (norm_text(x) for x in out) if line]

def blocks_to_plain_text(blocks: list[Block]) -> str:
    parts: list[str] = []
    for b in blocks:
        lines = block_to_plain_lines(b)
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
        lines.append(f"Обновлено: {meta.get('updated_at')} | Ревизия: {meta.get('revision')}")
    lines.append("")

    img_idx = 0
    for b in blocks:
        if b.type == "heading":
            lvl = b.level or 1
            lines.append("#" * min(max(lvl, 1), 6) + " " + b.text)
            lines.append("")
        elif b.type == "paragraph":
            if b.text:
                lines.append(b.text)
                lines.append("")
        elif b.type == "callout":
            lines.append(f"> {b.text}")
            lines.append("")
        elif b.type == "list":
            if b.items:
                if b.ordered:
                    n = 1
                    for it in b.items:
                        lines.append(f"{n}. {it}")
                        n += 1
                else:
                    for it in b.items:
                        lines.append(f"- {it}")
                lines.append("")
        elif b.type == "code":
            lines.append("```")
            lines.append(b.text)
            lines.append("```")
            lines.append("")
        elif b.type == "table":
            lines.append(b.text)
            lines.append("")

        if b.images:
            for im in b.images:
                img_idx += 1
                cap = im.caption or im.alt or ""
                cap = f" — {cap}" if cap else ""
                lines.append(f"![img{img_idx}{cap}]({im.local_path})")
            lines.append("")

    return "\n".join(lines).strip() + "\n"

def main() -> None:
    if not DATA_ROOT.exists():
        raise SystemExit(f"DATA_ROOT not found: {DATA_ROOT}")

    entries = sorted(
        [p for p in DATA_ROOT.iterdir() if p.is_dir() and p.name.isdigit()],
        key=lambda p: int(p.name)
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
                    **{k: v for k, v in asdict(b).items() if v not in (None, "", [], {})},
                    "images": [
                        {k: v for k, v in asdict(im).items() if v not in (None, "", [], {})}
                        for im in (b.images or [])
                    ] if (b.images is not None) else []
                }
                for b in blocks
            ],
            "plain_text": blocks_to_plain_text(blocks),
        }

        out_dir = OUT_ROOT / str(entry_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "article.json").write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "article.md").write_text(blocks_to_markdown(meta, blocks), encoding="utf-8")

        total += 1
        if total % 25 == 0:
            print(f"[OK] sanitized {total} entries...")

    print(f"[DONE] sanitized entries: {total}")
    print(f"Output: {OUT_ROOT}")

if __name__ == "__main__":
    main()
