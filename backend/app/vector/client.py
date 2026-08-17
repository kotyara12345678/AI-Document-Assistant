"""Qdrant client wrapper for document chunk storage and semantic search."""

import time
import uuid
from functools import lru_cache

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings
from app.vector.embeddings import get_embedding_dimension


@lru_cache
def get_qdrant_client() -> QdrantClient:
    # qdrant-client >= 1.13 no longer exposes set_retries()/retries in the
    # constructor, so the retry budget is applied at the wrapper level (see
    # _retry_call) instead of inside the SDK.
    return QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY or None,
        timeout=settings.QDRANT_TIMEOUT,
        check_compatibility=False,
    )


def _retry_call(fn, attempts: int | None = None) -> "object":
    """Run ``fn`` up to ``QDRANT_RETRIES + 1`` times on transient failures.

    Only transport-level errors (connect/read/write timeouts) and HTTP 5xx are
    retried: they can succeed on a retry without an application change. 4xx
    (e.g. a missing collection) and programming errors propagate immediately so
    callers keep their exact error semantics.
    """
    budget = max(1, int(settings.QDRANT_RETRIES) + 1)
    if attempts is not None:
        budget = max(1, attempts)
    last_exc: Exception | None = None
    for attempt in range(budget):
        try:
            return fn()
        except httpx.TransportError as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_exc = exc
        if attempt + 1 < budget:
            time.sleep(0.1 * (attempt + 1))
    raise last_exc


def _point_id(payload: dict) -> str:
    """Deterministic point id so a re-upsert REPLACES the chunk's point instead
    of appending a duplicate (the historical cause of stale vectors not being
    cleaned up after re-indexing).

    Qdrant only accepts unsigned integers or UUIDs as point ids, so the
    ``document_id:chunk_index`` pair is hashed into a stable UUID.
    """
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{payload.get('document_id', 0)}:{payload.get('chunk_index', 0)}",
        )
    )


def ensure_collection(
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> None:
    """Create the collection if it does not exist yet.

    Vector size is derived from the loaded embedding model. Unlike the previous
    implementation, a Qdrant outage is NOT mistaken for "collection missing"
    (which caused a bogus create attempt and a create-race between concurrent
    first uploads), and an existing collection whose vector size no longer
    matches the embedding model is reported loudly instead of failing every
    query at runtime.
    """
    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION
    expected = get_embedding_dimension()

    try:
        existing = _retry_call(lambda: client.get_collection(name))
    except Exception:
        # Probe before creating: only create when the collection is verifiably
        # absent (network errors raise from collection_exists and we do NOT
        # create, so an outage is not masked).
        try:
            exists = _retry_call(lambda: client.collection_exists(name))
        except Exception:
            return
        if not exists:
            try:
                _retry_call(
                    lambda: client.create_collection(
                        collection_name=name,
                        vectors_config=qmodels.VectorParams(
                            size=expected,
                            distance=qmodels.Distance.COSINE,
                        ),
                    )
                )
            except Exception:
                # A concurrent creator may have won the race; verify.
                try:
                    _retry_call(lambda: client.get_collection(name))
                except Exception:
                    raise
        return

    # Dimension sanity-check: a swapped embedding model would otherwise make
    # every upsert/query fail opaquely.
    try:
        vectors_conf = existing.config.params.vectors
        size = vectors_conf.size
    except Exception:
        size = None
    if size is not None and size != expected:
        raise RuntimeError(
            f"Qdrant collection '{name}' has vector size {size} but the embedding "
            f"model produces {expected}; recreate the collection (or restore the "
            f"previous model) instead of running with a broken index."
        )


def upsert_chunks(
    vectors: list[list[float]],
    payloads: list[dict],
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> int:
    """Insert chunk vectors with payloads into the collection. Returns point count.

    Point ids are deterministic (``document_id:chunk_index``), so re-indexing a
    document replaces its existing points instead of appending duplicates.
    """
    if len(vectors) != len(payloads):
        raise ValueError("vectors and payloads must have the same length")
    if not vectors:
        return 0

    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION
    ensure_collection(client, name)

    points = [
        qmodels.PointStruct(
            id=_point_id(payload), vector=vec, payload=payload
        )
        for vec, payload in zip(vectors, payloads)
    ]
    _retry_call(
        lambda: client.upsert(collection_name=name, points=points, wait=True)
    )
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
        _retry_call(lambda: client.get_collection(name))
    except Exception:
        return 0

    result = _retry_call(
        lambda: client.delete(
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
            wait=True,
        )
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
        _retry_call(lambda: client.get_collection(name))
    except Exception:
        return 0

    result = _retry_call(
        lambda: client.delete(
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
        _retry_call(lambda: client.get_collection(name))
    except Exception:
        return 0

    result = _retry_call(
        lambda: client.count(
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
    )
    return int(getattr(result, "count", 0))


def search_vectors(
    query_vector: list[float],
    limit: int = 5,
    user_id: int | None = None,
    document_id: int | None = None,
    document_ids: list[int] | None = None,
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> list[dict]:
    """Semantic search. Returns list of {score, payload} dicts.

    ``document_ids`` filters to any of the given documents and, when present,
    takes precedence over ``document_id``.

    Scores are cosine similarities clamped to ``[0, 1]`` and always carry a
    dict payload, so callers can build ``SearchResponse`` directly; a missing
    collection degrades to ``[]`` instead of a 500, mirroring the RAG path.
    """
    client = client or get_qdrant_client()
    name = collection or settings.QDRANT_COLLECTION

    try:
        _retry_call(lambda: client.get_collection(name))
    except Exception:
        return []

    must = []
    if user_id is not None:
        must.append(
            qmodels.FieldCondition(
                key="user_id",
                match=qmodels.MatchValue(value=user_id),
            )
        )
    if document_ids:
        must.append(
            qmodels.FieldCondition(
                key="document_id",
                match=qmodels.MatchAny(any=list(document_ids)),
            )
        )
    elif document_id is not None:
        must.append(
            qmodels.FieldCondition(
                key="document_id",
                match=qmodels.MatchValue(value=document_id),
            )
        )
    query_filter = qmodels.Filter(must=must) if must else None

    try:
        results = _retry_call(
            lambda: client.query_points(
                collection_name=name,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        )
    except Exception:
        return []

    out: list[dict] = []
    for hit in results.points:
        payload = hit.payload or {}
        score = getattr(hit, "score", 0) or 0
        out.append(
            {
                "score": max(0.0, min(1.0, score)),
                "payload": payload,
                "document_id": payload.get("document_id") or 0,
                "chunk_index": payload.get("chunk_index") or 0,
            }
        )
    return out
