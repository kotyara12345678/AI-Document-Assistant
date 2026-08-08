"""RAG retrieval: embed query -> Qdrant -> ranked chunks for chat context."""

import logging
from dataclasses import dataclass

from app.schemas.chat import SourceRef
from app.vector import client as vector_client
from app.vector.embeddings import embed_text

logger = logging.getLogger("app.retrieval")


@dataclass(frozen=True)
class RetrievedChunk:
    source: SourceRef
    score: float
    text: str


def retrieve_context(
    question: str,
    user_id: int,
    document_id: int | None = None,
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[RetrievedChunk]:
    """Embed the question and fetch the most relevant chunks.

    Returns an empty list when there is nothing indexed yet or the vector
    search fails (e.g. collection is missing) — the caller should answer
    honestly that no information was found instead of failing the request.
    """
    query_vector = embed_text(question)
    try:
        results = vector_client.search_vectors(
            query_vector=query_vector,
            limit=top_k,
            user_id=user_id,
            document_id=document_id,
        )
    except Exception:
        logger.exception("Vector search failed; returning no context")
        return []

    chunks: list[RetrievedChunk] = []
    for result in results:
        score = result["score"]
        if score < min_score:
            continue
        payload = result["payload"]
        chunks.append(
            RetrievedChunk(
                source=SourceRef(
                    document_id=payload.get("document_id", 0),
                    filename=payload.get("original_filename", ""),
                    chunk_index=payload.get("chunk_index", 0),
                    score=score,
                    text=payload.get("text", "")[:1000],
                ),
                score=score,
                text=payload.get("text", ""),
            )
        )
    return chunks
