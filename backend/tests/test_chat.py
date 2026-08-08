"""Tests for the RAG /api/chat flow with a mocked Gemini client.

Runs in-process via FastAPI TestClient against the real PostgreSQL and Qdrant,
so the Gemini mock (monkeypatch) actually takes effect on the server stack.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import gemini

API_PREFIX = "/api"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _upload(client: TestClient, filename: str, content: bytes):
    files = {"file": (filename, content)}
    return client.post(f"{API_PREFIX}/documents/upload", files=files)


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


@pytest.fixture()
def fake_gemini(monkeypatch):
    """Replace the real Gemini call with a stub that records the prompt."""

    calls = {}

    def fake_generate_answer(prompt, system_instruction=None, client=None, api_key=None):
        calls["prompt"] = prompt
        calls["system_instruction"] = system_instruction
        return "Ответ по документам: бюджет составляет 900000 рублей."

    monkeypatch.setattr(gemini, "generate_answer", fake_generate_answer)
    yield calls


def test_chat_returns_answer_with_sources(client, fake_gemini):
    marker = f"CHT{uuid.uuid4().hex[:6]}"
    text = (
        f"План продаж квадрокоптеров {marker}. "
        "Рекламный бюджет составляет 900000 рублей в квартал. "
        "Из них на контекстную рекламу выделено 300000 рублей. "
    ) * 20
    resp = _upload(client, "chat_doc.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text
    document_id = resp.json()["id"]

    chat_resp = client.post(
        f"{API_PREFIX}/chat",
        json={"question": f"какой рекламный бюджет у квадрокоптеров {marker}"},
    )
    assert chat_resp.status_code == 200, chat_resp.text
    data = chat_resp.json()

    assert data["answer"] == "Ответ по документам: бюджет составляет 900000 рублей."
    assert data["sources"], "Expected sources in chat response"

    top = data["sources"][0]
    assert top["document_id"] == document_id
    assert top["filename"] == "chat_doc.txt"
    assert top["chunk_index"] >= 0
    assert top["score"] > 0.3
    assert marker in top["text"] or "900000" in top["text"]

    # Gemini received a prompt built from real retrieved context.
    assert "CONTEXT:" in fake_gemini["prompt"]
    assert marker in fake_gemini["prompt"]


def test_chat_with_document_filter(client, fake_gemini):
    marker_a = f"DOCA{uuid.uuid4().hex[:6]}"
    text_a = (
        f"Документ про корабли {marker_a}. Парусные яхты стоят 1000000 рублей."
    ) * 20
    resp_a = _upload(client, "ships.txt", text_a.encode("utf-8"))
    doc_a = resp_a.json()["id"]

    text_b = (
        f"Документ про велосипеды. Горные велосипеды стоят 50000 рублей."
    ) * 20
    resp_b = _upload(client, "bikes.txt", text_b.encode("utf-8"))

    chat_resp = client.post(
        f"{API_PREFIX}/chat",
        json={"question": f"сколько стоят парусные яхты {marker_a}", "document_id": doc_a},
    )
    assert chat_resp.status_code == 200, chat_resp.text
    data = chat_resp.json()
    assert data["sources"], "Expected sources"
    for source in data["sources"]:
        assert source["document_id"] == doc_a, "Chat must only use the filtered document"


def test_chat_returns_honest_answer_when_no_context(client, fake_gemini):
    chat_resp = client.post(
        f"{API_PREFIX}/chat",
        json={"question": "какой цвет самого редкого марсианского кактуса"},
    )
    assert chat_resp.status_code == 200, chat_resp.text
    data = chat_resp.json()
    assert data["sources"] == []
    assert "could not find" in data["answer"].lower() or "не нашел" in data["answer"].lower()


def test_chat_degraded_when_gemini_fails(client, monkeypatch):
    marker = f"FAIL{uuid.uuid4().hex[:6]}"
    text = (
        f"Контент для проверки деградации {marker}. "
        "Здесь говорится про роботов и автоматизацию. "
    ) * 20
    resp = _upload(client, "degrade.txt", text.encode("utf-8"))
    assert resp.status_code == 201
    document_id = resp.json()["id"]

    def failing_gemini(prompt, system_instruction=None, client=None, api_key=None):
        raise gemini.GeminiError("boom")

    monkeypatch.setattr(gemini, "generate_answer", failing_gemini)

    chat_resp = client.post(
        f"{API_PREFIX}/chat",
        json={"question": f"что тут про роботов {marker}"},
    )
    assert chat_resp.status_code == 200, chat_resp.text
    data = chat_resp.json()
    assert "Gemini unavailable" in data["answer"]
    assert data["sources"], "Fallback answer should still expose sources"
