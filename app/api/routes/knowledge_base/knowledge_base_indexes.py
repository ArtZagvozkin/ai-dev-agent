from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, status

from app.api.dependencies import (
    get_knowledge_base_index_build_service,
    get_knowledge_base_index_repository,
)
from app.domain.knowledge_base_indexes import KnowledgeBaseIndex
from app.repositories.knowledge_base_indexes import KnowledgeBaseIndexRepository
from app.schemas.knowledge_base_indexes import (
    KnowledgeBaseIndexBuildRequest,
    KnowledgeBaseIndexResponse,
)
from app.services.knowledge_base.knowledge_base_index_build import (
    KnowledgeBaseIndexBuildService,
)


router = APIRouter(
    prefix="/knowledge-bases",
    tags=["knowledge-bases"],
)


@router.get(
    "/{kb_key}/indexes",
    response_model=list[KnowledgeBaseIndexResponse],
)
def list_knowledge_base_indexes(
    kb_key: str,
    status_filter: str | None = Query(default=None, alias="status"),
    repository: KnowledgeBaseIndexRepository = Depends(
        get_knowledge_base_index_repository,
    ),
) -> list[KnowledgeBaseIndexResponse]:
    indexes = repository.list(
        kb_key=kb_key,
        status=status_filter,
    )

    return [
        _index_to_response(index)
        for index in indexes
    ]


@router.post(
    "/{kb_key}/index/build",
    response_model=KnowledgeBaseIndexResponse,
    status_code=status.HTTP_201_CREATED,
)
def build_knowledge_base_index(
    kb_key: str,
    data: KnowledgeBaseIndexBuildRequest = Body(...),
    service: KnowledgeBaseIndexBuildService = Depends(
        get_knowledge_base_index_build_service,
    ),
):
    return service.build_first_index(
        kb_key=kb_key,
        data=data.to_service_request(),
    )


@router.post(
    "/{kb_key}/index/rebuild",
    response_model=KnowledgeBaseIndexResponse,
)
def rebuild_knowledge_base_index(
    kb_key: str,
    data: KnowledgeBaseIndexBuildRequest = Body(...),
    service: KnowledgeBaseIndexBuildService = Depends(
        get_knowledge_base_index_build_service,
    ),
):
    return service.rebuild_index(
        kb_key=kb_key,
        data=data.to_service_request(),
    )


def _index_to_response(index: KnowledgeBaseIndex) -> KnowledgeBaseIndexResponse:
    return KnowledgeBaseIndexResponse(
        id=index.id,
        knowledge_base_id=index.knowledge_base_id,
        kb_key=index.kb_key,
        status=index.status,
        index_schema_version=index.index_schema_version,
        embedding_provider=index.embedding_provider,
        embedding_model=index.embedding_model,
        embedding_dimensions=index.embedding_dimensions,
        qdrant_collection_name=index.qdrant_collection_name,
        documents_count=index.documents_count,
        chunks_count=index.chunks_count,
        build_started_at=index.build_started_at,
        build_finished_at=index.build_finished_at,
        error_message=index.error_message,
        is_current=index.is_current,
    )
