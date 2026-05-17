from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
from pathlib import Path

from fastapi import HTTPException

from app.components.code_search.embeddings import EmbeddingClient, HashingEmbeddingClient
from app.components.code_search.chunker import CodeChunker
from app.components.code_search.models import CodeChunk, IndexStats, RetrievedChunk
from app.components.code_search.retriever import HybridRetriever
from app.components.code_search.vector_store import InMemoryVectorStore


EXCLUDED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "vendor",
    "build",
    "dist",
    "coverage",
}

PRIORITIZED_TOP_LEVEL_DIRECTORIES = {
    "app",
    "backend",
    "client",
    "cmd",
    "go",
    "lib",
    "server",
    "services",
    "src",
}

TEXT_FILE_SUFFIXES = {
    "",
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".hh",
    ".hpp",
    ".html",
    ".htm",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mk",
    ".pl",
    ".pm",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".t",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(slots=True)
class IndexedCodeFile:
    path: str
    language: str
    content_hash: str
    size_bytes: int
    chunks: list[CodeChunk]


class CodebaseIndex:
    def __init__(
        self,
        chunks: list[CodeChunk],
        stats: IndexStats,
        embedding_client: EmbeddingClient | None = None,
        vector_store=None,
        force_vector_reindex: bool = False,
    ):
        """Wraps indexed chunks and initializes the retriever used for question answering."""
        self.chunks = chunks
        self.stats = stats
        self._retriever = HybridRetriever(
            chunks,
            embedding_client=embedding_client or HashingEmbeddingClient(),
            vector_store=vector_store or InMemoryVectorStore(),
            force_reindex=force_vector_reindex,
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        bm25_query: str | None = None,
        vector_query: str | None = None,
    ) -> list[RetrievedChunk]:
        """Runs hybrid retrieval over the indexed chunks for a developer query."""
        return self._retriever.search(
            query=query,
            top_k=top_k,
            bm25_query=bm25_query,
            vector_query=vector_query,
        )

    def stats_payload(self) -> dict:
        """Formats index statistics for API responses and diagnostics."""
        return {
            "repository_path": self.stats.repository_path,
            "files_indexed": self.stats.files_indexed,
            "chunks_indexed": self.stats.chunks_indexed,
        }


class CodebaseIndexer:
    def __init__(
        self,
        chunker: CodeChunker | None = None,
        embedding_client: EmbeddingClient | None = None,
        vector_store_factory=None,
    ):
        """Configures repository indexing with chunking, embeddings, and vector storage."""
        self.chunker = chunker or CodeChunker()
        self.embedding_client = embedding_client or HashingEmbeddingClient()
        self.vector_store_factory = vector_store_factory or (
            lambda repository_path: InMemoryVectorStore()
        )

    def iter_files(
        self,
        repository_path: str | Path,
        max_files: int = 2_000,
        max_file_bytes: int = 200_000,
    ) -> Iterator[IndexedCodeFile]:
        """
        Scans a repository and yields indexed files one by one.

        This method is intended for persistent index build:
        - file/chunk rows can be saved to Postgres incrementally;
        - embeddings can be generated in smaller batches;
        - vectors can be written to Qdrant incrementally.
        """
        root_path = Path(repository_path).resolve()

        if not root_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Repository path not found: {root_path}",
            )

        if not root_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"Repository path must be a directory: {root_path}",
            )

        files = self._scan_files(
            root_path,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
        )

        for file_path in files:
            raw_bytes = self._read_file_bytes(file_path)
            if not raw_bytes:
                continue

            if b"\x00" in raw_bytes:
                continue

            content = raw_bytes.decode("utf-8", errors="ignore")
            if not content:
                continue

            chunks = self.chunker.chunk_text(
                file_path=file_path,
                root_path=root_path,
                content=content,
            )
            if not chunks:
                continue

            try:
                relative_path = file_path.relative_to(root_path).as_posix()
            except ValueError:
                relative_path = file_path.name

            yield IndexedCodeFile(
                path=relative_path,
                language=self.chunker.detect_language(file_path),
                content_hash=hashlib.sha256(raw_bytes).hexdigest(),
                size_bytes=len(raw_bytes),
                chunks=chunks,
            )

    def collect_files(
        self,
        repository_path: str | Path,
        max_files: int = 2_000,
        max_file_bytes: int = 200_000,
    ) -> list[IndexedCodeFile]:
        """
        Scans a repository and builds code chunks without creating a retriever.

        Kept for compatibility with persistent-index code that still wants
        the complete list in memory. New long-running build code should prefer
        iter_files().
        """
        indexed_files = list(
            self.iter_files(
                repository_path=repository_path,
                max_files=max_files,
                max_file_bytes=max_file_bytes,
            )
        )

        if not indexed_files:
            root_path = Path(repository_path).resolve()
            raise HTTPException(
                status_code=404,
                detail=f"No searchable code chunks found under: {root_path}",
            )

        return indexed_files

    def build(
        self,
        repository_path: str | Path,
        max_files: int = 2_000,
        max_file_bytes: int = 200_000,
        force_vector_reindex: bool = False,
    ) -> CodebaseIndex:
        """Scans a repository path, chunks files, and produces a searchable code index."""
        root_path = Path(repository_path).resolve()

        if not root_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Repository path not found: {root_path}",
            )

        if not root_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"Repository path must be a directory: {root_path}",
            )

        files = self._scan_files(
            root_path,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
        )
        chunks: list[CodeChunk] = []

        for file_path in files:
            content = self._read_text_file(file_path)
            if not content:
                continue

            chunks.extend(
                self.chunker.chunk_text(
                    file_path=file_path,
                    root_path=root_path,
                    content=content,
                )
            )

        if not chunks:
            raise HTTPException(
                status_code=404,
                detail=f"No searchable code chunks found under: {root_path}",
            )

        stats = IndexStats(
            repository_path=str(root_path),
            files_indexed=len(files),
            chunks_indexed=len(chunks),
        )

        return CodebaseIndex(
            chunks=chunks,
            stats=stats,
            embedding_client=self.embedding_client,
            vector_store=self.vector_store_factory(root_path),
            force_vector_reindex=force_vector_reindex,
        )

    def _scan_files(
        self,
        root_path: Path,
        max_files: int,
        max_file_bytes: int,
    ) -> list[Path]:
        """Collects candidate text files under the repository while applying size and path filters."""
        candidates: list[Path] = []

        for file_path in root_path.rglob("*"):
            if not file_path.is_file():
                continue

            if any(part in EXCLUDED_DIRECTORIES for part in file_path.parts):
                continue

            if file_path.suffix.lower() not in TEXT_FILE_SUFFIXES:
                continue

            try:
                if file_path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue

            candidates.append(file_path)

        candidates.sort(key=lambda path: self._sort_key(root_path, path))
        return candidates[:max_files]

    def _sort_key(self, root_path: Path, file_path: Path) -> tuple[int, int, str]:
        """Ranks files so source-heavy paths are indexed before less relevant content."""
        relative_parts = file_path.relative_to(root_path).parts
        top_level = relative_parts[0] if relative_parts else ""
        priority = 0 if top_level in PRIORITIZED_TOP_LEVEL_DIRECTORIES else 1
        return (priority, len(relative_parts), file_path.as_posix())

    def _read_file_bytes(self, file_path: Path) -> bytes:
        """Reads raw file bytes and returns an empty byte string on read errors."""
        try:
            return file_path.read_bytes()
        except OSError:
            return b""

    def _read_text_file(self, file_path: Path) -> str:
        """Reads a file as text and skips binary-looking content."""
        raw_bytes = self._read_file_bytes(file_path)

        if not raw_bytes:
            return ""

        if b"\x00" in raw_bytes:
            return ""

        return raw_bytes.decode("utf-8", errors="ignore")


class CodebaseIndexCache:
    def __init__(self, indexer: CodebaseIndexer | None = None):
        """Creates an in-memory cache for repository indexes keyed by path and limits."""
        self.indexer = indexer or CodebaseIndexer()
        self._cache: dict[tuple[str, int, int], CodebaseIndex] = {}

    def get_or_build(
        self,
        repository_path: str | Path,
        max_files: int = 2_000,
        max_file_bytes: int = 200_000,
        force_reindex: bool = False,
    ) -> CodebaseIndex:
        """Returns a cached index or rebuilds it when missing or explicitly requested."""
        key = (str(Path(repository_path).resolve()), max_files, max_file_bytes)

        if force_reindex or key not in self._cache:
            self._cache[key] = self.indexer.build(
                repository_path=repository_path,
                max_files=max_files,
                max_file_bytes=max_file_bytes,
                force_vector_reindex=force_reindex,
            )

        return self._cache[key]
