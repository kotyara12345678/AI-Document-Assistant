"""Semantic search over indexed document chunks (no LLM involved)."""

import logging

from app.schemas.search import SearchResultItem, SearchResponse
from app.vector import client as vector_client
from app.vector.embeddings import embed_text

logger = logging.getLogger("app.search")


def semantic_search(
    query: str,
    limit: int,
    user_id: int,
) -> SearchResponse:
    """Semantic search over the user's chunks.

    Embedding or Qdrant failures degrade to an empty result set instead of a
    500, matching the RAG/chat path, so the search endpoint never bombs a UI
    when the vector collection is missing or Qdrant is briefly unavailable.
    """
    try:
        query_vector = embed_text(query)
    except Exception:
        logger.exception("Semantic search: embedding failed; returning no results")
        return SearchResponse(query=query, results=[])

    try:
        results = vector_client.search_vectors(
            query_vector=query_vector,
            limit=limit,
            user_id=user_id,
        )
    except Exception:
        logger.exception("Semantic search: vector lookup failed; returning no results")
        return SearchResponse(query=query, results=[])

    items = [
        SearchResultItem(
            document_id=payload.get("document_id") or 0,
            filename=payload.get("original_filename") or "",
            chunk_index=payload.get("chunk_index") or 0,
            text=payload.get("text") or "",
            score=result["score"],
        )
        for result in results
        if (payload := result.get("payload"))
    ]

    return SearchResponse(query=query, results=items)
