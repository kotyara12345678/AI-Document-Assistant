"""Qdrant client wrapper for document chunk storage and semantic search."""

import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings
from app.vector.embeddings import get_embedding_dimension


@lru_cache
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)


def ensure_collection(
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> None:
    """Create the collection if it does not exist yet.

    Vector size is derived from the loaded embedding model.
    """
    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION

    try:
        client.get_collection(name)
    except Exception:
        client.create_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(
                size=get_embedding_dimension(),
                distance=qmodels.Distance.COSINE,
            ),
        )


def upsert_chunks(
    vectors: list[list[float]],
    payloads: list[dict],
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> int:
    """Insert chunk vectors with payloads into the collection. Returns point count."""
    if len(vectors) != len(payloads):
        raise ValueError("vectors and payloads must have the same length")
    if not vectors:
        return 0

    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION
    ensure_collection(client, name)

    points = [
        qmodels.PointStruct(id=uuid.uuid4(), vector=vec, payload=payload)
        for vec, payload in zip(vectors, payloads)
    ]
    client.upsert(collection_name=name, points=points)
    return len(points)


def delete_document_vectors(
    document_id: int,
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> int:
    """Delete all points belonging to a document. Returns deleted count."""
    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION

    try:
        client.get_collection(name)
    except Exception:
        return 0

    result = client.delete(
        collection_name=name,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="document_id",
                        match=qmodels.MatchValue(value=document_id),
                    )
                ]
            )
        ),
    )
    deleted = getattr(result, "status", None)
    return 1 if deleted else 0


def delete_user_vectors(
    user_id: int,
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> int:
    """Delete all points belonging to a user. Returns deleted count."""
    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION

    try:
        client.get_collection(name)
    except Exception:
        return 0

    result = client.delete(
        collection_name=name,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="user_id",
                        match=qmodels.MatchValue(value=user_id),
                    )
                ]
            )
        ),
    )
    return 1 if getattr(result, "status", None) else 0


def document_vector_count(
    document_id: int,
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> int:
    """Number of chunk points stored for a document (0 if the collection is missing)."""
    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION

    try:
        client.get_collection(name)
    except Exception:
        return 0

    result = client.count(
        collection_name=name,
        count_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="document_id",
                    match=qmodels.MatchValue(value=document_id),
                )
            ]
        ),
        exact=True,
    )
    return int(getattr(result, "count", 0))


def search_vectors(
    query_vector: list[float],
    limit: int = 5,
    user_id: int | None = None,
    document_id: int | None = None,
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> list[dict]:
    """Semantic search. Returns list of {score, payload} dicts."""
    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION

    must = []
    if user_id is not None:
        must.append(
            qmodels.FieldCondition(
                key="user_id",
                match=qmodels.MatchValue(value=user_id),
            )
        )
    if document_id is not None:
        must.append(
            qmodels.FieldCondition(
                key="document_id",
                match=qmodels.MatchValue(value=document_id),
            )
        )
    query_filter = qmodels.Filter(must=must) if must else None

    results = client.query_points(
        collection_name=name,
        query=query_vector,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )

    return [
        {
            "score": hit.score,
            "payload": hit.payload or {},
        }
        for hit in results.points
    ]
