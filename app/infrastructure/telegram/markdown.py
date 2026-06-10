from __future__ import annotations

import re


MD_V2_SPECIAL_CHARS = set("_*[]()~`>#+-=|{}.!")
PLACEHOLDER_PREFIX = "\u0000TG"
PLACEHOLDER_SUFFIX = "\u0000"

HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LINK_RE = re.compile(r"!?\[([^\]]+)\]\(([^)]+)\)")
CODE_FENCE_RE = re.compile(r"^(`{3,})(.*)$")
LIST_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
NUMBERED_LIST_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")


def normalize_markdown_for_telegram(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    if not normalized:
        return ""

    normalized = _normalize_markdown_outside_code_blocks(normalized)
    normalized = _normalize_blank_lines(normalized)

    return normalized.strip()


def convert_to_md_v2(text: str) -> str:
    normalized = normalize_markdown_for_telegram(text)

    if not normalized:
        return ""

    lines = normalized.splitlines()
    out_lines: list[str] = []

    in_code_block = False
    code_fence_len = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.lstrip()

        if in_code_block:
            fence_match = CODE_FENCE_RE.match(stripped)
            if fence_match and len(fence_match.group(1)) == code_fence_len:
                out_lines.append("```")
                in_code_block = False
                code_fence_len = 0
                continue

            out_lines.append(_escape_code(line))
            continue

        fence_match = CODE_FENCE_RE.match(stripped)
        if fence_match:
            fence = fence_match.group(1)
            lang = fence_match.group(2).strip()

            in_code_block = True
            code_fence_len = len(fence)

            if lang:
                safe_lang = re.sub(r"[^A-Za-z0-9_+-]", "", lang)
                out_lines.append(f"```{safe_lang}")
            else:
                out_lines.append("```")

            continue

        if not line.strip():
            out_lines.append("")
            continue

        header_match = HEADER_RE.match(line)
        if header_match:
            header_text = header_match.group(2).strip()
            out_lines.append(f"*{_process_inline(header_text)}*")
            continue

        numbered_match = NUMBERED_LIST_RE.match(line)
        if numbered_match:
            indent = numbered_match.group(1)
            number = numbered_match.group(2)
            body = numbered_match.group(3).strip()
            out_lines.append(f"{indent}{number}\\. {_process_inline(body)}")
            continue

        list_match = LIST_RE.match(line)
        if list_match:
            indent = list_match.group(1)
            body = list_match.group(2).strip()
            out_lines.append(f"{indent}\\- {_process_inline(body)}")
            continue

        if stripped.startswith(">"):
            quote_text = stripped.lstrip(">").strip()
            if quote_text:
                out_lines.append(f"> {_process_inline(quote_text)}")
            else:
                out_lines.append(">")
            continue

        out_lines.append(_process_inline(line))

    if in_code_block:
        out_lines.append("```")

    return "\n".join(out_lines)


def split_md_v2(text: str, limit: int = 3900) -> list[str]:
    normalized_limit = max(1000, min(int(limit or 3900), 4096))

    if len(text) <= normalized_limit:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= normalized_limit:
            current += line
            continue

        if current.strip():
            chunks.append(current.rstrip())

        if len(line) <= normalized_limit:
            current = line
            continue

        for start in range(0, len(line), normalized_limit):
            part = line[start : start + normalized_limit].rstrip()
            if part:
                chunks.append(part)

        current = ""

    if current.strip():
        chunks.append(current.rstrip())

    return chunks


def split_plain_text(text: str, limit: int = 3900) -> list[str]:
    normalized_limit = max(1000, min(int(limit or 3900), 4096))

    if len(text) <= normalized_limit:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= normalized_limit:
            current += line
            continue

        if current.strip():
            chunks.append(current.rstrip())

        if len(line) <= normalized_limit:
            current = line
            continue

        for start in range(0, len(line), normalized_limit):
            part = line[start : start + normalized_limit].rstrip()
            if part:
                chunks.append(part)

        current = ""

    if current.strip():
        chunks.append(current.rstrip())

    return chunks


def _normalize_markdown_outside_code_blocks(text: str) -> str:
    parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    normalized_parts: list[str] = []

    for index, part in enumerate(parts):
        if index % 2 == 1:
            normalized_parts.append(part)
            continue

        normalized_parts.append(_restore_markdown_line_breaks(part))

    return "".join(normalized_parts)


def _restore_markdown_line_breaks(text: str) -> str:
    if not text:
        return ""

    normalized = text

    normalized = re.sub(
        r"(?<!\n)\s+(#{1,6}\s+)",
        r"\n\n\1",
        normalized,
    )

    normalized = re.sub(
        r"(?<!\n)\s+(\d+\.\s+)",
        r"\n\1",
        normalized,
    )

    normalized = re.sub(
        r"(?<!\n)\s+([*-]\s+)",
        r"\n\1",
        normalized,
    )

    normalized = _restore_bold_heading_line_breaks(normalized)

    return normalized


def _restore_bold_heading_line_breaks(text: str) -> str:
    pattern = re.compile(r"(?<!\n)\s+(\*\*[^*\n]{1,120}\*\*:)")

    def repl(match: re.Match) -> str:
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_prefix = text[line_start : match.start()].rstrip()

        if re.search(r"(^|\s)(?:[-*+]|\d+\.)$", line_prefix):
            return match.group(0)

        return f"\n\n{match.group(1)}"

    return pattern.sub(repl, text)


def _normalize_blank_lines(text: str) -> str:
    lines = [
        line.rstrip()
        for line in text.splitlines()
    ]

    result: list[str] = []
    previous_empty = False

    for line in lines:
        current_empty = not line.strip()

        if current_empty and previous_empty:
            continue

        result.append(line)
        previous_empty = current_empty

    return "\n".join(result)


def _process_inline(text: str) -> str:
    placeholders: dict[str, str] = {}
    processed = text

    processed = _replace_inline_code(processed, placeholders)
    processed = _replace_links(processed, placeholders)
    processed = _replace_bold(processed, placeholders)
    processed = _replace_strike(processed, placeholders)

    escaped = _escape_plain(processed)

    return _restore_placeholders(escaped, placeholders)


def _replace_inline_code(
    text: str,
    placeholders: dict[str, str],
) -> str:
    def repl(match: re.Match) -> str:
        code = _escape_code(match.group(1))
        return _new_placeholder(placeholders, f"`{code}`")

    return re.sub(r"`([^`\n]+)`", repl, text)


def _replace_links(
    text: str,
    placeholders: dict[str, str],
) -> str:
    def repl(match: re.Match) -> str:
        label = _escape_plain(match.group(1))
        url = _escape_link_url(match.group(2).strip())

        return _new_placeholder(placeholders, f"[{label}]({url})")

    return LINK_RE.sub(repl, text)


def _replace_bold(
    text: str,
    placeholders: dict[str, str],
) -> str:
    def repl(match: re.Match) -> str:
        inner = _escape_plain(match.group(1).strip())
        return _new_placeholder(placeholders, f"*{inner}*")

    return re.sub(r"\*\*(.+?)\*\*", repl, text)


def _replace_strike(
    text: str,
    placeholders: dict[str, str],
) -> str:
    def repl(match: re.Match) -> str:
        inner = _escape_plain(match.group(1).strip())
        return _new_placeholder(placeholders, f"~{inner}~")

    return re.sub(r"~~(.+?)~~", repl, text)


def _new_placeholder(
    placeholders: dict[str, str],
    value: str,
) -> str:
    key = f"{PLACEHOLDER_PREFIX}{len(placeholders)}{PLACEHOLDER_SUFFIX}"
    placeholders[key] = value

    return key


def _restore_placeholders(
    text: str,
    placeholders: dict[str, str],
) -> str:
    restored = text

    for key, value in placeholders.items():
        restored = restored.replace(key, value)

    return restored


def _escape_plain(text: str) -> str:
    result: list[str] = []

    for char in text:
        if char in MD_V2_SPECIAL_CHARS:
            result.append(f"\\{char}")
        else:
            result.append(char)

    return "".join(result)


def _escape_code(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _escape_link_url(url: str) -> str:
    return url.replace("\\", "\\\\").replace(")", "\\)")
