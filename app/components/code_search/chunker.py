import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from app.components.code_search.embeddings import tokenize
from app.components.code_search.models import CodeChunk

logger = logging.getLogger(__name__)


# Tree-sitter language names used by tree-sitter-language-pack / tree-sitter-languages.
# Keep common extensions explicit; unknown extensions still fall back to text/window chunking.
TREE_SITTER_LANGUAGE_MAP = {
    ".ada": "ada",
    ".adb": "ada",
    ".ads": "ada",
    ".agda": "agda",
    ".bash": "bash",
    ".bat": "batch",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".csv": "csv",
    ".dart": "dart",
    ".dockerfile": "dockerfile",
    ".ex": "elixir",
    ".exs": "elixir",
    ".elm": "elm",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".fs": "fsharp",
    ".fsi": "fsharp",
    ".fsx": "fsharp",
    ".go": "go",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".hs": "haskell",
    ".html": "html",
    ".htm": "html",
    ".java": "java",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".jsonc": "json",
    ".jl": "julia",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".lua": "lua",
    ".md": "markdown",
    ".markdown": "markdown",
    ".nim": "nim",
    ".nims": "nim",
    ".php": "php",
    ".pl": "perl",
    ".pm": "perl",
    ".t": "perl",
    ".ps1": "powershell",
    ".py": "python",
    ".pyi": "python",
    ".r": "r",
    ".R": "r",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sc": "scala",
    ".sh": "bash",
    ".sql": "sql",
    ".svelte": "svelte",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zig": "zig",
}

LANGUAGE_BY_SUFFIX = {**TREE_SITTER_LANGUAGE_MAP, ".txt": "text"}


PERL_DECLARATION_RE = re.compile(r"^\s*sub\s+([A-Za-z_][A-Za-z0-9_]*)")
PERL_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z0-9_:]+)\s*;")
PYTHON_DECLARATION_RE = re.compile(
    r"^\s*(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
PYTHON_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)")
C_DECLARATION_RE = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_:\<\>\[\],\s\*&~]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*$"
)
OPENING_BRACE_DECLARATION_RE = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_:\<\>\[\],\s\*&~]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{"
)
JS_ASSIGNMENT_RE = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=")
JS_OBJECT_ASSIGNMENT_RE = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\{"
)
OBJECT_NAME_RE = re.compile(r"\bname\s*:\s*['\"]([^'\"]+)['\"]")


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
    "go": [re.compile(r'^\s*import\s+(?:\(|"([^"]+)")')],
    "java": [re.compile(r"^\s*import\s+([A-Za-z0-9_.*]+)\s*;")],
    "javascript": [
        re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"^\s*const\s+.*?=\s+require\(['\"]([^'\"]+)['\"]\)"),
    ],
    "typescript": [
        re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"^\s*const\s+.*?=\s+require\(['\"]([^'\"]+)['\"]\)"),
    ],
    "rust": [re.compile(r"^\s*use\s+([A-Za-z0-9_:{}*,\s]+)\s*;")],
}


# Preferred declaration nodes for languages covered by tests and common repositories.
# The generic matcher below is only a fallback for grammars without a profile.
TREE_SITTER_PROFILES = {
    "c": {
        "class": {"enum_specifier", "struct_specifier", "union_specifier"},
        "method": {"function_definition"},
    },
    "cpp": {
        "class": {
            "class_specifier",
            "enum_specifier",
            "namespace_definition",
            "struct_specifier",
            "union_specifier",
        },
        "method": {"function_definition"},
    },
    "go": {
        "class": {"type_declaration"},
        "method": {"function_declaration", "method_declaration"},
    },
    "javascript": {
        "class": {"class_declaration"},
        "method": {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
        },
    },
    "php": {
        "class": {"class_declaration", "interface_declaration", "trait_declaration"},
        "method": {"function_definition", "method_declaration"},
    },
    "python": {
        "class": {"class_definition"},
        "method": {"function_definition"},
    },
    "ruby": {
        "class": {"class", "module"},
        "method": {"method", "singleton_method"},
    },
    "typescript": {
        "class": {
            "abstract_class_declaration",
            "class_declaration",
            "enum_declaration",
            "interface_declaration",
            "type_alias_declaration",
        },
        "method": {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
        },
    },
}


