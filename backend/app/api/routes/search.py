from fastapi import APIRouter, Depends

from app.core.security import get_current_user_id
from app.schemas.search import SearchRequest, SearchResponse
from app.services import search as search_service

router = APIRouter()


@router.post("", response_model=SearchResponse)
def search(
    request: SearchRequest,
    user_id: int = Depends(get_current_user_id),
) -> SearchResponse:
    return search_service.semantic_search(
        query=request.query,
        limit=request.limit,
        user_id=user_id,
    )
