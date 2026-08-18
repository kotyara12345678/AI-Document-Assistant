"""CRITICAL REGRESSION: the agent must never fabricate tool input/output,
document contents, created files or download links.

The backend is the source of truth for everything that "really happened": the
structured ``tool_calls`` / ``tool_results`` / ``sources`` /
``created_documents`` fields and the ``document_created`` stream event are
built ONLY from tools that were actually executed and returned real results.
Whatever the model *claims* in its free-text answer, the backend must never
echo an invented tool call, an invented document content, an invented created
file, or an invented download URL into the structured response.

The LLM is mocked at the ``chat_with_functions`` boundary (same pattern as
test_agent_policy.py), driving the real agent loop + real retrieval pipeline.
"""

import json

from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.models.document import Document
from app.services import gemini
from app.services.agent import agent_service
from app.schemas.agent import AgentRequest

API_PREFIX = "/api"


def _upload(client: TestClient, filename: str, content: bytes) -> int:
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": (filename, content)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]["id"]


def _tool_call_message(name="search_documents", arguments=None):
    return {
        "role": "assistant",
        "content": None,
        "function_call": {"name": name, "arguments": arguments or {}},
    }


def _scripted_functions(monkeypatch, script):
    """Queue (message, state_id) pairs; record every outgoing request."""
    calls = []

    def fake(messages, functions=None, function_call="auto", functions_state_id=None, client=None):
        calls.append(
            {
                "messages": messages,
                "functions": functions,
                "function_call": function_call,
                "functions_state_id": functions_state_id,
            }
        )
        return script.pop(0)

    monkeypatch.setattr(gemini, "chat_with_functions", fake)
    return calls


def _count_documents(user_id: int) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(Document)
            .filter(Document.user_id == user_id)
            .count()
        )
    finally:
        db.close()


def _collect_events(question: str, user_id: int, monkeypatch, script):
    """Drive run_agent_stream with scripted GigaChat turns and return events."""
    _scripted_functions(monkeypatch, script)
    events = list(
        agent_service.run_agent_stream(AgentRequest(question=question), user_id=user_id)
    )
    return events


# ---------------------------------------------------------------------------
# 1. Fabricated tool results: the model claims it searched/read/created but
#    never issued a tool call. The backend must not invent tool I/O.
# ---------------------------------------------------------------------------


