from fastapi import APIRouter, Depends

from app.api.dependencies import get_codebase_index_cache
from app.components.code_search.indexer import CodebaseIndexCache
from app.schemas.api import (
    CodebaseIndexRebuildRequest,
    CodebaseIndexRebuildResponse,
)


router = APIRouter(
    prefix="/codebase-index",
    tags=["codebase-index"],
)


@router.post("/rebuild", response_model=CodebaseIndexRebuildResponse)
def rebuild_codebase_index(
    data: CodebaseIndexRebuildRequest,
    index_cache: CodebaseIndexCache = Depends(get_codebase_index_cache),
):
    index = index_cache.get_or_build(
        repository_path=data.repository_path,
        max_files=data.max_files,
        max_file_bytes=data.max_file_bytes,
        force_reindex=True,
    )

    stats = index.stats_payload()

    return {
        "repository_path": stats["repository_path"],
        "files_indexed": stats["files_indexed"],
        "chunks_indexed": stats["chunks_indexed"],
    }
