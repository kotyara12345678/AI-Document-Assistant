"""Tests for metadata-aware RAG: the LLM decides when document metadata is
used. Every /api/chat turn is answered through a mocked Gemini that both acts
as the metadata classifier and the answer generator (the classifier runs the
same generate_answer interface), so both scenarios are exercised end-to-end:
questions where metadata must be skipped and questions where it is used,
including retrieval narrowed to a named document.
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.services import gemini

API_PREFIX = "/api"
CLASSIFIER_INS = settings.CHAT_METADATA_CLASSIFIER_INSTRUCTION


def _upload(client: TestClient, filename: str, content: bytes):
    return client.post(f"{API_PREFIX}/documents/upload", files={"file": (filename, content)})


def _metadata_gemini(decision: str, answer: str):
    """Build a generate_answer stub: classifier returns `decision` JSON, all
    other calls return `answer` and record the answer prompt."""
    captured: dict = {}

    def fake(prompt, system_instruction=None, client=None, history=None, summary=None):
        if system_instruction == CLASSIFIER_INS:
            return decision
        captured["prompt"] = prompt
        return answer

    return fake, captured


def test_content_question_passes_no_metadata(client, monkeypatch):
    """'что написано в документе' must NOT leak metadata into the prompt."""
    marker = f"MTNO{uuid.uuid4().hex[:6]}"
    text = (f"Текст для вопросов без метаданных {marker}. Прогноз продаж 12345 рублей. ") * 20
    resp = _upload(client, "plain_q.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text

    fake, captured = _metadata_gemini(
        '{"needs_metadata": false, "fields": [], "target_filename": null}',
        "Ответ без метаданных.",
    )
    monkeypatch.setattr(gemini, "generate_answer", fake)

    chat = client.post(f"{API_PREFIX}/chat", json={"question": f"что написано в документе {marker}"})
    assert chat.status_code == 200, chat.text
    assert chat.json()["sources"], "content question still returns sources"

    prompt = captured["prompt"]
    assert marker in prompt
    assert "CONTEXT:" in prompt
    # No metadata headers in the LLM prompt.
    for forbidden in ("filename=", "created_at", "file_size", "content_length", "[Document"):
        assert forbidden not in prompt, f"prompt must not contain metadata header: {forbidden}"


def test_question_with_metadata_gets_only_requested_fields(client, monkeypatch):
    """'когда загружен документ' must receive created_at (+name) metadata."""
    marker = f"MTYS{uuid.uuid4().hex[:6]}"
    text = (f"Важные данные {marker}. Курс валют 90 рублей. ") * 20
    resp = _upload(client, "upload_time.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text

    fake, captured = _metadata_gemini(
        '{"needs_metadata": true, "fields": ["original_filename", "created_at"], "target_filename": null}',
        "Документ загружен, вот ответ.",
    )
    monkeypatch.setattr(gemini, "generate_answer", fake)

    chat = client.post(f"{API_PREFIX}/chat", json={"question": f"когда загружен документ {marker}"})
    assert chat.status_code == 200, chat.text
    assert chat.json()["sources"]

    prompt = captured["prompt"]
    assert "upload_time.txt" in prompt, "requested filename metadata must be present"
    assert "created_at" in prompt, "requested upload-date metadata must be present"
    # The date must be real (from the DB), not fabricated.
    assert re.search(r"created_at=['\"]?\d{4}-\d{2}-\d{2}", prompt), "created_at must be a real ISO date"
    # Fields that were NOT requested stay out of the prompt.
    assert "file_size" not in prompt
    assert "content_length" not in prompt


def test_unavailable_metadata_field_is_never_fabricated(client, monkeypatch):
    """Requesting a missing field (page_number) is dropped, never guessed."""
    marker = f"MTPG{uuid.uuid4().hex[:6]}"
    text = (f"Документ про страницы {marker}. Ошибок не должно быть. ") * 20
    resp = _upload(client, "pages.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text

    fake, captured = _metadata_gemini(
        '{"needs_metadata": true, "fields": ["original_filename", "page_number"], "target_filename": null}',
        "Ответ без выдуманных страниц.",
    )
    monkeypatch.setattr(gemini, "generate_answer", fake)

    chat = client.post(f"{API_PREFIX}/chat", json={"question": f"на какой странице про {marker}"})
    assert chat.status_code == 200, chat.text
    assert chat.json()["sources"]

    prompt = captured["prompt"]
    assert "pages.txt" in prompt, "available requested filename is kept"
    assert "page_number" not in prompt, "unknown metadata field must not be passed"
    assert "created_at" not in prompt, "unrequested field must not be passed"


def test_target_filename_narrows_retrieval(client, monkeypatch):
    """'ищи только в rye.txt' must restrict sources to that document."""
    shared = f"общая фраза про урожай {uuid.uuid4().hex[:6]}"
    _upload(client, "wheat.txt", (f"{shared}. Здесь пшеница. ") * 20)
    resp_b = _upload(client, "rye.txt", (f"{shared}. Здесь рожь. ") * 20)
    assert resp_b.status_code == 201, resp_b.text
    doc_b = resp_b.json()[0]["id"]

    fake, _ = _metadata_gemini(
        '{"needs_metadata": false, "fields": [], "target_filename": "rye.txt"}',
        "Ответ строго по выбранному документу.",
    )
    monkeypatch.setattr(gemini, "generate_answer", fake)

    chat = client.post(f"{API_PREFIX}/chat", json={"question": f"расскажи подробнее про {shared}"})
    assert chat.status_code == 200, chat.text
    sources = chat.json()["sources"]
    assert sources, "the targeted document should be retrieved"
    assert all(s["document_id"] == doc_b for s in sources), (
        "retrieval must be scoped to the named document"
    )


def test_classifier_failure_falls_back_to_no_metadata(client, monkeypatch):
    """If the classifier call fails, the pipeline keeps working without metadata."""
    marker = f"MTFL{uuid.uuid4().hex[:6]}"
    text = (f"Контент на случай сбоя классификатора {marker}. Показатели растут. ") * 20
    resp = _upload(client, "fail_meta.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text

    captured: dict = {}

    def fake(prompt, system_instruction=None, client=None, history=None, summary=None):
        if system_instruction == CLASSIFIER_INS:
            raise gemini.GeminiError("classifier boom")
        captured["prompt"] = prompt
        return "Ответ несмотря на сбой классификатора."

    monkeypatch.setattr(gemini, "generate_answer", fake)

    chat = client.post(f"{API_PREFIX}/chat", json={"question": f"что в документе {marker}"})
    assert chat.status_code == 200, chat.text
    assert chat.json()["sources"]
    assert marker in captured["prompt"]
    for forbidden in ("filename=", "created_at", "[Document"):
        assert forbidden not in captured["prompt"]


def test_classifier_garbage_json_falls_back_safely(client, monkeypatch):
    """Unparseable classifier output must not crash the RAG flow."""
    marker = f"MTGB{uuid.uuid4().hex[:6]}"
    text = (f"Документ для сценария битого JSON {marker}. Возврат 5 процентов. ") * 20
    resp = _upload(client, "garbage_json.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text

    fake, captured = _metadata_gemini(
        "мне кажется, тут нужны метаданные??",
        "Ответ с битым JSON классификатора.",
    )
    monkeypatch.setattr(gemini, "generate_answer", fake)

    chat = client.post(f"{API_PREFIX}/chat", json={"question": f"расскажи про {marker}"})
    assert chat.status_code == 200, chat.text
    assert chat.json()["sources"]
    assert "CONTEXT:" in captured["prompt"]
    assert "filename=" not in captured["prompt"]