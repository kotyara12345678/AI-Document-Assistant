"""Semantic search over indexed document chunks (no LLM involved)."""

from app.schemas.search import SearchResultItem, SearchResponse
from app.vector import client as vector_client
from app.vector.embeddings import embed_text


def semantic_search(
    query: str,
    limit: int,
    user_id: int,
) -> SearchResponse:
    query_vector = embed_text(query)
    results = vector_client.search_vectors(
        query_vector=query_vector,
        limit=limit,
        user_id=user_id,
    )

    items = [
        SearchResultItem(
            document_id=payload.get("document_id"),
            filename=payload.get("original_filename", ""),
            chunk_index=payload.get("chunk_index"),
            text=payload.get("text", ""),
            score=result["score"],
        )
        for result in results
        if (payload := result.get("payload"))
    ]

    return SearchResponse(query=query, results=items)
