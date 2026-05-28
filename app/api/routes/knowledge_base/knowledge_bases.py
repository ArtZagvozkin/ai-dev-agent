from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, status

from app.api.dependencies import get_knowledge_base_service
from app.domain.knowledge_bases import KnowledgeBase
from app.schemas.knowledge_bases import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDeleteResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from app.services.knowledge_base.knowledge_bases import KnowledgeBaseService


router = APIRouter(
    prefix="/knowledge-bases",
    tags=["knowledge-bases"],
)


@router.get(
    "",
    response_model=list[KnowledgeBaseResponse],
)
def list_knowledge_bases(
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> list[KnowledgeBaseResponse]:
    knowledge_bases = service.list_knowledge_bases()

    return [
        _knowledge_base_to_response(knowledge_base)
        for knowledge_base in knowledge_bases
    ]


@router.post(
    "",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_base(
    data: KnowledgeBaseCreateRequest,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> KnowledgeBaseResponse:
    knowledge_base = service.create_knowledge_base(data.to_create_data())

    return _knowledge_base_to_response(knowledge_base)


@router.get(
    "/{kb_key}",
    response_model=KnowledgeBaseResponse,
)
def get_knowledge_base(
    kb_key: str,
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> KnowledgeBaseResponse:
    knowledge_base = service.get_knowledge_base(kb_key)

    return _knowledge_base_to_response(knowledge_base)


@router.patch(
    "/{kb_key}",
    response_model=KnowledgeBaseResponse,
)
def update_knowledge_base(
    kb_key: str,
    data: KnowledgeBaseUpdateRequest | None = Body(default=None),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> KnowledgeBaseResponse:
    request = data or KnowledgeBaseUpdateRequest()

    knowledge_base = service.update_knowledge_base(
        kb_key=kb_key,
        data=request.to_update_data(),
    )

    return _knowledge_base_to_response(knowledge_base)


@router.delete(
    "/{kb_key}",
    response_model=KnowledgeBaseDeleteResponse,
)
def delete_knowledge_base(
    kb_key: str,
    delete_qdrant_collections: bool = Query(default=True),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> KnowledgeBaseDeleteResponse:
    result = service.delete_knowledge_base(
        kb_key=kb_key,
        delete_qdrant_collections=delete_qdrant_collections,
    )

    return KnowledgeBaseDeleteResponse(**result)


def _knowledge_base_to_response(
    knowledge_base: KnowledgeBase,
) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=knowledge_base.id,
        kb_key=knowledge_base.kb_key,
        display_name=knowledge_base.display_name,
        source_type=knowledge_base.source_type,
        source_path=knowledge_base.source_path,
        default_top_k=knowledge_base.default_top_k,
        default_max_context_chars=knowledge_base.default_max_context_chars,
        current_index_id=knowledge_base.current_index_id,
    )
