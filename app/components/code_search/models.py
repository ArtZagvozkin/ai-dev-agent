from dataclasses import dataclass


@dataclass(slots=True)
class CodeChunk:
    chunk_id: str
    parent_chunk_id: str | None
    chunk_type: str
    path: str
    language: str
    start_line: int
    end_line: int
    content: str
    contextualized_text: str
    symbol: str | None = None
    ast_node_type: str | None = None
    declaration_type: str | None = None
    parent_symbol: str | None = None
    keywords: list[str] | None = None
    imports: list[str] | None = None
    references: list[str] | None = None
    top_level_symbols: list[str] | None = None
    code_unit: str | None = None

    def cache_key(self) -> tuple[str, int, int]:
        """Builds a stable identifier for deduplication across retrieval stages."""
        return (self.path, self.start_line, self.end_line)

    def is_full_code_unit(self) -> bool:
        """Checks whether the chunk represents a named declaration rather than a plain window."""
        return bool(self.symbol and self.declaration_type)


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    parent_chunk_id: str | None
    chunk_type: str
    path: str
    language: str
    start_line: int
    end_line: int
    content: str
    contextualized_text: str
    symbol: str | None
    score: float
    bm25_score: float
    vector_score: float
    combined_score: float
    ast_node_type: str | None = None
    declaration_type: str | None = None
    parent_symbol: str | None = None
    keywords: list[str] | None = None
    imports: list[str] | None = None
    references: list[str] | None = None
    top_level_symbols: list[str] | None = None
    code_unit: str | None = None

    def cache_key(self) -> tuple[str, int, int]:
        """Builds a stable identifier for deduplication across retrieval stages."""
        return (self.path, self.start_line, self.end_line)

    def is_full_code_unit(self) -> bool:
        """Checks whether the retrieved item represents a full named code unit."""
        return bool(self.symbol and self.declaration_type)


@dataclass(slots=True)
class VectorSearchHit:
    chunk_id: str
    parent_chunk_id: str | None
    chunk_type: str
    path: str
    language: str
    start_line: int
    end_line: int
    content: str
    contextualized_text: str
    symbol: str | None
    vector_score: float
    ast_node_type: str | None = None
    declaration_type: str | None = None
    parent_symbol: str | None = None
    keywords: list[str] | None = None
    imports: list[str] | None = None
    references: list[str] | None = None
    top_level_symbols: list[str] | None = None
    code_unit: str | None = None

    def cache_key(self) -> tuple[str, int, int]:
        """Builds a stable identifier for matching vector hits back to chunks."""
        return (self.path, self.start_line, self.end_line)


@dataclass(slots=True)
class IndexStats:
    repository_path: str
    files_indexed: int
    chunks_indexed: int
