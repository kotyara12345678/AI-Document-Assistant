"""RAG hybrid retrieval: semantic (Qdrant) + keyword (PostgreSQL FTS).

The two retrievers run in parallel. Semantic scores are already cosine
similarities in [0, 1]; keyword ts_rank values are max-normalized onto the
same scale. Results are merged with dedup by (document_id, chunk_index),
keeping the higher of the two scores, then sorted and filtered by min_score.
When the reranker is enabled the merged candidates are re-scored by a
cross-encoder and cut down to the final top_k (see reranker.py).
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.config import settings
from app.database.session import SessionLocal
from app.schemas.chat import SourceRef
from app.services import reranker
from app.vector import client as vector_client
from app.vector.embeddings import embed_text

logger = logging.getLogger("app.retrieval")

# Full-text search configuration used both for the GIN index expression and
# for the tsquery/ts_rank calls (kept in sync with models/document_chunk.py).
FTS_CONFIG = "russian"


@dataclass(frozen=True)
class RetrievedChunk:
    source: SourceRef
    score: float
    text: str


def _as_document_ids(
    document_id: int | list[int] | None,
) -> list[int] | None:
    """Normalize a single id or a list of ids into one list (None = no filter)."""
    if document_id is None:
        return None
    if isinstance(document_id, int):
        return [document_id]
    ids = list(document_id)
    return ids or None


def _semantic_search(
    question: str,
    user_id: int,
    document_ids: list[int] | None,
    top_k: int,
) -> list[RetrievedChunk]:
    """Embed the question and fetch the most relevant chunks from Qdrant."""
    try:
        query_vector = embed_text(question)
        results = vector_client.search_vectors(
            query_vector=query_vector,
            limit=top_k,
            user_id=user_id,
            document_ids=document_ids,
        )
    except Exception:
        logger.exception("Vector search failed; returning no semantic context")
        return []

    chunks: list[RetrievedChunk] = []
    for result in results:
        payload = result.get("payload")
        if not payload:
            continue
        score = result["score"]
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


def _keyword_search(
    question: str,
    user_id: int,
    document_ids: list[int] | None,
    top_k: int,
) -> list[dict]:
    """Rank chunks by PostgreSQL full-text search (ts_rank).

    The query is built with OR semantics over the question's lexemes so a
    single word absent from a document does not suppress the whole match
    (plain/websearch_to_tsquery AND every token together). ts_rank then orders
    chunks by how many terms they cover.

    ``document_ids`` restricts matches to those documents; None searches the
    user's whole library.

    Returns raw rows {document_id, chunk_index, text, filename, rank}.
    """
    try:
        db = SessionLocal()
        try:
            sql = (
                "SELECT dc.document_id AS document_id, dc.chunk_index AS chunk_index, "
                "dc.text AS text, d.original_filename AS filename, "
                f"ts_rank(to_tsvector('{FTS_CONFIG}', dc.text), q.query) AS rank "
                "FROM document_chunks dc "
                "JOIN documents d ON d.id = dc.document_id "
                "CROSS JOIN ("
                "  SELECT to_tsquery('" + FTS_CONFIG + "', "
                "    string_agg(lexeme, ' | ')) AS query "
                "  FROM unnest(to_tsvector('" + FTS_CONFIG + "', :question))"
                ") q "
                f"WHERE d.user_id = :user_id "
                f"AND to_tsvector('{FTS_CONFIG}', dc.text) @@ q.query"
            )
            params: dict = {"question": question, "user_id": user_id}
            if document_ids:
                if len(document_ids) == 1:
                    sql += " AND dc.document_id = :document_id"
                    params["document_id"] = document_ids[0]
                else:
                    sql += " AND dc.document_id = ANY(:document_ids)"
                    params["document_ids"] = document_ids
            sql += " ORDER BY rank DESC LIMIT :limit"
            params["limit"] = top_k

            rows = db.execute(sa.text(sql), params).mappings().all()
            return [dict(row) for row in rows]
        finally:
            db.close()
    except Exception:
        logger.exception("Keyword search failed; returning no keyword context")
        return []


def _merge_results(
    semantic_chunks: list[RetrievedChunk],
    keyword_rows: list[dict],
    top_k: int,
    min_score: float,
) -> list[RetrievedChunk]:
    """Normalize keyword scores and merge both result sets, dedup by chunk."""
    # Map (document_id, chunk_index) -> raw row for the keyword hits.
    kw_rows: dict[tuple[int, int], dict] = {}
    for row in keyword_rows:
        kw_rows[(row["document_id"], row["chunk_index"])] = row

    # Max-normalize keyword ranks to [0, 1] so both retrievers share a scale.
    kw_scores: dict[tuple[int, int], float] = {}
    max_rank = max((row["rank"] for row in keyword_rows), default=0.0)
    if max_rank > 0:
        for key, row in kw_rows.items():
            kw_scores[key] = row["rank"] / max_rank

    merged: dict[tuple[int, int], RetrievedChunk] = {}
    for chunk in semantic_chunks:
        key = (chunk.source.document_id, chunk.source.chunk_index)
        merged[key] = chunk

    for key, row in kw_rows.items():
        score = kw_scores.get(key, 0.0)
        if key in merged:
            old = merged[key]
            if score > old.score:
                merged[key] = RetrievedChunk(
                    source=SourceRef(
                        document_id=old.source.document_id,
                        filename=old.source.filename,
                        chunk_index=old.source.chunk_index,
                        score=score,
                        text=old.source.text,
                    ),
                    score=score,
                    text=old.text,
                )
        elif score > 0:
            merged[key] = RetrievedChunk(
                source=SourceRef(
                    document_id=row["document_id"],
                    filename=row["filename"],
                    chunk_index=row["chunk_index"],
                    score=score,
                    text=row["text"][:1000],
                ),
                score=score,
                text=row["text"],
            )

    results = sorted(merged.values(), key=lambda chunk: chunk.score, reverse=True)
    results = [chunk for chunk in results if chunk.score >= min_score]
    return results[:top_k]


def _rerank(question: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Re-order hybrid candidates with the cross-encoder reranker.

    Each candidate gets a sigmoid relevance score in [0, 1] (see
    reranker.compute_scores); the top_k candidates are returned, carrying the
    reranker score in place of the raw hybrid score so sources reflect it.
    """
    scores = reranker.compute_scores(question, [chunk.text for chunk in chunks])
    ranked = sorted(
        zip(chunks, scores), key=lambda pair: pair[1], reverse=True
    )
    results: list[RetrievedChunk] = []
    for chunk, score in ranked[:top_k]:
        score = round(float(score), 6)
        results.append(
            RetrievedChunk(
                source=SourceRef(
                    document_id=chunk.source.document_id,
                    filename=chunk.source.filename,
                    chunk_index=chunk.source.chunk_index,
                    score=score,
                    text=chunk.source.text,
                ),
                score=score,
                text=chunk.text,
            )
        )
    return results


