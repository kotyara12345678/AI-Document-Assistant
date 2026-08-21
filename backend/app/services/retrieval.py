"""RAG hybrid retrieval: 4-layer architecture.

1. **Exact match** (ILIKE + phraseto_tsquery): finds chunks containing literal
   identifiers — article numbers, INN, dates, contract numbers, phones, emails.
   Highest priority for entity-heavy queries.
2. **Keyword / FTS** (OR ts_rank): broad lexical recall with coverage-weighted
   scoring.
3. **Semantic** (Qdrant cosine): embeddings-based similarity.
4. **Merge**: dedup by (document_id, chunk_index), boost chunks found by
   multiple layers, sort by final score.

When the reranker is enabled the merged candidates are re-scored by a
cross-encoder and cut down to the final top_k (see reranker.py).
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import sqlalchemy as sa

from app.core.config import settings
from app.database.session import SessionLocal
from app.schemas.chat import SourceRef
from app.services import reranker
from app.services.entity_extraction import (
    QueryEntities,
    extract_entities,
    generate_article_variants,
    get_law_keywords,
)
from app.vector import client as vector_client
from app.vector.embeddings import embed_text

logger = logging.getLogger("app.retrieval")

# Pool: 4 workers for the 4 retrieval layers (semantic, keyword, exact, phrase).
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="retrieval")

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


# ---------------------------------------------------------------------------
# Layer 1: Exact match (ILIKE)
# ---------------------------------------------------------------------------


def _build_exact_patterns(entities: QueryEntities) -> list[str]:
    """Build ILIKE patterns from extracted entities, most specific first."""
    patterns: list[str] = []

    # Article numbers with all Russian case forms; when a law is detected,
    # combined "статья N <ABBREV>" variants are prepended (higher specificity).
    for num in entities.article_numbers:
        patterns.extend(generate_article_variants(num, law_name=entities.law_name))

    # INN values (exact digit string)
    for inn in entities.inn_values:
        patterns.append(inn)

    # Contract numbers
    for cn in entities.contract_numbers:
        patterns.append(cn)
        patterns.append(f"№ {cn}")
        patterns.append(f"номер {cn}")

    # Dates
    for date in entities.dates:
        patterns.append(date)

    # Phone numbers
    for phone in entities.phone_numbers:
        patterns.append(phone)

    # Emails
    for email in entities.emails:
        patterns.append(email)

    # Organizations
    for org in entities.organizations:
        patterns.append(org)

    # Exact phrases (quoted text)
    for phrase in entities.exact_phrases:
        patterns.append(phrase)

    # Generic numbers: search as "word N" patterns to avoid false positives
    for num in entities.exact_numbers:
        patterns.append(num)

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for p in patterns:
        key = p.lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(p)
    return result


def _exact_search(
    patterns: list[str],
    user_id: int,
    document_ids: list[int] | None,
    top_k: int,
) -> list[dict]:
    """ILIKE-based exact match for entity patterns."""
    if not patterns:
        return []
    try:
        from app.services.exact_match import exact_match_search

        return exact_match_search(patterns, user_id, document_ids, top_k)
    except Exception:
        logger.exception("Exact match search failed")
        return []


# ---------------------------------------------------------------------------
# Layer 2: FTS phrase match (phraseto_tsquery)
# ---------------------------------------------------------------------------


def _build_phrase_queries(entities: QueryEntities) -> list[str]:
    """Build phrase queries for phraseto_tsquery from entities."""
    phrases: list[str] = []

    for num in entities.article_numbers:
        # Combined article+law phrases (highest specificity)
        if entities.law_name:
            law_upper = entities.law_name.upper()
            phrases.append(f"статья {num} {law_upper}")
            phrases.append(f"ст {num} {law_upper}")
        # Article-only phrases
        phrases.append(f"статья {num}")
        phrases.append(f"ст {num}")

    for cn in entities.contract_numbers:
        phrases.append(f"договор {cn}")

    for inn in entities.inn_values:
        phrases.append(f"инн {inn}")

    # Deduplicate
    seen: set[str] = set()
    result: list[str] = []
    for p in phrases:
        key = p.lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(p)
    return result


def _phrase_search(
    phrases: list[str],
    user_id: int,
    document_ids: list[int] | None,
    top_k: int,
) -> list[dict]:
    """FTS phrase match using phraseto_tsquery."""
    if not phrases:
        return []
    try:
        from app.services.exact_match import phrase_match_search

        return phrase_match_search(phrases, user_id, document_ids, top_k)
    except Exception:
        logger.exception("Phrase match search failed")
        return []


# ---------------------------------------------------------------------------
# Layer 3: Semantic search (Qdrant)
# ---------------------------------------------------------------------------


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
        payload = result.get("payload") or {}
        if not payload:
            continue
        score = result["score"]
        text = payload.get("text") or ""
        chunks.append(
            RetrievedChunk(
                source=SourceRef(
                    document_id=payload.get("document_id") or 0,
                    filename=payload.get("original_filename") or "",
                    chunk_index=payload.get("chunk_index") or 0,
                    score=score,
                    text=text[:1000],
                ),
                score=score,
                text=text,
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# Layer 4: Keyword / FTS (OR ts_rank with coverage weighting)
# ---------------------------------------------------------------------------


def _keyword_search(
    question: str,
    user_id: int,
    document_ids: list[int] | None,
    top_k: int,
) -> list[dict]:
    """Rank chunks by PostgreSQL full-text search (ts_rank) with OR semantics."""
    try:
        db = SessionLocal()
        try:
            tsv = (
                f"to_tsvector('{FTS_CONFIG}', "
                "regexp_replace(d.original_filename, "
                "'[^a-zA-Z0-9а-яА-ЯёЁ]', ' ', 'g') || ' ' || dc.text)"
            )
            sql = (
                "SELECT dc.document_id AS document_id, dc.chunk_index AS chunk_index, "
                "dc.text AS text, d.original_filename AS filename, "
                f"ts_rank({tsv}, q.query) AS rank, "
                "q.total_lexemes AS total_lexemes, "
                f"(SELECT count(*) FROM unnest({tsv}) AS chunk_lex "
                "WHERE chunk_lex.lexeme = ANY(q.lexemes)) AS matched_lexemes "
                "FROM document_chunks dc "
                "JOIN documents d ON d.id = dc.document_id "
                "CROSS JOIN ("
                "  SELECT to_tsquery('" + FTS_CONFIG + "', "
                "    string_agg(lexeme, ' | ')) AS query, "
                "    array_agg(lexeme) AS lexemes, count(*) AS total_lexemes "
                "  FROM unnest(to_tsvector('" + FTS_CONFIG + "', :question))"
                ") q "
                "WHERE d.user_id = :user_id AND ("
                "to_tsvector('" + FTS_CONFIG + "', dc.text) @@ q.query OR "
                + tsv + " @@ q.query)"
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


# ---------------------------------------------------------------------------
# Merge: combine all layers, dedup, boost exact matches
# ---------------------------------------------------------------------------


def _merge_results(
    semantic_chunks: list[RetrievedChunk],
    keyword_rows: list[dict],
    exact_rows: list[dict],
    phrase_rows: list[dict],
    entities: QueryEntities,
    top_k: int,
    min_score: float,
) -> list[RetrievedChunk]:
    """Merge results from all four layers with entity-aware boosting.

    Scoring priority (highest to lowest):
    1. Exact ILIKE match (score from exact_match.py, boosted)
    2. FTS phrase match (phraseto_tsquery, 0.9 base)
    3. Keyword FTS (OR ts_rank, coverage-weighted, 0-1)
    4. Semantic cosine (Qdrant, 0-1)

    When a chunk is found by multiple layers, its score is boosted:
    - found by exact + semantic: score = max(exact_score * 1.15, semantic_score)
    - found by exact only: score = exact_score (already high)
    - found by semantic + keyword: score = max(semantic_score, kw_score)
    """
    merged: dict[tuple[int, int], RetrievedChunk] = {}
    # Track which layers found each chunk.
    layer_hits: dict[tuple[int, int], set[str]] = {}

    def _key(row: dict) -> tuple[int, int]:
        return (row["document_id"], row["chunk_index"])

    def _add_or_boost(key: tuple[int, int], chunk: RetrievedChunk, layer: str) -> None:
        existing = merged.get(key)
        existing_layers = layer_hits.get(key, set())
        if existing is None:
            merged[key] = chunk
            layer_hits[key] = {layer}
        else:
            # Boost when a chunk is found by multiple layers.
            new_layers = existing_layers | {layer}
            layer_hits[key] = new_layers
            if len(new_layers) >= 3:
                # Found by 3+ layers: strong boost, cap at 1.0
                boosted = min(1.0, max(existing.score, chunk.score) * 1.2)
            elif len(new_layers) >= 2:
                # Found by 2 layers: moderate boost
                boosted = min(1.0, max(existing.score, chunk.score) * 1.1)
            else:
                boosted = max(existing.score, chunk.score)
            if boosted > existing.score:
                merged[key] = RetrievedChunk(
                    source=SourceRef(
                        document_id=existing.source.document_id,
                        filename=existing.source.filename,
                        chunk_index=existing.source.chunk_index,
                        score=boosted,
                        text=existing.source.text,
                    ),
                    score=boosted,
                    text=existing.text,
                )

    # --- Layer 1: Exact ILIKE matches (highest priority) ---
    for row in exact_rows:
        key = _key(row)
        score = row.get("match_score", 0.8)
        # Boost: exact matches get a 15% bonus but cap at 1.0.
        boosted_score = min(1.0, score * 1.15)
        chunk = RetrievedChunk(
            source=SourceRef(
                document_id=row["document_id"],
                filename=row["filename"],
                chunk_index=row["chunk_index"],
                score=boosted_score,
                text=row["text"][:1000],
            ),
            score=boosted_score,
            text=row["text"],
        )
        _add_or_boost(key, chunk, "exact")

    # --- Layer 2: FTS phrase match ---
    for row in phrase_rows:
        key = _key(row)
        score = row.get("match_score", 0.9)
        chunk = RetrievedChunk(
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
        _add_or_boost(key, chunk, "phrase")

    # --- Layer 3: Keyword FTS (OR ts_rank, coverage-weighted) ---
    kw_scores: dict[tuple[int, int], float] = {}
    max_rank = max((row["rank"] for row in keyword_rows), default=0.0)
    if max_rank > 0:
        for row in keyword_rows:
            key = _key(row)
            total = row.get("total_lexemes") or 0
            matched = row.get("matched_lexemes") or 0
            coverage = (matched / total) if total else 0.0
            kw_scores[key] = (row["rank"] / max_rank) * coverage

    for row in keyword_rows:
        key = _key(row)
        score = kw_scores.get(key, 0.0)
        if score <= 0:
            continue
        chunk = RetrievedChunk(
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
        _add_or_boost(key, chunk, "keyword")

    # --- Layer 4: Semantic (Qdrant cosine) ---
    for chunk in semantic_chunks:
        key = (chunk.source.document_id, chunk.source.chunk_index)
        _add_or_boost(key, chunk, "semantic")

    # Sort by final score, filter by min_score, cap at top_k.
    results = sorted(merged.values(), key=lambda c: c.score, reverse=True)
    results = [c for c in results if c.score >= min_score]
    return results[:top_k]


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------


def _rerank(question: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Re-order hybrid candidates with the cross-encoder reranker."""
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