# Generic declaration markers. This is intentionally broader than per-language profiles:
# tree-sitter node names are not fully stable across grammars, but these suffixes cover most languages.
CLASS_NODE_EXACT = {
    "interface_declaration",
    "class_declaration",
    "class_definition",
    "class_specifier",
    "struct_item",
    "struct_declaration",
    "struct_specifier",
    "enum_declaration",
    "enum_item",
    "enum_specifier",
    "trait_item",
    "impl_item",
    "object_definition",
    "type_declaration",
    "type_definition",
    "namespace_definition",
    "namespace_declaration",
}

METHOD_NODE_EXACT = {
    "method",
    "function_item",
    "function_definition",
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
    "method_declaration",
    "constructor_declaration",
    "subroutine_declaration",
}

DECLARATION_SUFFIXES = (
    "_function",
    "_method",
    "_procedure",
    "_subroutine",
    "_constructor",
    "_class",
    "_struct",
    "_interface",
    "_trait",
    "_enum",
    "_module",
    "_namespace",
)

DECLARATION_CONTAINS = (
    "function",
    "method",
    "procedure",
    "subroutine",
    "constructor",
    "class",
    "struct",
    "interface",
    "trait",
    "enum",
    "module",
    "namespace",
    "impl",
)

WRAPPER_NODE_TYPES = {
    "export_statement",
    "decorated_definition",
    "public_field_definition",
    "lexical_declaration",
    "variable_declaration",
    "assignment_statement",
}


