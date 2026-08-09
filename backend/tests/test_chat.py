"""Tests for the RAG /api/chat flow with a mocked Gemini client.

Runs in-process via FastAPI TestClient against the real PostgreSQL and Qdrant,
so the Gemini mock (monkeypatch) actually takes effect on the server stack.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.services import gemini

API_PREFIX = "/api"


def _upload(client: TestClient, filename: str, content: bytes):
    files = {"file": (filename, content)}
    return client.post(f"{API_PREFIX}/documents/upload", files=files)


@pytest.fixture()
def fake_gemini(monkeypatch):
    """Replace the real Gemini call with a stub that records the prompt."""

    calls = {}

    def fake_generate_answer(
        prompt, system_instruction=None, client=None, history=None, summary=None
    ):
        calls["prompt"] = prompt
        calls["system_instruction"] = system_instruction
        calls["history"] = history
        calls["summary"] = summary
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

    def failing_gemini(prompt, system_instruction=None, client=None, history=None, summary=None):
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


def test_chat_history_is_passed_to_gemini(client, fake_gemini):
    """Each turn must reach Gemini with the recent history as context."""
    marker = f"HIST{uuid.uuid4().hex[:6]}"
    text = (
        f"База знаний по маркетингу {marker}. "
        "Бюджет на контекстную рекламу 300000 рублей. "
    ) * 20
    _upload(client, "history_doc.txt", text.encode("utf-8"))

    client.post(f"{API_PREFIX}/chat", json={"question": f"что в документе {marker}"})
    second = client.post(
        f"{API_PREFIX}/chat",
        json={"question": f"а сколько на контекстную рекламу {marker}"},
    )
    assert second.status_code == 200, second.text

    # The second turn must have received the first user/assistant pair.
    assert fake_gemini["history"], "Expected chat history in the LLM request"
    roles = [m["role"] for m in fake_gemini["history"]]
    assert roles == ["user", "assistant"]
    assert marker in fake_gemini["history"][0]["content"]


def test_history_rolling_summary(client, monkeypatch):
    """After the summary threshold is crossed, older turns are folded into a summary."""
    marker = f"SUMM{uuid.uuid4().hex[:6]}"
    text = (
        f"Данные о продажах {marker}. Рост выручки составил 15%. "
    ) * 20
    _upload(client, "summary_doc.txt", text.encode("utf-8"))

    calls = []

    def summarizing_gemini(
        prompt, system_instruction=None, client=None, history=None, summary=None
    ):
        calls.append(
            {"prompt": prompt, "system_instruction": system_instruction,
             "history": history, "summary": summary}
        )
        return "Ответ про продажи."

    monkeypatch.setattr(gemini, "generate_answer", summarizing_gemini)

    # Force a low threshold so the summary kicks in within a handful of turns.
    from app.services import chat as chat_service
    from app.core.config import settings

    monkeypatch.setattr(settings, "CHAT_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "CHAT_HISTORY_MESSAGES", 2)

    for i in range(6):
        resp = client.post(f"{API_PREFIX}/chat", json={"question": f"вопрос номер {i} {marker}"})
        assert resp.status_code == 200, resp.text

    # At least one request must have triggered summary generation: the final
    # LLM call either carries a non-None summary, or a summary call happened.
    summary_calls = [c for c in calls if c["system_instruction"] == settings.CHAT_SUMMARY_INSTRUCTION]
    assert summary_calls, "Expected at least one summary generation call"
    last_summary = summary_calls[-1]
    assert last_summary["prompt"], "Summary prompt should not be empty"
    assert "опрос номер" in last_summary["prompt"] or marker in last_summary["prompt"]

    # The most recent answer call should either use the summary or a bounded history.
    answer_calls = [c for c in calls if c["system_instruction"] != settings.CHAT_SUMMARY_INSTRUCTION]
    assert answer_calls
    last = answer_calls[-1]
    assert last["summary"] is not None or len(last["history"] or []) <= 2


def test_ai_prefix_uses_plain_gigachat_without_rag(client, monkeypatch):
    """Messages starting with @ai bypass RAG retrieval and keep sources empty."""
    marker = f"AIPR{uuid.uuid4().hex[:6]}"
    text = (
        f"Документ только для RAG {marker}. Бюджет 999999 рублей."
    ) * 20
    _upload(client, "ai_prefix_doc.txt", text.encode("utf-8"))

    calls = []

    def recording_gemini(
        prompt, system_instruction=None, client=None, history=None, summary=None
    ):
        calls.append({"prompt": prompt, "history": history})
        return "Прямой ответ без документов."

    monkeypatch.setattr(gemini, "generate_answer", recording_gemini)

    resp = client.post(
        f"{API_PREFIX}/chat",
        json={"question": f"@ai {marker} расскажи анекдот"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["answer"] == "Прямой ответ без документов."
    assert data["sources"] == [], "@ai mode must not return RAG sources"

    # The prompt must be the raw question (without @ai and without RAG context).
    assert calls, "GigaChat should have been called"
    assert calls[0]["prompt"] == f"{marker} расскажи анекдот"
    assert "CONTEXT:" not in calls[0]["prompt"]


def test_rag_still_runs_for_non_ai_messages(client, fake_gemini):
    """Messages without @ai keep the RAG behaviour untouched."""
    marker = f"RAGK{uuid.uuid4().hex[:6]}"
    text = (
        f"База знаний о транспорте {marker}. Грузовики стоят 2 миллиона."
    ) * 20
    _upload(client, "rag_still.txt", text.encode("utf-8"))

    resp = client.post(
        f"{API_PREFIX}/chat",
        json={"question": f"сколько стоят грузовики {marker}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sources"], "RAG mode must still return sources"
    assert "CONTEXT:" in fake_gemini["prompt"]


def test_chats_crud_and_autotitle(client, fake_gemini):
    """Create/list/messages/delete of chats + title from the first question."""
    marker = f"CRUD{uuid.uuid4().hex[:6]}"
    text = (f"Документ {marker}. Бюджет проекта 300000 рублей.") * 20
    _upload(client, "crud_doc.txt", text.encode("utf-8"))

    created = client.post(f"{API_PREFIX}/chats", json={"title": None})
    assert created.status_code == 201, created.text
    chat_id = created.json()["id"]

    listed = client.get(f"{API_PREFIX}/chats").json()
    assert any(c["id"] == chat_id for c in listed)
    assert listed[0]["title"] == "Новый чат"

    assert client.get(f"{API_PREFIX}/chats/{chat_id}/messages").json() == []

    # First question names the chat.
    question = f"какой бюджет проекта {marker}"
    chat_resp = client.post(
        f"{API_PREFIX}/chat",
        json={"chat_id": chat_id, "question": question},
    )
    assert chat_resp.status_code == 200, chat_resp.text
    assert chat_resp.json()["chat_id"] == chat_id

    listed = client.get(f"{API_PREFIX}/chats").json()
    target = next(c for c in listed if c["id"] == chat_id)
    assert target["title"] == question

    # Both turns persisted in the chat's message list.
    msgs = client.get(f"{API_PREFIX}/chats/{chat_id}/messages").json()
    assert len(msgs) == 2
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert all(m["chat_id"] == chat_id for m in msgs)

    # Deleting the chat removes it and its messages.
    del_resp = client.delete(f"{API_PREFIX}/chats/{chat_id}")
    assert del_resp.status_code == 200
    listed = client.get(f"{API_PREFIX}/chats").json()
    assert all(c["id"] != chat_id for c in listed)
    assert client.get(f"{API_PREFIX}/chats/{chat_id}/messages").status_code == 404


def test_chat_history_scoped_to_chat(client, fake_gemini):
    """Messages in one chat never leak into another chat's history."""
    marker = f"SCOP{uuid.uuid4().hex[:6]}"
    text = (f"Документ про корабли {marker}. Стоимость яхты 1000000 рублей.") * 20
    _upload(client, "scope_doc.txt", text.encode("utf-8"))

    chat_a = client.post(f"{API_PREFIX}/chats", json={}).json()["id"]
    chat_b = client.post(f"{API_PREFIX}/chats", json={}).json()["id"]

    resp = client.post(
        f"{API_PREFIX}/chat",
        json={"chat_id": chat_a, "question": f"сколько стоит яхта {marker}"},
    )
    assert resp.status_code == 200, resp.text

    msgs_a = client.get(f"{API_PREFIX}/chats/{chat_a}/messages").json()
    msgs_b = client.get(f"{API_PREFIX}/chats/{chat_b}/messages").json()
    assert len(msgs_a) == 2
    assert msgs_b == [], "Another chat must not see this conversation"

    # The first chat only receives its own history as LLM context.
    resp = client.post(
        f"{API_PREFIX}/chat",
        json={"chat_id": chat_a, "question": f"а сколько парусная лодка {marker}"},
    )
    assert resp.status_code == 200, resp.text
    roles = [m["role"] for m in fake_gemini["history"]]
    assert roles == ["user", "assistant"]
    assert marker in fake_gemini["history"][0]["content"]


