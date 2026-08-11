"""Tests for hybrid retrieval: semantic (Qdrant) + keyword (PostgreSQL FTS).

Runs in-process against the real PostgreSQL and Qdrant via FastAPI TestClient
for uploads, then calls the retrieval service directly.
"""

import uuid

from fastapi.testclient import TestClient

from app.core.config import settings
from app.services import retrieval
from app.services import reranker as reranker_service

API_PREFIX = "/api"


def _upload(client: TestClient, filename: str, content: bytes):
    return client.post(f"{API_PREFIX}/documents/upload", files={"file": (filename, content)})


def test_keyword_finds_document_when_semantic_is_similar(client, user_id):
    """A unique keyword must surface a document even when another one is
    semantically closer to the question. The top chunk's score comes from the
    keyword-normalized rank (regression: SourceRef.score must reflect the merge)."""
    marker = f"KWRD{uuid.uuid4().hex[:6]}"
    target_text = (
        f"Криостат XQ-77 {marker}. Рабочая температура 50 милликельвин. "
    ) * 20
    distractor_text = (
        "Криостат модели ZX-12. Рабочая температура 50 милликельвин, "
        "диапазон 4-100 кельвин. "
    ) * 20

    resp = _upload(client, "target.txt", target_text.encode("utf-8"))
    assert resp.status_code == 201, resp.text
    target_id = resp.json()[0]["id"]
    assert _upload(client, "distractor.txt", distractor_text.encode("utf-8")).status_code == 201

    chunks = retrieval.retrieve_context(
        question=f"криостат XQ-77 {marker}",
        user_id=user_id,
        top_k=5,
        min_score=0.3,
    )
    assert chunks, "Expected at least one retrieved chunk"
    top = chunks[0]
    assert top.source.document_id == target_id, "Keyword-only unique term must rank the target first"
    assert marker in top.text
    # Keyword max-normalization gives the top ts_rank hit score 1.0.
    assert top.source.score >= 0.99, f"SourceRef.score must carry the keyword boost, got {top.source.score}"
    assert top.score == top.source.score