def test_no_fabricated_tool_results_when_model_only_claims(client, monkeypatch):
    """The model answers 'I searched and found X' without calling any tool:
    tool_calls, tool_results, sources and created_documents must stay empty."""
    _scripted_functions(
        monkeypatch,
        [({"content": "Я выполнил поиск и нашёл документ: оклад 300000 рублей."}, None)],
    )

    resp = client.post(
        f"{API_PREFIX}/agent", json={"question": "какая зарплата у Сергея?"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # The backend never synthesizes a tool call/result from the model's prose.
    assert data["tool_calls"] == []
    assert data["tool_results"] == []
    assert data["sources"] == []
    assert data["created_documents"] == []
    assert data["agent_steps"] == []


# ---------------------------------------------------------------------------
# 2. Fabricated document contents: the model quotes 'document content' without
#    ever reading a document.
# ---------------------------------------------------------------------------


def test_no_fabricated_document_contents_without_read(client, monkeypatch, user_id):
    """The model quotes specific document content without calling read_document:
    no source is surfaced, no tool result is fabricated, no document is read."""
    marker = "SRC1234"
    _upload(client, f"src_{marker}.txt", f"Содержимое {marker}".encode("utf-8"))

    _scripted_functions(
        monkeypatch,
        [({"content": f"В вашем договоре написано, что оклад 500000 рублей ({marker})."}, None)],
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": f"что написано в моём договоре? ({marker})"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # No read_document was executed, so no content is surfaced anywhere.
    assert data["tool_calls"] == []
    assert data["tool_results"] == []
    assert data["sources"] == []
    assert data["created_documents"] == []
    # And nothing was persisted.
    assert _count_documents(user_id) == 1  # only the uploaded file


# ---------------------------------------------------------------------------
# 3. Fabricated PDF creation: the model claims a PDF was created without a
#    successful create_document tool result.
# ---------------------------------------------------------------------------


def test_no_fabricated_pdf_creation_without_tool_call(client, monkeypatch, user_id):
    """The model claims 'PDF created' but never calls create_document: the
    backend must not report a created document and must not emit a
    document_created event."""
    _scripted_functions(
        monkeypatch,
        [
            (
                {
                    "content": (
                        "PDF-договор создан и доступен по ссылке "
                        f"{API_PREFIX}/documents/9/file"
                    )
                },
                None,
            )
        ],
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай PDF-договор"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["created_documents"] == []
    assert data["tool_calls"] == []
    assert data["tool_results"] == []
    assert _count_documents(user_id) == 0


def test_no_document_created_event_when_model_claims_pdf(client, monkeypatch, user_id):
    """Streaming endpoint: a bare 'PDF is ready' claim without a real
    create_document result must NOT produce a document_created event."""
    events = _collect_events(
        "создай PDF-договор",
        user_id,
        monkeypatch,
        [({"content": "PDF-договор готов, скачивайте."}, None)],
    )
    created = [e for e in events if e["type"] == "document_created"]
    assert created == []
    finals = [e for e in events if e["type"] == "final"]
    assert len(finals) == 1


def test_failed_create_document_is_not_surfaced_as_success(client, monkeypatch, user_id):
    """create_document FAILS (unsupported format); the model then claims
    success. The backend must keep created_documents empty and must not emit
    a document_created event with a download_url."""
    bad_create = _tool_call_message(
        "create_document",
        {"document_spec": {"title": "X", "blocks": []}, "output_format": "exe"},
    )
    _scripted_functions(
        monkeypatch,
        [(bad_create, "s"), ({"content": "Документ создан! Скачивайте."}, None)],
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай договор"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    created = [r for r in data["tool_results"] if r["name"] == "create_document"]
    assert created, "create_document must have been called by the model"
    payload = json.loads(created[-1]["content"])
    assert payload.get("success") is False, "the tool really failed"
    # Even though the model claims success, the backend reports no file.
    assert data["created_documents"] == []
    assert _count_documents(user_id) == 0


def test_stream_no_created_event_when_create_fails(client, monkeypatch, user_id):
    """Streaming endpoint: a failing create_document must not emit
    document_created, regardless of what the model says afterwards."""
    bad_create = _tool_call_message(
        "create_document",
        {"document_spec": {"title": "X", "blocks": []}, "output_format": "exe"},
    )
    events = _collect_events(
        "создай договор",
        user_id,
        monkeypatch,
        [(bad_create, "s"), ({"content": "Готово!"}, None)],
    )
    created = [e for e in events if e["type"] == "document_created"]
    assert created == []


# ---------------------------------------------------------------------------
# 4. Fake download links: the only download URLs the backend may surface come
#    from a real successful create/edit result.
# ---------------------------------------------------------------------------


def test_fake_download_link_is_never_emitted(client, monkeypatch, user_id):
    """The model prints a fake download URL but no tool created anything: no
    document_created event and no created_documents entry may carry a URL."""
    fake_url = f"{API_PREFIX}/documents/999999/file"
    _scripted_functions(
        monkeypatch,
        [({"content": f"Ваш файл готов: {fake_url}"}, None)],
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай документ"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["created_documents"] == []
    # The fabricated URL lives only in the model's free-text answer, never in
    # the structured response the backend controls.
    assert fake_url not in json.dumps(data["created_documents"])


def test_download_url_only_from_real_tool_result(client, monkeypatch, user_id):
    """Positive control: when create_document truly succeeds, the ONLY
    download_url surfaced is the one built from the real document_id — never
    one invented by the model's text."""
    create_msg = _tool_call_message(
        "create_document",
        {
            "document_spec": {
                "title": "Договор",
                "blocks": [{"type": "paragraph", "text": "Стороны заключили договор."}],
            },
            "output_format": "docx",
        },
    )
    fake_url_in_answer = f"{API_PREFIX}/documents/777777/file"
    _scripted_functions(
        monkeypatch,
        [
            (create_msg, "s"),
            ({"content": f"Документ готов, скачивайте: {fake_url_in_answer}"}, None),
        ],
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай договор"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    created = [r for r in data["tool_results"] if r["name"] == "create_document"]
    payload = json.loads(created[-1]["content"])
    assert payload["success"] is True
    real_doc_id = payload["document_id"]
    assert _count_documents(user_id) == 1

    # The structured created_documents only carries the real document, with
    # the server-built URL prefix, never the model's fake URL.
    assert data["created_documents"] == [
        {
            "document_id": real_doc_id,
            "filename": payload["filename"],
            "file_type": payload["file_type"],
        }
    ]
    assert payload["download_url"] == f"{API_PREFIX}/documents/{real_doc_id}/file"
    assert payload["download_url"] != fake_url_in_answer


# ---------------------------------------------------------------------------
# 5. Anti-fabrication of the model's PROSE: the final answer may not claim a
#    created file / download link that no real tool result backs up.
# ---------------------------------------------------------------------------


def test_prose_claim_of_created_pdf_is_sanitized(client, monkeypatch, user_id):
    """The model's free-text answer claims 'PDF created, here is a link' but no
    create_document was called: the backend replaces the fabricated claim with
    an honest statement and strips the fake URL."""
    fake_url = f"{API_PREFIX}/documents/42/file"
    _scripted_functions(
        monkeypatch,
        [
            (
                {
                    "content": (
                        "Ваш отчёт готов в формате PDF и доступен по ссылке: "
                        f"{fake_url}"
                    )
                },
                None,
            )
        ],
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": "создай PDF-отчёт по проекту УралТехноСтрой"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["created_documents"] == []
    assert data["tool_calls"] == []
    # The fabricated URL must not survive anywhere in the answer.
    assert fake_url not in data["answer"]
    # The answer must be honest: no claim that a file was really created.
    assert "не был создан" in data["answer"] or "не создан" in data["answer"]


def test_prose_claim_after_failed_create_is_sanitized(client, monkeypatch, user_id):
    """create_document FAILS (fabricated $XXX figures are rejected by the
    placeholder gate); the model then claims success in prose. The backend must
    replace the success claim with the honest error."""
    create_msg = _tool_call_message(
        "create_document",
        {
            "document_spec": {
                "title": "Отчёт",
                "blocks": [
                    {"type": "paragraph", "text": "Общий доход: $XXX млн."},
                    {"type": "paragraph", "text": "Общие расходы: $YYY млн."},
                ],
            },
            "output_format": "pdf",
        },
    )
    _scripted_functions(
        monkeypatch,
        [
            (create_msg, "s"),
            ({"content": "Отчёт создан в PDF, скачайте файл."}, None),
        ],
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": "создай PDF-отчёт по проекту УралТехноСтрой"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # The tool really rejected the $XXX/$YYY placeholders.
    created = [r for r in data["tool_results"] if r["name"] == "create_document"]
    payload = json.loads(created[-1]["content"])
    assert payload["success"] is False
    assert any("$XXX" in p for p in payload.get("placeholders", []))

    # The model's prose success claim must be replaced by the honest outcome.
    assert data["created_documents"] == []
    assert "файл не создан" in data["answer"].lower()
    assert "скачайте" not in data["answer"].lower()
    assert _count_documents(user_id) == 0


def test_honest_failure_prose_is_not_rewritten(client, monkeypatch, user_id):
    """When the model honestly reports the failure, the backend must NOT
    rewrite it — only fabricated success claims are sanitized."""
    bad_create = _tool_call_message(
        "create_document",
        {"document_spec": {"title": "X", "blocks": []}, "output_format": "exe"},
    )
    honest_text = "Не удалось создать документ: указан неверный формат."
    _scripted_functions(
        monkeypatch,
        [(bad_create, "s"), ({"content": honest_text}, None)],
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай договор"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == honest_text


def test_sanitized_answer_mentions_empty_search_not_skipped_tools(
    client, monkeypatch, user_id
):
    """REGRESSION from a real run: the model searched for the project, got no
    hits, but then claimed in prose that a PDF report was created. The honest
    replacement must say the SEARCH found nothing — never that the tools were
    skipped (they were not)."""
    search_msg = _tool_call_message(
        "search_documents", {"query": "проект УралТехноСтрой"}
    )
    _scripted_functions(
        monkeypatch,
        [
            (search_msg, "s"),  # real search runs, returns [] (no hits)
            (
                {
                    "content": (
                        "Ваш PDF-отчёт по проекту «УралТехноСтрой» создан и "
                        "доступен для скачивания."
                    )
                },
                None,
            ),
        ],
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={
            "question": (
                "Создай в формате PDF подробный отчёт по проекту "
                "«УралТехноСтрой» на основе информации из моих документов."
            )
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Search DID run and returned nothing real.
    assert [c["name"] for c in data["tool_calls"]] == ["search_documents"]
    search_result = json.loads(data["tool_results"][0]["content"])
    assert search_result == []

    # The sanitized answer reflects the real situation: search done, no data.
    answer = data["answer"]
    assert "не нашёл" in answer
    assert "УралТехноСтрой" in answer
    # It must NOT claim the tools were skipped.
    assert "инструменты" not in answer.lower()
    # And it must not claim a file was created / offer a download.
    assert data["created_documents"] == []
    assert "скачивания" not in answer.lower()
    assert "файл не создан" in answer.lower()
