import hashlib
import re
from pathlib import Path

from app.components.code_search.embeddings import tokenize
from app.components.code_search.models import CodeChunk


TREE_SITTER_LANGUAGE_MAP = {
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cs": "c_sharp",
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".lua": "lua",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
}

LANGUAGE_BY_SUFFIX = {
    **TREE_SITTER_LANGUAGE_MAP,
    ".md": "markdown",
    ".pm": "perl",
    ".pl": "perl",
    ".t": "perl",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".sh": "shell",
}

PERL_DECLARATION_RE = re.compile(r"^\s*sub\s+([A-Za-z_][A-Za-z0-9_]*)")
PERL_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z0-9_:]+)\s*;")
PYTHON_DECLARATION_RE = re.compile(r"^\s*(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)")
PYTHON_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)")
C_DECLARATION_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_\s\*]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*$")
OPENING_BRACE_DECLARATION_RE = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_\s\*]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{"
)
IMPORT_PATTERNS = {
    "python": [
        re.compile(r"^\s*import\s+([A-Za-z0-9_.,\s]+)"),
        re.compile(r"^\s*from\s+([A-Za-z0-9_.]+)\s+import\s+([A-Za-z0-9_.*, ]+)"),
    ],
    "perl": [
        re.compile(r"^\s*use\s+([A-Za-z0-9_:]+)"),
        re.compile(r"^\s*require\s+([A-Za-z0-9_:]+)"),
        re.compile(r"^\s*use\s+base\s+['\"]([A-Za-z0-9_:]+)['\"]"),
    ],
    "c": [re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]')],
    "cpp": [re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]')],
    "javascript": [
        re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"^\s*const\s+.*?=\s+require\(['\"]([^'\"]+)['\"]\)"),
    ],
    "typescript": [
        re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"^\s*const\s+.*?=\s+require\(['\"]([^'\"]+)['\"]\)"),
    ],
}

TREE_SITTER_PROFILES = {
    "c": {"function_definition": ("method", "function")},
    "cpp": {"function_definition": ("method", "function"), "class_specifier": ("class", "class")},
    "c_sharp": {
        "class_declaration": ("class", "class"),
        "interface_declaration": ("class", "interface"),
        "method_declaration": ("method", "function"),
    },
    "go": {
        "function_declaration": ("method", "function"),
        "method_declaration": ("method", "function"),
        "type_declaration": ("class", "type"),
    },
    "java": {
        "class_declaration": ("class", "class"),
        "interface_declaration": ("class", "interface"),
        "method_declaration": ("method", "function"),
    },
    "javascript": {
        "function_declaration": ("method", "function"),
        "class_declaration": ("class", "class"),
        "method_definition": ("method", "function"),
    },
    "kotlin": {
        "class_declaration": ("class", "class"),
        "function_declaration": ("method", "function"),
    },
    "lua": {"function_declaration": ("method", "function")},
    "php": {
        "function_definition": ("method", "function"),
        "method_declaration": ("method", "function"),
        "class_declaration": ("class", "class"),
    },
    "python": {
        "function_definition": ("method", "function"),
        "class_definition": ("class", "class"),
    },
    "ruby": {
        "method": ("method", "function"),
        "class": ("class", "class"),
        "module": ("class", "module"),
    },
    "rust": {
        "function_item": ("method", "function"),
        "impl_item": ("class", "impl"),
        "struct_item": ("class", "struct"),
    },
    "scala": {
        "class_definition": ("class", "class"),
        "object_definition": ("class", "object"),
        "function_definition": ("method", "function"),
    },
    "swift": {
        "class_declaration": ("class", "class"),
        "function_declaration": ("method", "function"),
    },
    "typescript": {
        "function_declaration": ("method", "function"),
        "class_declaration": ("class", "class"),
        "method_definition": ("method", "function"),
    },
}


