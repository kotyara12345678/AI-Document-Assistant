"""Tests for hybrid retrieval: semantic (Qdrant) + keyword (PostgreSQL FTS).

Runs in-process against the real PostgreSQL and Qdrant via FastAPI TestClient
for uploads, then calls the retrieval service directly.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import retrieval

API_PREFIX = "/api"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_qdrant():
    from app.core.config import settings
    from app.vector.client import get_qdrant_client

    qclient = get_qdrant_client()
    try:
        qclient.delete_collection(settings.QDRANT_COLLECTION)
    except Exception:
        pass
    yield
    try:
        qclient.delete_collection(settings.QDRANT_COLLECTION)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_db():
    """Isolate each test from documents/chat rows left by previous runs."""
    from app.database.session import SessionLocal
    from app.models.chat import Chat
    from app.models.chat_message import ChatMessage, ChatSummary
    from app.models.document import Document

    db = SessionLocal()
    try:
        db.query(ChatSummary).delete()
        db.query(ChatMessage).delete()
        db.query(Chat).delete()
        db.query(Document).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(ChatSummary).delete()
        db.query(ChatMessage).delete()
        db.query(Chat).delete()
        db.query(Document).delete()
        db.commit()
    finally:
        db.close()


def _upload(client: TestClient, filename: str, content: bytes):
    return client.post(f"{API_PREFIX}/documents/upload", files={"file": (filename, content)})


def test_keyword_finds_document_when_semantic_is_similar(client):
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
    target_id = resp.json()["id"]
    assert _upload(client, "distractor.txt", distractor_text.encode("utf-8")).status_code == 201

    chunks = retrieval.retrieve_context(
        question=f"криостат XQ-77 {marker}",
        user_id=1,
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


def test_hybrid_merge_dedups_and_bounds_scores(client):
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
        user_id=1,
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


def test_retrieve_context_returns_empty_when_nothing_matches(client):
    """Random non-existent lexemes must yield an empty list, not an error."""
    noise = f"zxqwvx{uuid.uuid4().hex[:8]}"
    chunks = retrieval.retrieve_context(question=noise, user_id=1, top_k=5, min_score=0.3)
    assert chunks == []
