"""Exact-match retrieval layer.

Provides direct PostgreSQL ILIKE / regex matching for exact identifiers
(article numbers, INN, dates, contract numbers, phone numbers, emails,
organizations) that semantic search and FTS handle poorly.

This layer runs IN PARALLEL with the existing semantic + FTS retrievers and
its results are merged with a scoring boost so that chunks containing an exact
identifier always outrank chunks that only match semantically.

The layer is intentionally simple: a single SQL query per search that scans
the ``document_chunks`` table joined with ``documents`` (for ``user_id``
filtering). For the scale of a single-user document assistant (hundreds to
low-thousands of chunks) this is fast enough; for larger deployments it can be
replaced with a trigram index (``pg_trgm``) later.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from app.database.session import SessionLocal

logger = logging.getLogger("app.exact_match")


def _escape_ilike(value: str) -> str:
    """Escape special ILIKE characters to search for the literal string."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def exact_match_search(
    patterns: list[str],
    user_id: int,
    document_ids: list[int] | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Search for chunks whose text ILIKE-matches any of the given patterns.

    Each pattern is wrapped with ``%`` on both sides so partial matches count:
    ``"статья 105"`` matches ``"Статья 105. Убийство..."``.

    Returns a list of dicts ``{document_id, chunk_index, text, filename,
    match_score}`` where ``match_score`` is a heuristic in [0, 1] reflecting
    how many patterns matched and how specific they were.

    Patterns are ordered from most specific (longer, multi-word) to least
    specific (single number). A chunk matching a specific pattern outranks one
    matching only a generic pattern.
    """
    if not patterns:
        return []

    try:
        db = SessionLocal()
        try:
            # Build OR conditions: one ILIKE per pattern.
            # Longer patterns get higher base scores.
            conditions = []
            params: dict = {"user_id": user_id}
            for i, pat in enumerate(patterns):
                escaped = _escape_ilike(pat.strip())
                param_name = f"pat_{i}"
                conditions.append(f"dc.text ILIKE :{param_name}")
                # Longer patterns (more specific) get a higher base weight.
                # A 10-digit INN is more specific than a single digit "3".
                word_count = len(pat.split())
                char_count = len(pat)
                weight = min(1.0, 0.3 + word_count * 0.15 + char_count * 0.01)
                params[param_name] = f"%{escaped}%"
                params[f"w_{i}"] = weight

            if not conditions:
                return []

            where_clause = " OR ".join(conditions)

            # Build the scoring expression: sum of weights of matched patterns.
            case_parts = []
            for i in range(len(patterns)):
                case_parts.append(
                    f"(CASE WHEN dc.text ILIKE :pat_{i} THEN :w_{i} ELSE 0 END)"
                )
            score_expr = " + ".join(case_parts)

            sql = (
                "SELECT dc.document_id, dc.chunk_index, dc.text, "
                "d.original_filename AS filename, "
                f"({score_expr}) AS match_score "
                "FROM document_chunks dc "
                "JOIN documents d ON d.id = dc.document_id "
                f"WHERE d.user_id = :user_id AND ({where_clause})"
            )

            if document_ids:
                if len(document_ids) == 1:
                    sql += " AND dc.document_id = :doc_id"
                    params["doc_id"] = document_ids[0]
                else:
                    sql += " AND dc.document_id = ANY(:doc_ids)"
                    params["doc_ids"] = document_ids

            sql += " ORDER BY match_score DESC LIMIT :limit"
            params["limit"] = top_k

            rows = db.execute(sa.text(sql), params).mappings().all()
            # Normalize match_score to [0, 1] by capping at 1.0.
            return [
                {
                    "document_id": row["document_id"],
                    "chunk_index": row["chunk_index"],
                    "text": row["text"],
                    "filename": row["filename"],
                    "match_score": min(1.0, float(row["match_score"])),
                }
                for row in rows
            ]
        finally:
            db.close()
    except Exception:
        logger.exception("Exact match search failed")
        return []


def phrase_match_search(
    phrases: list[str],
    user_id: int,
    document_ids: list[int] | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Search for chunks containing exact phrases using PostgreSQL FTS
    ``phraseto_tsquery`` which matches words IN ORDER.

    This fills the gap between OR-FTS (too permissive) and ILIKE (no ranking).
    ``phraseto_tsquery('russian', 'статья 105')`` creates a tsquery that
    matches chunks containing "статья" immediately followed by "105" (with
    possible intervening inflected forms).
    """
    if not phrases:
        return []

    try:
        db = SessionLocal()
        try:
            conditions = []
            params: dict = {"user_id": user_id}

            for i, phrase in enumerate(phrases):
                param_name = f"phrase_{i}"
                # phraseto_tsquery handles Russian morphology:
                # "статья 105" matches "Статье 105" as well.
                conditions.append(
                    f"to_tsvector('russian', dc.text) "
                    f"@@ phraseto_tsquery('russian', :{param_name})"
                )
                params[param_name] = phrase

            if not conditions:
                return []

            where_clause = " OR ".join(conditions)

            # Phrase-matched chunks get a flat score of 0.9 (high but not
            # perfect — ILIKE is higher for exact literal matches).
            sql = (
                "SELECT dc.document_id, dc.chunk_index, dc.text, "
                "d.original_filename AS filename "
                "FROM document_chunks dc "
                "JOIN documents d ON d.id = dc.document_id "
                f"WHERE d.user_id = :user_id AND ({where_clause})"
            )

            if document_ids:
                if len(document_ids) == 1:
                    sql += " AND dc.document_id = :doc_id"
                    params["doc_id"] = document_ids[0]
                else:
                    sql += " AND dc.document_id = ANY(:doc_ids)"
                    params["doc_ids"] = document_ids

            sql += " LIMIT :limit"
            params["limit"] = top_k

            rows = db.execute(sa.text(sql), params).mappings().all()
            return [
                {
                    "document_id": row["document_id"],
                    "chunk_index": row["chunk_index"],
                    "text": row["text"],
                    "filename": row["filename"],
                    "match_score": 0.9,
                }
                for row in rows
            ]
        finally:
            db.close()
    except Exception:
        logger.exception("Phrase match search failed")
        return []