class CodeChunker:
    def __init__(self, target_chunk_lines: int = 80):
        """Initializes the chunker and prepares optional tree-sitter support."""
        if target_chunk_lines <= 0:
            raise ValueError("target_chunk_lines must be positive")
        self.target_chunk_lines = target_chunk_lines
        self._tree_sitter_get_parser = self._load_tree_sitter_parser()
        self._parser_cache: dict[str, Any] = {}

    def chunk_text(
        self, file_path: Path, root_path: Path, content: str
    ) -> list[CodeChunk]:
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
        records = self._extract_declaration_records(
            language, relative_path, content, lines
        )
        return self._build_chunks(relative_path, language, lines, imports, records)

    def detect_language(self, file_path: Path) -> str:
        """Infers the source language from the file extension."""
        suffix = file_path.suffix
        return LANGUAGE_BY_SUFFIX.get(
            suffix, LANGUAGE_BY_SUFFIX.get(suffix.lower(), "text")
        )

    def _load_tree_sitter_parser(self):
        """Loads the optional tree-sitter parser factory when the dependency is installed."""
        try:
            from tree_sitter_language_pack import get_parser

            logger.info("Code chunker tree-sitter backend: tree_sitter_language_pack")
            return get_parser
        except ImportError:
            try:
                from tree_sitter_languages import get_parser

                logger.info("Code chunker tree-sitter backend: tree_sitter_languages")
                return get_parser
            except ImportError:
                logger.warning(
                    "Tree-sitter parser backend is not installed; chunker will use fallbacks"
                )
                return None

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
            logger.info(
                "Code chunker selected strategy: path=%s language=%s strategy=tree_sitter declarations=%s",
                relative_path,
                language,
                len(records),
            )
            return records

        records = self._chunk_with_heuristics(language, relative_path, lines)
        if records:
            logger.info(
                "Code chunker selected strategy: path=%s language=%s strategy=heuristic_fallback declarations=%s",
                relative_path,
                language,
                len(records),
            )
            return records

        logger.info(
            "Code chunker selected strategy: path=%s language=%s strategy=file_window_fallback declarations=0",
            relative_path,
            language,
        )
        return []

    def _chunk_with_tree_sitter(
        self, language: str, relative_path: str, content: str
    ) -> list[dict]:
        """Builds declaration records from tree-sitter AST nodes for any supported grammar."""
        if not self._tree_sitter_get_parser:
            return []
        if language in {"text", "csv", "perl"}:
            return []

        try:
            parser = self._get_tree_sitter_parser(language)
            if parser is None:
                return []

            source_bytes = content.encode("utf-8")
            tree = parser.parse(source_bytes)
        except Exception as exc:
            logger.warning(
                "Tree-sitter parse failed: path=%s language=%s error=%s",
                relative_path,
                language,
                exc,
            )
            return []

        if getattr(tree.root_node, "has_error", False):
            logger.warning(
                "Tree-sitter parse has syntax errors: path=%s language=%s",
                relative_path,
                language,
            )
            # Still try to extract records. Many grammars recover enough useful nodes.

        source_lines = content.splitlines()
        records: list[dict] = []

        def visit(node, parents: list[dict]):
            declaration = self._classify_tree_sitter_node(node, source_bytes, language)
            next_parents = parents

            if declaration:
                record = self._record_from_node(
                    node=node,
                    symbol_node=declaration.get("symbol_node") or node,
                    source_lines=source_lines,
                    source_bytes=source_bytes,
                    language=language,
                    relative_path=relative_path,
                    chunk_type=declaration["chunk_type"],
                    declaration_type=declaration["declaration_type"],
                    parents=parents,
                )
                if record:
                    records.append(record)
                    next_parents = parents + [
                        {
                            "chunk_type": record["chunk_type"],
                            "symbol": record.get("symbol") or node.type,
                        }
                    ]

            for child in getattr(node, "children", []):
                visit(child, next_parents)

        visit(tree.root_node, [])
        return self._dedupe_and_filter_ast_records(records)

    def _get_tree_sitter_parser(self, language: str):
        """Returns a cached parser for the requested language."""
        if language in self._parser_cache:
            return self._parser_cache[language]

        try:
            parser = self._tree_sitter_get_parser(language)
        except Exception as exc:
            logger.info(
                "Tree-sitter language unsupported: language=%s error=%s", language, exc
            )
            self._parser_cache[language] = None
            return None

        self._parser_cache[language] = parser
        return parser

    def _classify_tree_sitter_node(
        self, node, source_bytes: bytes, language: str
    ) -> dict | None:
        """Classifies a tree-sitter node as class/method when it looks like a code unit."""
        if not getattr(node, "is_named", True):
            return None

        node_type = node.type
        profile = TREE_SITTER_PROFILES.get(language)

        if profile:
            if node_type in profile["class"]:
                if not self._is_real_class_like_declaration(node, language):
                    return None
                return {
                    "chunk_type": self._chunk_type_from_node_type(node_type),
                    "declaration_type": self._declaration_type_from_node_type(
                        node_type
                    ),
                }

            if node_type in profile["method"]:
                return {
                    "chunk_type": "method",
                    "declaration_type": self._declaration_type_from_node_type(
                        node_type
                    ),
                }

            if node_type in WRAPPER_NODE_TYPES:
                child_decl = self._find_child_declaration(
                    node, source_bytes, language
                )
                if child_decl:
                    return child_decl

            return None

        if node_type in CLASS_NODE_EXACT or self._node_type_matches(
            node_type, kind="class"
        ):
            if not self._is_real_class_like_declaration(node, language):
                return None
            return {
                "chunk_type": self._chunk_type_from_node_type(node_type),
                "declaration_type": self._declaration_type_from_node_type(node_type),
            }

        if node_type in METHOD_NODE_EXACT or self._node_type_matches(
            node_type, kind="method"
        ):
            return {
                "chunk_type": "method",
                "declaration_type": self._declaration_type_from_node_type(node_type),
            }

        # Common JS/TS/Ruby/PHP pattern: const foo = () => {}, exports.foo = function() {}, etc.
        if node_type in WRAPPER_NODE_TYPES:
            child_decl = self._find_child_declaration(node, source_bytes, language)
            if child_decl:
                return child_decl

        return None

    def _node_type_matches(self, node_type: str, kind: str) -> bool:
        """Broad grammar-independent node type matching."""
        if not (
            node_type.endswith(("_declaration", "_definition", "_item", "_specifier"))
            or node_type.endswith(DECLARATION_SUFFIXES)
        ):
            return False

        if kind == "class":
            return any(
                word in node_type
                for word in (
                    "class",
                    "struct",
                    "interface",
                    "trait",
                    "enum",
                    "module",
                    "namespace",
                    "impl",
                    "object",
                    "type",
                )
            )
        if kind == "method":
            return any(
                word in node_type
                for word in (
                    "function",
                    "method",
                    "procedure",
                    "subroutine",
                    "constructor",
                )
            )
        return any(word in node_type for word in DECLARATION_CONTAINS)

    def _find_child_declaration(
        self, node, source_bytes: bytes, language: str
    ) -> dict | None:
        """Detects declaration-like values inside wrapper/assignment nodes."""
        snippet = source_bytes[node.start_byte : node.end_byte].decode(
            "utf-8", errors="ignore"
        )

        profile = TREE_SITTER_PROFILES.get(language)
        for child in getattr(node, "children", []):
            if not getattr(child, "is_named", True):
                continue

            child_type = child.type
            if profile:
                is_method = child_type in profile["method"]
            else:
                is_method = child_type in METHOD_NODE_EXACT or self._node_type_matches(
                    child_type, "method"
                )
            if is_method:
                return {
                    "chunk_type": "method",
                    "declaration_type": self._declaration_type_from_node_type(
                        child_type
                    ),
                    "symbol_node": node,
                }

            if profile:
                is_class = child_type in profile["class"]
            else:
                is_class = child_type in CLASS_NODE_EXACT or self._node_type_matches(
                    child_type, "class"
                )
            if is_class:
                if not self._is_real_class_like_declaration(child, language):
                    continue
                return {
                    "chunk_type": self._chunk_type_from_node_type(child_type),
                    "declaration_type": self._declaration_type_from_node_type(
                        child_type
                    ),
                    "symbol_node": node,
                }

        if language in {"javascript", "typescript"} and (
            JS_OBJECT_ASSIGNMENT_RE.search(snippet)
            or OBJECT_NAME_RE.search(snippet)
        ):
            return {
                "chunk_type": "object",
                "declaration_type": "object",
                "symbol_node": node,
            }

        if language in {"javascript", "typescript"} and (
            "=>" in snippet or "function" in snippet
        ):
            return {
                "chunk_type": "method",
                "declaration_type": "function",
                "symbol_node": node,
            }

        return None

    def _chunk_type_from_node_type(self, node_type: str) -> str:
        """Maps declaration node types to searchable chunk categories."""
        if "object" in node_type or "type_alias" in node_type:
            return "object"

        return "class"

    def _is_real_class_like_declaration(self, node, language: str) -> bool:
        """Rejects type references that grammars expose as struct/enum specifier nodes."""
        node_type = node.type

        if language in {"c", "cpp"} and node_type in {
            "enum_specifier",
            "struct_specifier",
            "union_specifier",
        }:
            return self._has_descendant_type(
                node,
                {
                    "declaration_list",
                    "enumerator_list",
                    "field_declaration_list",
                },
            )

        return True

    def _has_descendant_type(self, node, node_types: set[str]) -> bool:
        """Checks whether a tree-sitter node contains a descendant with one of the types."""
        for child in getattr(node, "children", []):
            if getattr(child, "is_named", True) and child.type in node_types:
                return True
            if self._has_descendant_type(child, node_types):
                return True

        return False

    def _declaration_type_from_node_type(self, node_type: str) -> str:
        """Normalizes grammar-specific node types into compact declaration labels."""
        if any(word in node_type for word in ("class", "object")):
            return "class"
        if "interface" in node_type:
            return "interface"
        if "struct" in node_type:
            return "struct"
        if "enum" in node_type:
            return "enum"
        if "trait" in node_type:
            return "trait"
        if "impl" in node_type:
            return "impl"
        if "module" in node_type:
            return "module"
        if "namespace" in node_type:
            return "namespace"
        if "constructor" in node_type:
            return "constructor"
        if any(
            word in node_type
            for word in ("function", "method", "procedure", "subroutine", "lambda")
        ):
            return "function"
        if "type" in node_type:
            return "type"
        return node_type

    def _record_from_node(
        self,
        node,
        symbol_node,
        source_lines: list[str],
        source_bytes: bytes,
        language: str,
        relative_path: str,
        chunk_type: str,
        declaration_type: str,
        parents: list[dict] | None = None,
    ) -> dict | None:
        """Materializes a chunk record from a tree-sitter node."""
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        snippet = "\n".join(source_lines[start_line - 1 : end_line]).strip()
        if not snippet:
            return None

        symbol = self._extract_symbol_from_node(
            symbol_node, source_bytes, language, snippet
        )
        parent_class_symbol = self._parent_class_symbol(parents or [])
        parent_symbol = (
            parent_class_symbol
            or self._parent_symbol_from_lines(language, source_lines, start_line)
            or relative_path
        )

        return {
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
            "chunk_strategy": "tree_sitter",
        }

    def _dedupe_and_filter_ast_records(self, records: list[dict]) -> list[dict]:
        """Removes duplicate AST records caused by wrapper nodes and nested expression nodes."""
        if not records:
            return []

        unique: dict[tuple[int, int, str, str], dict] = {}
        for record in records:
            key = (
                record["start_line"],
                record["end_line"],
                record.get("chunk_type") or "",
                record.get("symbol") or "",
            )
            current = unique.get(key)
            if current is None:
                unique[key] = record
                continue

            # Prefer named symbols over anonymous records.
            if not current.get("symbol") and record.get("symbol"):
                unique[key] = record

        deduped = sorted(
            unique.values(),
            key=lambda item: (
                item["start_line"],
                item["end_line"],
                item.get("chunk_type") or "",
            ),
        )

        filtered: list[dict] = []
        seen_ranges: set[tuple[int, int, str]] = set()
        for record in deduped:
            range_key = (record["start_line"], record["end_line"], record["chunk_type"])
            if range_key in seen_ranges:
                continue
            seen_ranges.add(range_key)
            filtered.append(record)

        return filtered

    def _chunk_with_heuristics(
        self, language: str, relative_path: str, lines: list[str]
    ) -> list[dict]:
        """Falls back to regex and indentation heuristics only when AST extraction failed."""
        if language == "python":
            return self._chunk_python_blocks(relative_path, lines)
        if language == "perl":
            return self._chunk_by_declarations(language, relative_path, lines)
        if language in {
            "c",
            "cpp",
            "java",
            "javascript",
            "typescript",
            "go",
            "rust",
            "csharp",
            "php",
            "swift",
            "kotlin",
        }:
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
                        "chunk_strategy": "heuristic",
                    }
                )
                continue

            function_match = PYTHON_DECLARATION_RE.match(line)
            if function_match:
                parent_class_symbol = None
                current_indent = self._indent_level(line)
                for previous in reversed(declarations):
                    if (
                        previous["chunk_type"] == "class"
                        and previous["indent"] < current_indent
                    ):
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
                        "chunk_strategy": "heuristic",
                    }
                )

        return self._materialize_indent_records(
            "python", relative_path, lines, declarations
        )

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
                next_decl = declarations[position + 1]
                if next_decl["indent"] <= current_indent:
                    end_index = min(end_index, next_decl["index"] - 1)

            while end_index > start_index and not lines[end_index].strip():
                end_index -= 1

            snippet = "\n".join(lines[start_index : end_index + 1]).strip()
            if not snippet:
                continue

            parent_class_symbol = declaration.get("parent_class_symbol")
            parent_symbol = (
                parent_class_symbol
                or self._parent_symbol_from_lines(language, lines, start_index + 1)
                or relative_path
            )
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
                    "ast_node_type": (
                        "class_definition"
                        if declaration["chunk_type"] == "class"
                        else "function_definition"
                    ),
                    "parent_symbol": parent_symbol,
                    "parent_class_symbol": parent_class_symbol,
                    "chunk_strategy": declaration.get("chunk_strategy", "heuristic"),
                }
            )

        return records

    def _chunk_by_declarations(
        self, language: str, relative_path: str, lines: list[str]
    ) -> list[dict]:
        """Chunks declaration-oriented languages by named function boundaries."""
        declarations: list[tuple[int, str]] = []

        for index, line in enumerate(lines):
            match = (
                PERL_DECLARATION_RE.match(line)
                if language == "perl"
                else PYTHON_DECLARATION_RE.match(line)
            )
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
                    "ast_node_type": (
                        "subroutine_declaration"
                        if language == "perl"
                        else "function_definition"
                    ),
                    "parent_symbol": self._parent_symbol_from_lines(
                        language, lines, start_index + 1
                    )
                    or relative_path,
                    "parent_class_symbol": None,
                    "chunk_strategy": "heuristic",
                }
            )

        return records

    def _chunk_by_c_like_blocks(
        self, language: str, relative_path: str, lines: list[str]
    ) -> list[dict]:
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
            while opening_index < len(lines) and "{" not in lines[opening_index]:
                opening_index += 1
            if opening_index >= len(lines):
                index += 1
                continue

            brace_depth = 0
            seen_opening = False
            end_index = opening_index
            for cursor in range(opening_index, len(lines)):
                line = self._strip_string_literals(lines[cursor])
                opens = line.count("{")
                closes = line.count("}")
                brace_depth += opens
                if opens:
                    seen_opening = True
                brace_depth -= closes
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
                        "parent_symbol": self._parent_symbol_from_lines(
                            language, lines, start_index + 1
                        )
                        or relative_path,
                        "parent_class_symbol": None,
                        "chunk_strategy": "heuristic",
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
        file_chunk = self._build_file_chunk(
            relative_path, language, lines, imports, top_level_symbols, records
        )
        chunks.append(file_chunk)

        class_chunks_by_symbol: dict[str, CodeChunk] = {}
        for record in records:
            if record["chunk_type"] not in {"class", "object"}:
                continue

            chunk = self._build_declaration_chunk(
                record=record,
                language=language,
                relative_path=relative_path,
                imports=imports,
                parent_chunk_id=file_chunk.chunk_id,
                top_level_symbols=top_level_symbols,
            )
            chunks.append(chunk)
            if chunk.chunk_type == "class" and chunk.symbol:
                class_chunks_by_symbol[chunk.symbol] = chunk

        for record in records:
            if record["chunk_type"] != "method":
                continue

            parent_chunk = class_chunks_by_symbol.get(
                record.get("parent_class_symbol") or ""
            )
            parent_chunk_id = (
                parent_chunk.chunk_id if parent_chunk else file_chunk.chunk_id
            )
            chunk = self._build_declaration_chunk(
                record=record,
                language=language,
                relative_path=relative_path,
                imports=imports,
                parent_chunk_id=parent_chunk_id,
                top_level_symbols=top_level_symbols,
            )
            chunks.append(chunk)

        # If AST/heuristic found declarations, also preserve meaningful top-level code not covered by declarations.
        if records:
            chunks.extend(
                self._build_uncovered_file_window_chunks(
                    relative_path,
                    language,
                    lines,
                    imports,
                    records,
                    file_chunk.chunk_id,
                )
            )
        else:
            chunks.extend(
                self._build_file_window_chunks(
                    relative_path, language, lines, imports, file_chunk.chunk_id
                )
            )

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
        chunk_id = self._make_chunk_id(
            relative_path, "file", relative_path, 1, len(lines)
        )
        strategies = self._unique_limited(
            [record.get("chunk_strategy", "unknown") for record in records], limit=5
        )
        summary_lines = [
            f"Path: {relative_path}",
            f"Language: {language}",
            "Chunk Type: file",
            f"Chunk Strategy: {'+'.join(strategies) if strategies else 'file_summary'}",
            f"Imports: {', '.join(imports) if imports else 'None'}",
            f"Top-level Symbols: {', '.join(top_level_symbols) if top_level_symbols else 'None'}",
        ]
        content = "\n".join(summary_lines)
        references = self._extract_references(
            "\n".join([content] + [record.get("symbol") or "" for record in records])
        )
        contextualized_text = "\n".join(
            summary_lines
            + [f"References: {', '.join(references) if references else 'None'}"]
        )
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
        symbol = record.get("symbol")
        content = record["content"]
        chunk_type = record["chunk_type"]
        references = self._extract_references(content)
        chunk_id = self._make_chunk_id(
            relative_path,
            chunk_type,
            symbol or "anonymous",
            record["start_line"],
            record["end_line"],
        )
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
            chunk_strategy=record.get("chunk_strategy", "unknown"),
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
            keywords=self._build_keywords(
                relative_path, language, symbol, record.get("parent_symbol"), content
            ),
            imports=imports,
            references=references,
            top_level_symbols=top_level_symbols,
            code_unit=content,
        )

    def _build_file_window_chunks(
        self,
        relative_path: str,
        language: str,
        lines: list[str],
        imports: list[str],
        parent_chunk_id: str,
    ) -> list[CodeChunk]:
        """Builds real-code windows for procedural/config files with no declarations."""
        return self._build_windows_from_ranges(
            relative_path=relative_path,
            language=language,
            lines=lines,
            imports=imports,
            parent_chunk_id=parent_chunk_id,
            ranges=[(0, len(lines))],
            chunk_strategy="file_window",
        )

    def _build_uncovered_file_window_chunks(
        self,
        relative_path: str,
        language: str,
        lines: list[str],
        imports: list[str],
        records: list[dict],
        parent_chunk_id: str,
    ) -> list[CodeChunk]:
        """Preserves top-level code not covered by AST declarations, e.g. constants, routes, config."""
        covered = [False] * len(lines)
        for record in records:
            for index in range(
                max(0, record["start_line"] - 1), min(len(lines), record["end_line"])
            ):
                covered[index] = True

        ranges: list[tuple[int, int]] = []
        start: int | None = None
        in_block_comment = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            ignore_line = False

            if in_block_comment:
                ignore_line = True
                if "*/" in stripped:
                    in_block_comment = False
            elif stripped.startswith("/*"):
                ignore_line = True
                if "*/" not in stripped:
                    in_block_comment = True
            elif stripped.startswith(("//", "*")):
                ignore_line = True
            elif stripped.startswith(
                (
                    "#include",
                    "=cut",
                    "=head",
                    "export {",
                    "from ",
                    "import",
                    "package ",
                    "require ",
                    "use ",
                )
            ):
                ignore_line = True

            is_meaningful_uncovered = (
                not covered[index] and bool(stripped) and not ignore_line
            )
            if is_meaningful_uncovered and start is None:
                start = index
            elif not is_meaningful_uncovered and start is not None:
                if self._range_has_signal(lines, start, index):
                    ranges.append((start, index))
                start = None

        if start is not None and self._range_has_signal(lines, start, len(lines)):
            ranges.append((start, len(lines)))

        return self._build_windows_from_ranges(
            relative_path=relative_path,
            language=language,
            lines=lines,
            imports=imports,
            parent_chunk_id=parent_chunk_id,
            ranges=ranges,
            chunk_strategy="top_level_window",
        )

    def _build_windows_from_ranges(
        self,
        relative_path: str,
        language: str,
        lines: list[str],
        imports: list[str],
        parent_chunk_id: str,
        ranges: list[tuple[int, int]],
        chunk_strategy: str,
    ) -> list[CodeChunk]:
        """Builds window chunks from selected line ranges."""
        chunks: list[CodeChunk] = []
        window_size = self.target_chunk_lines

        for range_start, range_end in ranges:
            for start_index in range(range_start, range_end, window_size):
                end_index = min(start_index + window_size, range_end)
                snippet = "\n".join(lines[start_index:end_index]).strip()
                if not snippet:
                    continue

                start_line = start_index + 1
                end_line = end_index
                chunk_id = self._make_chunk_id(
                    relative_path, "file_window", relative_path, start_line, end_line
                )
                references = self._extract_references(snippet)
                contextualized_text = self._build_contextualized_text(
                    path=relative_path,
                    language=language,
                    chunk_type="file_window",
                    symbol=None,
                    parent_symbol=relative_path,
                    imports=imports,
                    references=references,
                    top_level_symbols=[],
                    code_unit=snippet,
                    chunk_strategy=chunk_strategy,
                )
                chunks.append(
                    CodeChunk(
                        chunk_id=chunk_id,
                        parent_chunk_id=parent_chunk_id,
                        chunk_type="file_window",
                        path=relative_path,
                        language=language,
                        start_line=start_line,
                        end_line=end_line,
                        content=snippet,
                        contextualized_text=contextualized_text,
                        symbol=None,
                        ast_node_type="file_window",
                        declaration_type="file_window",
                        parent_symbol=relative_path,
                        keywords=self._build_keywords(
                            relative_path, language, None, relative_path, snippet
                        ),
                        imports=imports,
                        references=references,
                        top_level_symbols=[],
                        code_unit=snippet,
                    )
                )

        return chunks

    def _extract_symbol_from_node(
        self, node, source_bytes: bytes, language: str, snippet: str
    ) -> str | None:
        """Extracts a declaration symbol from an AST node, with snippet parsing as fallback."""
        for field in ("name", "declarator", "identifier", "property", "key"):
            name_node = None
            try:
                name_node = node.child_by_field_name(field)
            except Exception:
                name_node = None
            if name_node is not None:
                name = self._extract_identifier_from_node(name_node, source_bytes)
                if name:
                    return name

        symbol = self._extract_js_symbol_from_node(node, source_bytes, snippet)
        if symbol:
            return symbol

        # Generic fallback: find the first identifier child that is likely a declaration name.
        for child in getattr(node, "children", []):
            if child.type in {
                "identifier",
                "type_identifier",
                "property_identifier",
                "field_identifier",
                "constant",
            }:
                name = (
                    source_bytes[child.start_byte : child.end_byte]
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
                if name and name not in {
                    "function",
                    "class",
                    "def",
                    "sub",
                    "fn",
                    "func",
                }:
                    return name
            if getattr(child, "is_named", True):
                name = self._extract_identifier_from_node(child, source_bytes)
                if name:
                    return name

        return self._extract_symbol_from_snippet(language, snippet)

    def _extract_identifier_from_node(self, node, source_bytes: bytes) -> str | None:
        """Extracts a readable identifier from a possibly nested declarator node."""
        if node.type in {
            "identifier",
            "type_identifier",
            "property_identifier",
            "field_identifier",
            "constant",
        }:
            return (
                source_bytes[node.start_byte : node.end_byte]
                .decode("utf-8", errors="ignore")
                .strip()
            )

        for child in getattr(node, "children", []):
            name = self._extract_identifier_from_node(child, source_bytes)
            if name:
                return name
        return None

    def _extract_js_symbol_from_node(
        self, node, source_bytes: bytes, snippet: str
    ) -> str | None:
        """Extracts symbols from common JS/TS module-level declarations."""
        match = JS_ASSIGNMENT_RE.search(snippet)
        if match:
            return match.group(1)

        match = OBJECT_NAME_RE.search(snippet)
        if match:
            return match.group(1)

        if node.type == "object":
            return "default_export"

        return None

    def _extract_symbol_from_snippet(self, language: str, snippet: str) -> str | None:
        """Pulls the declared symbol name from the first line of a chunk snippet."""
        first_line = snippet.splitlines()[0] if snippet else ""

        if language == "perl":
            match = PERL_DECLARATION_RE.match(first_line)
            return match.group(1) if match else None

        if language == "python":
            match = PYTHON_DECLARATION_RE.match(first_line) or PYTHON_CLASS_RE.match(
                first_line
            )
            return match.group(1) if match else None

        if language in {"javascript", "typescript"}:
            match = JS_ASSIGNMENT_RE.search(first_line)
            if match:
                return match.group(1)

        match = OPENING_BRACE_DECLARATION_RE.match(
            first_line
        ) or C_DECLARATION_RE.match(first_line)
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
                    imports.extend(
                        part.strip()
                        for part in match.group(1).split(",")
                        if part.strip()
                    )
                else:
                    group = match.group(1) if match.groups() else None
                    if group:
                        imports.append(group.strip())

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
        chunk_strategy: str = "unknown",
    ) -> str:
        """Builds the exact text that will be indexed and embedded for one chunk."""
        lines = [
            f"Path: {path}",
            f"Language: {language}",
            f"Chunk Type: {chunk_type}",
            f"Chunk Strategy: {chunk_strategy}",
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

    def _make_chunk_id(
        self, path: str, chunk_type: str, symbol: str, start_line: int, end_line: int
    ) -> str:
        """Builds a deterministic identifier for one chunk."""
        digest = hashlib.md5(
            f"{path}:{chunk_type}:{symbol}:{start_line}:{end_line}".encode("utf-8")
        ).hexdigest()[:12]
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

        cursor = index + 1
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1

        if cursor < len(lines) and lines[cursor].strip().startswith("{"):
            return declaration_match.group(1)

        return None

    def _parent_class_symbol(self, parents: list[dict]) -> str | None:
        """Finds the nearest enclosing class-like symbol in the current traversal stack."""
        for parent in reversed(parents):
            if parent["chunk_type"] == "class":
                return parent.get("symbol")

        return None

    def _parent_symbol_from_lines(
        self, language: str, lines: list[str], line_number: int
    ) -> str | None:
        """Looks upward in the file to find a containing module or package name."""
        if language == "perl":
            for index in range(line_number - 2, -1, -1):
                match = PERL_PACKAGE_RE.match(lines[index])
                if match:
                    return match.group(1)

        if language == "python":
            # Python module identity is represented by relative path elsewhere.
            return None

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
        candidates = tokenize(
            "\n".join(
                filter(
                    None,
                    [
                        relative_path,
                        language,
                        parent_symbol or "",
                        symbol or "",
                        snippet,
                    ],
                )
            )
        )
        return self._unique_limited(
            [token for token in candidates if len(token) >= 3], limit=12
        )

    def _unique_limited(self, values: list[str], limit: int) -> list[str]:
        """Deduplicates values while preserving order and enforcing an upper bound."""
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
            if len(result) >= limit:
                break

        return result

    def _range_has_signal(self, lines: list[str], start: int, end: int) -> bool:
        """Checks whether an uncovered range is worth indexing."""
        text = "\n".join(lines[start:end]).strip()
        if not text:
            return False
        stripped_lines = [line.strip() for line in lines[start:end] if line.strip()]
        if len(stripped_lines) < 2:
            return False

        metadata_prefixes = (
            "#include",
            "//",
            "/*",
            "*",
            "=cut",
            "=head",
            "export {",
            "from ",
            "import",
            "package ",
            "require ",
            "use ",
        )
        if stripped_lines and all(
            line.startswith(metadata_prefixes) for line in stripped_lines
        ):
            return False
        if stripped_lines and stripped_lines[0].startswith("import"):
            return False

        code_lines = [
            line
            for line in stripped_lines
            if not line.startswith(("//", "/*", "*"))
        ]
        if code_lines and all(
            line.endswith(";") and "(" in line and "=" not in line and "{" not in line
            for line in code_lines
        ):
            return False

        return any(line.startswith("#define") for line in code_lines) or any(
            char in text for char in "={["
        )

    def _strip_string_literals(self, line: str) -> str:
        """Removes simple quoted strings before brace counting in heuristic fallback."""
        line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
        line = re.sub(r"'(?:\\.|[^'\\])*'", "''", line)
        return line