class CodeChunker:
    def __init__(self, target_chunk_lines: int = 80):
        """Initializes the chunker and prepares optional tree-sitter support."""
        self.target_chunk_lines = target_chunk_lines
        self._tree_sitter_get_parser = self._load_tree_sitter_parser()

    def chunk_text(self, file_path: Path, root_path: Path, content: str) -> list[CodeChunk]:
        """Splits one file into searchable file, class, and method chunks."""
        try:
            relative_path = file_path.relative_to(root_path).as_posix()
        except ValueError:
            relative_path = file_path.name

        language = self.detect_language(file_path)
        lines = content.splitlines()
        if not lines:
            return []

        imports = self._extract_imports(language, lines)
        records = self._extract_declaration_records(language, relative_path, content, lines)
        return self._build_chunks(relative_path, language, lines, imports, records)

    def detect_language(self, file_path: Path) -> str:
        """Infers the source language from the file extension."""
        return LANGUAGE_BY_SUFFIX.get(file_path.suffix.lower(), "text")

    def _load_tree_sitter_parser(self):
        """Loads the optional tree-sitter parser factory when the dependency is installed."""
        try:
            from tree_sitter_languages import get_parser
        except ImportError:
            return None

        return get_parser

    def _extract_declaration_records(
        self,
        language: str,
        relative_path: str,
        content: str,
        lines: list[str],
    ) -> list[dict]:
        """Extracts declaration records before they are materialized as final chunks."""
        records = self._chunk_with_tree_sitter(language, relative_path, content)
        if records:
            return records

        records = self._chunk_with_heuristics(language, relative_path, lines)
        if records:
            return records

        return []

    def _chunk_with_tree_sitter(self, language: str, relative_path: str, content: str) -> list[dict]:
        """Builds declaration records from AST nodes when tree-sitter supports the language."""
        if not self._tree_sitter_get_parser or language not in TREE_SITTER_PROFILES:
            return []

        try:
            parser = self._tree_sitter_get_parser(language)
            tree = parser.parse(content.encode("utf-8"))
        except Exception:
            return []

        records: list[dict] = []
        source_lines = content.splitlines()
        source_bytes = content.encode("utf-8")
        node_types = TREE_SITTER_PROFILES[language]

        def visit(node, parents: list[dict]):
            next_parents = parents
            if node.type in node_types:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                snippet = "\n".join(source_lines[start_line - 1 : end_line]).strip()
                if snippet:
                    chunk_type, declaration_type = node_types[node.type]
                    symbol = self._extract_symbol_from_node(node, source_bytes, language, snippet)
                    parent_class_symbol = self._parent_class_symbol(parents)
                    parent_symbol = parent_class_symbol or self._parent_symbol_from_lines(language, source_lines, start_line)
                    record = {
                        "chunk_type": chunk_type,
                        "declaration_type": declaration_type,
                        "path": relative_path,
                        "language": language,
                        "start_line": start_line,
                        "end_line": end_line,
                        "content": snippet,
                        "symbol": symbol,
                        "ast_node_type": node.type,
                        "parent_symbol": parent_symbol,
                        "parent_class_symbol": parent_class_symbol,
                    }
                    records.append(record)
                    next_parents = parents + [
                        {
                            "chunk_type": chunk_type,
                            "symbol": symbol or node.type,
                        }
                    ]

            for child in node.children:
                visit(child, next_parents)

        visit(tree.root_node, [])
        return records

    def _chunk_with_heuristics(self, language: str, relative_path: str, lines: list[str]) -> list[dict]:
        """Falls back to regex and indentation heuristics when AST parsing is unavailable."""
        if language == "python":
            return self._chunk_python_blocks(relative_path, lines)
        if language == "perl":
            return self._chunk_by_declarations(language, relative_path, lines)
        if language in {"c", "cpp", "java", "javascript", "go", "rust", "typescript"}:
            return self._chunk_by_c_like_blocks(language, relative_path, lines)

        return []

    def _chunk_python_blocks(self, relative_path: str, lines: list[str]) -> list[dict]:
        """Builds class and method records for Python using indentation-based parsing."""
        declarations: list[dict] = []

        for index, line in enumerate(lines):
            class_match = PYTHON_CLASS_RE.match(line)
            if class_match:
                declarations.append(
                    {
                        "index": index,
                        "indent": self._indent_level(line),
                        "chunk_type": "class",
                        "declaration_type": "class",
                        "symbol": class_match.group(1),
                    }
                )
                continue

            function_match = PYTHON_DECLARATION_RE.match(line)
            if function_match:
                parent_class_symbol = None
                current_indent = self._indent_level(line)
                for previous in reversed(declarations):
                    if previous["chunk_type"] == "class" and previous["indent"] < current_indent:
                        parent_class_symbol = previous["symbol"]
                        break

                declarations.append(
                    {
                        "index": index,
                        "indent": current_indent,
                        "chunk_type": "method",
                        "declaration_type": "function",
                        "symbol": function_match.group(1),
                        "parent_class_symbol": parent_class_symbol,
                    }
                )

        return self._materialize_indent_records("python", relative_path, lines, declarations)

    def _materialize_indent_records(
        self,
        language: str,
        relative_path: str,
        lines: list[str],
        declarations: list[dict],
    ) -> list[dict]:
        """Turns indentation-based declarations into chunk records with start and end lines."""
        records: list[dict] = []

        for position, declaration in enumerate(declarations):
            start_index = declaration["index"]
            current_indent = declaration["indent"]
            end_index = len(lines) - 1

            for cursor in range(start_index + 1, len(lines)):
                candidate = lines[cursor]
                if not candidate.strip():
                    continue
                if self._indent_level(candidate) <= current_indent:
                    end_index = cursor - 1
                    break

            if position + 1 < len(declarations):
                end_index = min(end_index, declarations[position + 1]["index"] - 1)

            while end_index > start_index and not lines[end_index].strip():
                end_index -= 1

            snippet = "\n".join(lines[start_index : end_index + 1]).strip()
            if not snippet:
                continue

            parent_class_symbol = declaration.get("parent_class_symbol")
            parent_symbol = parent_class_symbol or self._parent_symbol_from_lines(language, lines, start_index + 1)
            records.append(
                {
                    "chunk_type": declaration["chunk_type"],
                    "declaration_type": declaration["declaration_type"],
                    "path": relative_path,
                    "language": language,
                    "start_line": start_index + 1,
                    "end_line": end_index + 1,
                    "content": snippet,
                    "symbol": declaration["symbol"],
                    "ast_node_type": "class_definition" if declaration["chunk_type"] == "class" else "function_definition",
                    "parent_symbol": parent_symbol,
                    "parent_class_symbol": parent_class_symbol,
                }
            )

        return records

    def _chunk_by_declarations(self, language: str, relative_path: str, lines: list[str]) -> list[dict]:
        """Chunks declaration-oriented languages by named function boundaries."""
        declarations: list[tuple[int, str]] = []

        for index, line in enumerate(lines):
            match = PERL_DECLARATION_RE.match(line) if language == "perl" else PYTHON_DECLARATION_RE.match(line)
            if match:
                declarations.append((index, match.group(1)))

        records: list[dict] = []
        for position, (start_index, symbol) in enumerate(declarations):
            end_index = len(lines) - 1
            if position + 1 < len(declarations):
                end_index = declarations[position + 1][0] - 1

            while end_index > start_index and not lines[end_index].strip():
                end_index -= 1

            snippet = "\n".join(lines[start_index : end_index + 1]).strip()
            if not snippet:
                continue

            records.append(
                {
                    "chunk_type": "method",
                    "declaration_type": "function",
                    "path": relative_path,
                    "language": language,
                    "start_line": start_index + 1,
                    "end_line": end_index + 1,
                    "content": snippet,
                    "symbol": symbol,
                    "ast_node_type": "subroutine_declaration" if language == "perl" else "function_definition",
                    "parent_symbol": self._parent_symbol_from_lines(language, lines, start_index + 1),
                    "parent_class_symbol": None,
                }
            )

        return records

    def _chunk_by_c_like_blocks(self, language: str, relative_path: str, lines: list[str]) -> list[dict]:
        """Chunks brace-based languages by scanning for function signatures and balanced blocks."""
        records: list[dict] = []
        index = 0

        while index < len(lines):
            symbol = self._match_c_like_symbol(lines, index)
            if not symbol:
                index += 1
                continue

            start_index = index
            opening_index = index
            if "{" not in lines[index]:
                opening_index += 1
                if opening_index >= len(lines) or "{" not in lines[opening_index]:
                    index += 1
                    continue

            brace_depth = 0
            seen_opening = False
            end_index = opening_index
            for cursor in range(opening_index, len(lines)):
                brace_depth += lines[cursor].count("{")
                if lines[cursor].count("{"):
                    seen_opening = True
                brace_depth -= lines[cursor].count("}")
                if seen_opening and brace_depth <= 0:
                    end_index = cursor
                    break

            snippet = "\n".join(lines[start_index : end_index + 1]).strip()
            if snippet:
                records.append(
                    {
                        "chunk_type": "method",
                        "declaration_type": "function",
                        "path": relative_path,
                        "language": language,
                        "start_line": start_index + 1,
                        "end_line": end_index + 1,
                        "content": snippet,
                        "symbol": symbol,
                        "ast_node_type": "function_definition",
                        "parent_symbol": self._parent_symbol_from_lines(language, lines, start_index + 1),
                        "parent_class_symbol": None,
                    }
                )

            index = max(end_index + 1, index + 1)

        return records

    def _build_chunks(
        self,
        relative_path: str,
        language: str,
        lines: list[str],
        imports: list[str],
        records: list[dict],
    ) -> list[CodeChunk]:
        """Materializes final file, class, and method chunks with hierarchy and context."""
        chunks: list[CodeChunk] = []
        top_level_symbols = self._top_level_symbols(records)
        file_chunk = self._build_file_chunk(relative_path, language, lines, imports, top_level_symbols, records)
        chunks.append(file_chunk)

        class_chunks_by_symbol: dict[str, CodeChunk] = {}
        for record in records:
            if record["chunk_type"] != "class":
                continue

            chunk = self._build_declaration_chunk(
                record=record,
                language=language,
                relative_path=relative_path,
                imports=imports,
                parent_chunk_id=file_chunk.chunk_id,
                top_level_symbols=[],
            )
            chunks.append(chunk)
            if chunk.symbol:
                class_chunks_by_symbol[chunk.symbol] = chunk

        for record in records:
            if record["chunk_type"] != "method":
                continue

            parent_chunk = class_chunks_by_symbol.get(record.get("parent_class_symbol") or "")
            parent_chunk_id = parent_chunk.chunk_id if parent_chunk else file_chunk.chunk_id
            chunk = self._build_declaration_chunk(
                record=record,
                language=language,
                relative_path=relative_path,
                imports=imports,
                parent_chunk_id=parent_chunk_id,
                top_level_symbols=[],
            )
            chunks.append(chunk)

        return chunks

    def _build_file_chunk(
        self,
        relative_path: str,
        language: str,
        lines: list[str],
        imports: list[str],
        top_level_symbols: list[str],
        records: list[dict],
    ) -> CodeChunk:
        """Builds a file-level summary chunk without embedding the whole file body."""
        chunk_id = self._make_chunk_id(relative_path, "file", relative_path, 1, len(lines))
        summary_lines = [
            f"Path: {relative_path}",
            f"Language: {language}",
            "Chunk Type: file",
            f"Imports: {', '.join(imports) if imports else 'None'}",
            f"Top-level Symbols: {', '.join(top_level_symbols) if top_level_symbols else 'None'}",
        ]
        content = "\n".join(summary_lines)
        references = self._extract_references("\n".join([content] + [record["symbol"] or "" for record in records]))
        contextualized_text = "\n".join(summary_lines + [f"References: {', '.join(references) if references else 'None'}"])
        return CodeChunk(
            chunk_id=chunk_id,
            parent_chunk_id=None,
            chunk_type="file",
            path=relative_path,
            language=language,
            start_line=1,
            end_line=len(lines),
            content=content,
            contextualized_text=contextualized_text,
            symbol=None,
            ast_node_type="file",
            declaration_type="file",
            parent_symbol=None,
            keywords=self._build_keywords(relative_path, language, None, None, content),
            imports=imports,
            references=references,
            top_level_symbols=top_level_symbols,
            code_unit=None,
        )

    def _build_declaration_chunk(
        self,
        record: dict,
        language: str,
        relative_path: str,
        imports: list[str],
        parent_chunk_id: str,
        top_level_symbols: list[str],
    ) -> CodeChunk:
        """Builds a class or method chunk from an extracted declaration record."""
        symbol = record["symbol"]
        content = record["content"]
        chunk_type = record["chunk_type"]
        references = self._extract_references(content)
        chunk_id = self._make_chunk_id(relative_path, chunk_type, symbol or "anonymous", record["start_line"], record["end_line"])
        contextualized_text = self._build_contextualized_text(
            path=relative_path,
            language=language,
            chunk_type=chunk_type,
            symbol=symbol,
            parent_symbol=record.get("parent_symbol"),
            imports=imports,
            references=references,
            top_level_symbols=top_level_symbols,
            code_unit=content,
        )
        return CodeChunk(
            chunk_id=chunk_id,
            parent_chunk_id=parent_chunk_id,
            chunk_type=chunk_type,
            path=relative_path,
            language=language,
            start_line=record["start_line"],
            end_line=record["end_line"],
            content=content,
            contextualized_text=contextualized_text,
            symbol=symbol,
            ast_node_type=record.get("ast_node_type"),
            declaration_type=record.get("declaration_type"),
            parent_symbol=record.get("parent_symbol"),
            keywords=self._build_keywords(relative_path, language, symbol, record.get("parent_symbol"), content),
            imports=imports,
            references=references,
            top_level_symbols=top_level_symbols,
            code_unit=content,
        )

    def _extract_symbol_from_node(self, node, source_bytes: bytes, language: str, snippet: str) -> str | None:
        """Extracts a declaration symbol from an AST node, with snippet parsing as fallback."""
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="ignore").strip()
            if name:
                return name

        return self._extract_symbol_from_snippet(language, snippet)

    def _extract_symbol_from_snippet(self, language: str, snippet: str) -> str | None:
        """Pulls the declared symbol name from the first line of a chunk snippet."""
        first_line = snippet.splitlines()[0]

        if language == "perl":
            match = PERL_DECLARATION_RE.match(first_line)
            return match.group(1) if match else None

        if language == "python":
            match = PYTHON_DECLARATION_RE.match(first_line) or PYTHON_CLASS_RE.match(first_line)
            return match.group(1) if match else None

        match = OPENING_BRACE_DECLARATION_RE.match(first_line) or C_DECLARATION_RE.match(first_line)
        return match.group(1) if match else None

    def _extract_imports(self, language: str, lines: list[str]) -> list[str]:
        """Collects import-like statements from a file using lightweight language-specific patterns."""
        imports: list[str] = []
        patterns = IMPORT_PATTERNS.get(language, [])

        for line in lines:
            for pattern in patterns:
                match = pattern.match(line)
                if not match:
                    continue

                if language == "python" and len(match.groups()) == 2:
                    module_name = match.group(1).strip()
                    imported_names = [
                        f"{module_name}.{name.strip()}"
                        for name in match.group(2).split(",")
                        if name.strip() and name.strip() != "*"
                    ]
                    imports.extend(imported_names or [module_name])
                elif language == "python":
                    imports.extend(part.strip() for part in match.group(1).split(",") if part.strip())
                else:
                    imports.append(match.group(1).strip())

        return self._unique_limited(imports, limit=30)

    def _extract_references(self, text: str, limit: int = 50) -> list[str]:
        """Collects the first unique identifier-like tokens as lightweight reference metadata."""
        return self._unique_limited(tokenize(text), limit=limit)

    def _top_level_symbols(self, records: list[dict]) -> list[str]:
        """Collects top-level symbol names for the file summary chunk."""
        symbols: list[str] = []
        for record in records:
            if not record.get("symbol"):
                continue
            if record["chunk_type"] == "method" and record.get("parent_class_symbol"):
                continue
            symbols.append(record["symbol"])

        return self._unique_limited(symbols, limit=50)

    def _build_contextualized_text(
        self,
        path: str,
        language: str,
        chunk_type: str,
        symbol: str | None,
        parent_symbol: str | None,
        imports: list[str],
        references: list[str],
        top_level_symbols: list[str],
        code_unit: str | None,
    ) -> str:
        """Builds the exact text that will be indexed and embedded for one chunk."""
        lines = [
            f"Path: {path}",
            f"Language: {language}",
            f"Chunk Type: {chunk_type}",
            f"Symbol: {symbol or 'None'}",
            f"Parent: {parent_symbol or 'None'}",
            f"Imports: {', '.join(imports) if imports else 'None'}",
            f"Top-level Symbols: {', '.join(top_level_symbols) if top_level_symbols else 'None'}",
            f"References: {', '.join(references) if references else 'None'}",
        ]

        if code_unit:
            lines.append("Code Unit:")
            lines.append(code_unit)

        return "\n".join(lines)

    def _make_chunk_id(self, path: str, chunk_type: str, symbol: str, start_line: int, end_line: int) -> str:
        """Builds a deterministic identifier for one chunk."""
        digest = hashlib.md5(f"{path}:{chunk_type}:{symbol}:{start_line}:{end_line}".encode("utf-8")).hexdigest()[:12]
        return f"{chunk_type}-{digest}"

    def _indent_level(self, line: str) -> int:
        """Measures indentation depth for indentation-sensitive heuristic parsing."""
        return len(line) - len(line.lstrip(" "))

    def _match_c_like_symbol(self, lines: list[str], index: int) -> str | None:
        """Extracts a probable function symbol from a C-like declaration line."""
        line = lines[index]
        opening_match = OPENING_BRACE_DECLARATION_RE.match(line)
        if opening_match:
            return opening_match.group(1)

        declaration_match = C_DECLARATION_RE.match(line)
        if not declaration_match:
            return None

        next_index = index + 1
        if next_index < len(lines) and lines[next_index].strip() == "{":
            return declaration_match.group(1)

        return None

    def _parent_class_symbol(self, parents: list[dict]) -> str | None:
        """Finds the nearest enclosing class-like symbol in the current traversal stack."""
        for parent in reversed(parents):
            if parent["chunk_type"] == "class":
                return parent["symbol"]

        return None

    def _parent_symbol_from_lines(self, language: str, lines: list[str], line_number: int) -> str | None:
        """Looks upward in the file to find a containing module or package name."""
        if language == "perl":
            for index in range(line_number - 2, -1, -1):
                match = PERL_PACKAGE_RE.match(lines[index])
                if match:
                    return match.group(1)

        return None

    def _build_keywords(
        self,
        relative_path: str,
        language: str,
        symbol: str | None,
        parent_symbol: str | None,
        snippet: str,
    ) -> list[str]:
        """Builds a small deduplicated keyword set for lexical and semantic search hints."""
        candidates = tokenize("\n".join(filter(None, [relative_path, language, parent_symbol or "", symbol or "", snippet])))
        return self._unique_limited([token for token in candidates if len(token) >= 3], limit=12)

    def _unique_limited(self, values: list[str], limit: int) -> list[str]:
        """Deduplicates values while preserving order and enforcing an upper bound."""
        result: list[str] = []
        for value in values:
            if not value or value in result:
                continue
            result.append(value)
            if len(result) >= limit:
                break

        return result