def test_hybrid_merge_dedups_and_bounds_scores(client, user_id):
    """A chunk found by both retrievers appears once and its merged score is
    the higher of the two and stays within [0, 1]."""
    marker = f"MRGE{uuid.uuid4().hex[:6]}"
    text = (
        f"Финансовый отчёт по проекту Атлант {marker}. "
        "Бюджет составляет 2.5 миллиона рублей. Дедлайн декабрь 2026. "
    ) * 20
    resp = _upload(client, "merge_doc.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text

    chunks = retrieval.retrieve_context(
        question=f"какой бюджет у проекта Атлант {marker}",
        user_id=user_id,
        top_k=10,
        min_score=0.0,
    )
    assert chunks, "Expected retrieved chunks"

    keys = [(c.source.document_id, c.source.chunk_index) for c in chunks]
    assert len(keys) == len(set(keys)), "A chunk found by both retrievers must appear only once"
    for chunk in chunks:
        assert 0.0 <= chunk.source.score <= 1.0, f"Score out of range: {chunk.source.score}"
        assert chunk.source.score == chunk.score
        assert chunk.source.filename == "merge_doc.txt"


def test_retrieve_context_returns_empty_when_nothing_matches(client, user_id):
    """Random non-existent lexemes must yield an empty list, not an error."""
    noise = f"zxqwvx{uuid.uuid4().hex[:8]}"
    chunks = retrieval.retrieve_context(question=noise, user_id=user_id, top_k=5, min_score=0.3)
    assert chunks == []


def test_reranker_reorders_candidates_and_cuts_to_top_k(client, user_id, monkeypatch):
    """With the reranker enabled, hybrid produces a wider pool and the
    cross-encoder scores decide the final order/size, without loading the real
    model (compute_scores is stubbed)."""
    marker_a = f"RRA{uuid.uuid4().hex[:8]}"
    marker_b = f"RRB{uuid.uuid4().hex[:8]}"
    text_a = (f"Криогенная установка {marker_a}. Температура 50 мК. ") * 20
    text_b = (f"Криогенная установка {marker_b}. Температура 120 мК. ") * 20

    resp_a = _upload(client, "rerank_a.txt", text_a.encode("utf-8"))
    assert resp_a.status_code == 201, resp_a.text
    assert _upload(client, "rerank_b.txt", text_b.encode("utf-8")).status_code == 201

    question = f"криогенная установка {marker_a} {marker_b}"

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_CANDIDATES", 30)

    def fake_compute_scores(q, texts):
        # Prefer chunks that mention marker_b regardless of pool position.
        return [0.99 if marker_b in t else 0.15 for t in texts]

    monkeypatch.setattr(reranker_service, "compute_scores", fake_compute_scores)

    chunks = retrieval.retrieve_context(question=question, user_id=user_id, top_k=2, min_score=0.3)
    assert chunks, "Expected reranked chunks"
    assert len(chunks) <= 2
    assert marker_b in chunks[0].text, "Reranker must move the preferred candidate to the top"
    assert abs(chunks[0].score - 0.99) < 1e-6
    assert chunks[0].score == chunks[0].source.score
    for chunk in chunks:
        assert 0.0 <= chunk.score <= 1.0


def test_reranker_falls_back_to_hybrid_order_on_error(client, user_id, monkeypatch):
    """A failing reranker must not break the request: results fall back to the
    plain hybrid order."""
    marker = f"FALL{uuid.uuid4().hex[:8]}"
    text = (f"Документ про теплообменники {marker}. Расход 500 литров. ") * 20
    resp = _upload(client, "fallback.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text

    question = f"что про теплообменники {marker}"
    expected = retrieval.retrieve_context(question=question, user_id=user_id, top_k=5, min_score=0.3)
    assert expected, "Sanity: doc must be findable without the reranker"

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)

    def broken_compute_scores(question, texts):
        raise RuntimeError("model offline")

    monkeypatch.setattr(reranker_service, "compute_scores", broken_compute_scores)

    chunks = retrieval.retrieve_context(question=question, user_id=user_id, top_k=5, min_score=0.3)
    assert chunks, "Fallback must still return results"
    keys = [(c.source.document_id, c.source.chunk_index) for c in chunks]
    assert len(keys) == len(set(keys))


def test_retrieve_context_restricts_to_selected_documents(client, user_id):
    """Several document ids must restrict retrieval to exactly those documents,
    merging results from all of them and never leaking a third one in."""
    marker_a = f"MLTA{uuid.uuid4().hex[:6]}"
    marker_b = f"MLTB{uuid.uuid4().hex[:6]}"
    text_a = (f"Наноматериалы {marker_a}. Графен, электропроводность. ") * 20
    text_b = (f"Наноматериалы {marker_b}. Углеродные трубки. ") * 20
    decoy = (f"Наноматериалы LEAK{uuid.uuid4().hex[:6]}. Керамика. ") * 20

    id_a = _upload(client, "multi_a.txt", text_a.encode("utf-8")).json()[0]["id"]
    id_b = _upload(client, "multi_b.txt", text_b.encode("utf-8")).json()[0]["id"]
    id_c = _upload(client, "multi_c.txt", decoy.encode("utf-8")).json()[0]["id"]

    chunks = retrieval.retrieve_context(
        question=f"наноматериалы {marker_a} {marker_b}",
        user_id=user_id,
        document_id=[id_a, id_b],
        top_k=20,
        min_score=0.0,
    )
    assert chunks, "Expected results from the selected documents"
    doc_ids = {c.source.document_id for c in chunks}
    assert {id_a, id_b} <= doc_ids, f"Both selected docs must appear, got {doc_ids}"
    assert id_c not in doc_ids, "A document outside the selection must not leak in"
