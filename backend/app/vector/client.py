"""Qdrant client wrapper.

RAG is not implemented yet. This module provides the connection to Qdrant
and a helper to lazily create the target collection.
"""

from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings


@lru_cache
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)


def ensure_collection(
    client: QdrantClient | None = None,
    collection: str | None = None,
    vector_size: int | None = None,
) -> None:
    """Create the collection if it does not exist yet."""
    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION
    size = vector_size or settings.QDRANT_VECTOR_SIZE

    try:
        client.get_collection(name)
    except Exception:
        client.create_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(size=size, distance=qmodels.Distance.COSINE),
        )