# ---------------------------------------------------------------------------
# Law context validation for legal article queries
# ---------------------------------------------------------------------------

# Characters to look backwards in the document text when validating law context.
# This is enough to capture the law title that typically precedes articles.
_CONTEXT_WINDOW_CHARS = 2000


def _validate_law_context(
    chunks: list[RetrievedChunk],
    law_name: str,
    article_number: str,
    user_id: int,
    top_k: int,
) -> list[RetrievedChunk]:
    """Boost/penalise chunks based on whether the requested law context is nearby.

    For legal article queries like "статья 3 УК РФ" the retrieval layers may
    return chunks from ANY law that has a "Статья 3".  This function validates
    that the requested law's keywords actually appear in the document text
    surrounding each candidate chunk.

    Strategy:
    1. Collect unique document_ids from the candidates.
    2. For each document, load its full text from PostgreSQL once.
    3. For each candidate chunk, find its position in the document text and
       search backwards for law-name keywords within a context window.
    4. Boost chunks that have the right law context; heavily penalise those
       that don't (they are likely from a different law).
    5. Re-sort by adjusted score and return the top_k.

    If no chunks pass the validation (e.g. the document doesn't contain the
    requested law at all), the original chunks are returned unmodified so the
    agent can still answer "not found" based on the raw search results.
    """
    if not chunks or not law_name:
        return chunks

    law_keywords = get_law_keywords(law_name)
    if not law_keywords:
        return chunks

    # Normalise to lowercase for case-insensitive matching
    law_kw_lower = [kw.lower() for kw in law_keywords]

    # Collect unique document_ids
    doc_ids = list({c.source.document_id for c in chunks})
    if not doc_ids:
        return chunks

    # Load full document texts from DB (one query per unique doc)
    doc_texts: dict[int, str] = {}
    try:
        from app.database.session import SessionLocal
        from app.models.document import Document

        db = SessionLocal()
        try:
            rows = (
                db.query(Document.id, Document.content)
                .filter(Document.id.in_(doc_ids), Document.user_id == user_id)
                .all()
            )
            doc_texts = {row.id: row.content or "" for row in rows}
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to load document texts for law validation")
        return chunks

    validated: list[RetrievedChunk] = []
    for chunk in chunks:
        doc_text = doc_texts.get(chunk.source.document_id, "")
        if not doc_text:
            # Can't validate — keep the chunk
            validated.append(chunk)
            continue

        # Find the chunk text in the document to determine its position
        chunk_start = doc_text.find(chunk.text[:200])
        if chunk_start < 0:
            # Fuzzy match: try a shorter prefix
            chunk_start = doc_text.find(chunk.text[:80])
        if chunk_start < 0:
            # Can't locate — keep the chunk (don't penalise)
            validated.append(chunk)
            continue

        # Search backwards for law keywords within the context window
        context_start = max(0, chunk_start - _CONTEXT_WINDOW_CHARS)
        context_window = doc_text[context_start:chunk_start].lower()

        has_law_context = any(kw in context_window for kw in law_kw_lower)

        if has_law_context:
            # Boost: this chunk is confirmed to belong to the requested law
            boosted_score = min(1.0, chunk.score * 1.25)
            validated.append(
                RetrievedChunk(
                    source=SourceRef(
                        document_id=chunk.source.document_id,
                        filename=chunk.source.filename,
                        chunk_index=chunk.source.chunk_index,
                        score=boosted_score,
                        text=chunk.source.text,
                    ),
                    score=boosted_score,
                    text=chunk.text,
                )
            )
        else:
            # Penalise: no law context found — likely from a different law
            penalised_score = chunk.score * 0.3
            if penalised_score >= 0.05:  # keep but with low score
                validated.append(
                    RetrievedChunk(
                        source=SourceRef(
                            document_id=chunk.source.document_id,
                            filename=chunk.source.filename,
                            chunk_index=chunk.source.chunk_index,
                            score=penalised_score,
                            text=chunk.source.text,
                        ),
                        score=penalised_score,
                        text=chunk.text,
                    )
                )
            # If penalised below threshold, drop the chunk entirely

    # Re-sort by adjusted score
    validated.sort(key=lambda c: c.score, reverse=True)

    # Check if any chunks survived validation with reasonable scores
    has_good = any(c.score >= min_score for c in validated)
    if has_good:
        return validated[:top_k]

    # No chunks with law context found — return original results so the agent
    # can still answer "not found in your documents"
    return chunks


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def retrieve_context(
    question: str,
    user_id: int,
    document_id: int | list[int] | None = None,
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[RetrievedChunk]:
    """Run all four retrieval layers and merge the results.

    Layers run in parallel:
    1. Exact ILIKE match for extracted entities
    2. FTS phrase match (phraseto_tsquery)
    3. Keyword FTS (OR ts_rank with coverage)
    4. Semantic cosine (Qdrant)

    Results are merged with dedup by (document_id, chunk_index) and
    entity-aware boosting: chunks found by multiple layers get a score
    bonus. When the reranker is enabled the merged candidates are re-scored
    by a cross-encoder and cut down to ``top_k``.

    Returns an empty list when nothing was found.
    """
    document_ids = _as_document_ids(document_id)
    candidate_k = top_k
    rerank_enabled = settings.RERANKER_ENABLED
    if rerank_enabled:
        candidate_k = max(top_k, settings.RERANKER_CANDIDATES)

    # Extract entities from the query for exact-match layers.
    entities = extract_entities(question)
    exact_patterns = _build_exact_patterns(entities)
    phrase_queries = _build_phrase_queries(entities)

    # Run all four layers in parallel.
    semantic_future = _executor.submit(
        _semantic_search, question, user_id, document_ids, candidate_k
    )
    keyword_future = _executor.submit(
        _keyword_search, question, user_id, document_ids, candidate_k
    )
    exact_future = _executor.submit(
        _exact_search, exact_patterns, user_id, document_ids, candidate_k
    )
    phrase_future = _executor.submit(
        _phrase_search, phrase_queries, user_id, document_ids, candidate_k
    )

    semantic_chunks = semantic_future.result()
    keyword_rows = keyword_future.result()
    exact_rows = exact_future.result()
    phrase_rows = phrase_future.result()

    merged = _merge_results(
        semantic_chunks, keyword_rows, exact_rows, phrase_rows,
        entities, candidate_k, min_score,
    )

    if rerank_enabled and merged:
        try:
            merged = _rerank(question, merged, candidate_k)
        except Exception:
            logger.exception("Reranker failed; falling back to hybrid order")

    # --- Law context validation for legal article queries ---
    # When the query specifies both an article number AND a law name
    # (e.g. "статья 3 УК РФ"), validate that each candidate chunk actually
    # belongs to the requested law by checking the document text surrounding
    # the chunk for law-name keywords.  This prevents returning "Статья 3
    # Конституции" when the user asked for "Статья 3 УК РФ".
    if (
        merged
        and entities.article_numbers
        and entities.law_name
    ):
        merged = _validate_law_context(
            merged, entities.law_name, entities.article_numbers[0],
            user_id, top_k,
        )

    return merged[:top_k]
