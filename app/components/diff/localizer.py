import re
from dataclasses import dataclass


@dataclass
class LineLocalizationResult:
    ok: bool
    file_path: str | None = None
    new_line: int | None = None
    reason: str = ""


class DiffLineLocalizer:
    def _normalize(self, value: str) -> str:
        return " ".join(value.strip().split())

    def _soft_normalize(self, value: str) -> str:
        value = value.strip()
        value = value.replace("\\\\", "\\")
        value = value.replace('\\"', '"')
        value = value.replace("\\'", "'")
        value = value.replace("\\@", "@")
        value = value.replace("\\.", ".")
        value = value.replace("\\/", "/")
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _anchor_variants(self, value: str) -> list[str]:
        variants = set()

        for part in value.splitlines():
            part = part.strip()
            if not part:
                continue

            variants.add(part)
            variants.add(self._soft_normalize(part))

            # Часто LLM возвращает строку без начальных пробелов или с JSON-escaping.
            variants.add(part.lstrip())
            variants.add(self._soft_normalize(part.lstrip()))

        variants.add(value.strip())
        variants.add(self._soft_normalize(value))

        return [v for v in variants if v]

    def _matches(self, anchor: str, line: str) -> bool:
        anchor_norm = self._normalize(anchor)
        line_norm = self._normalize(line)

        if anchor_norm and anchor_norm in line_norm:
            return True

        anchor_soft = self._soft_normalize(anchor)
        line_soft = self._soft_normalize(line)

        if anchor_soft and anchor_soft in line_soft:
            return True

        # Последняя попытка: убрать пробелы вокруг операторов и сравнить компактно.
        compact_anchor = re.sub(r"\s+", "", anchor_soft)
        compact_line = re.sub(r"\s+", "", line_soft)

        return bool(compact_anchor and compact_anchor in compact_line)

    def _extract_added_lines(self, diff_text: str) -> list[tuple[int, str, int]]:
        added_lines: list[tuple[int, str, int]] = []
        current_new_line: int | None = None

        for raw_index, raw_line in enumerate(diff_text.splitlines()):
            if raw_line.startswith("@@"):
                match = re.search(r"\+(\d+)(?:,(\d+))?", raw_line)
                current_new_line = int(match.group(1)) if match else None
                continue

            if current_new_line is None:
                continue

            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                added_lines.append((current_new_line, raw_line[1:], raw_index))
                current_new_line += 1
            elif raw_line.startswith("-") and not raw_line.startswith("---"):
                continue
            elif raw_line.startswith("\\"):
                continue
            else:
                current_new_line += 1

        return added_lines

    def _line_matches_context(
        self,
        all_lines: list[str],
        index: int,
        before_anchor: str | None,
        after_anchor: str | None,
    ) -> bool:
        if before_anchor:
            before_window = "\n".join(all_lines[max(0, index - 10):index])
            if not any(
                self._matches(anchor, before_window)
                for anchor in self._anchor_variants(before_anchor)
            ):
                return False

        if after_anchor:
            after_window = "\n".join(all_lines[index + 1:index + 11])
            if not any(
                self._matches(anchor, after_window)
                for anchor in self._anchor_variants(after_anchor)
            ):
                return False

        return True

    def locate_line(
        self,
        file_path: str | None,
        anchor_text: str | None,
        before_anchor: str | None,
        after_anchor: str | None,
        file_diff: dict,
    ) -> LineLocalizationResult:
        if not file_path:
            return LineLocalizationResult(
                ok=False,
                reason="file_path is required for line localization",
            )

        if not anchor_text:
            return LineLocalizationResult(
                ok=False,
                reason="anchor_text is required for line localization",
            )

        new_path = file_diff.get("new_path")
        if file_path != new_path:
            return LineLocalizationResult(
                ok=False,
                reason=f"file_path '{file_path}' does not match diff new_path '{new_path}'",
            )

        diff_text = file_diff.get("diff", "")
        diff_lines = diff_text.splitlines()
        added_lines = self._extract_added_lines(diff_text)
        anchor_variants = self._anchor_variants(anchor_text)

        candidates: list[tuple[int, int]] = []

        for new_line, text, raw_index in added_lines:
            if any(self._matches(anchor, text) for anchor in anchor_variants):
                candidates.append((new_line, raw_index))

        if not candidates:
            return LineLocalizationResult(
                ok=False,
                reason="anchor_text was not found on added lines in MR diff",
            )

        unique_lines = sorted({line for line, _ in candidates})

        if len(unique_lines) == 1:
            return LineLocalizationResult(
                ok=True,
                file_path=file_path,
                new_line=unique_lines[0],
                reason="anchor_text matched uniquely on added line in MR diff",
            )

        context_candidates: list[int] = []

        for new_line, raw_index in candidates:
            if self._line_matches_context(
                all_lines=diff_lines,
                index=raw_index,
                before_anchor=before_anchor,
                after_anchor=after_anchor,
            ):
                context_candidates.append(new_line)

        unique_context_lines = sorted(set(context_candidates))

        if len(unique_context_lines) == 1:
            return LineLocalizationResult(
                ok=True,
                file_path=file_path,
                new_line=unique_context_lines[0],
                reason="anchor_text was ambiguous, context anchors resolved it",
            )

        if not unique_context_lines:
            return LineLocalizationResult(
                ok=False,
                reason=(
                    "anchor_text is ambiguous and context anchors did not resolve it, "
                    f"candidates: {unique_lines}"
                ),
            )

        return LineLocalizationResult(
            ok=False,
            reason=(
                "anchor_text is still ambiguous after context matching, "
                f"candidates: {unique_context_lines}"
            ),
        )