def retrieve_context(
    question: str,
    user_id: int,
    document_id: int | list[int] | None = None,
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[RetrievedChunk]:
    """Run semantic + keyword retrieval in parallel and merge the results.

    ``document_id`` may be a single id or a list of ids to search several
    documents at once; when None (or empty) the search runs across the user's
    whole library. Results from all selected documents are merged and go
    through the same classic re-ranking step.

    When the reranker is enabled the merge step first produces a wider
    candidate pool (RERANKER_CANDIDATES) which is then re-ranked by the
    cross-encoder and cut down to ``top_k``. When disabled the results are
    returned straight from the hybrid merge. If re-ranking fails the code
    falls back to the hybrid order instead of failing the request.

    Returns an empty list when nothing was found or both retrievers failed —
    the caller should answer honestly that no information was found instead of
    failing the request.
    """
    document_ids = _as_document_ids(document_id)
    candidate_k = top_k
    rerank_enabled = settings.RERANKER_ENABLED
    if rerank_enabled:
        candidate_k = max(top_k, settings.RERANKER_CANDIDATES)

    with ThreadPoolExecutor(max_workers=2) as executor:
        semantic_future = executor.submit(
            _semantic_search, question, user_id, document_ids, candidate_k
        )
        keyword_future = executor.submit(
            _keyword_search, question, user_id, document_ids, candidate_k
        )
        semantic_chunks = semantic_future.result()
        keyword_rows = keyword_future.result()

    merged = _merge_results(semantic_chunks, keyword_rows, candidate_k, min_score)

    if not rerank_enabled or not merged:
        return merged[:top_k]

    try:
        return _rerank(question, merged, top_k)
    except Exception:
        logger.exception("Reranker failed; falling back to hybrid order")
        return merged[:top_k]
