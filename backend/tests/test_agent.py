"""Tests for the minimal agent layer (POST /api/agent).

The GigaChat client is mocked at the ``chat_with_functions`` boundary, so the
agent loop, the tool execution against the real retrieval pipeline and the
message round-trip are all exercised in-process against real PostgreSQL and
Qdrant.
"""

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import gemini
from app.services.agent import agent_service

API_PREFIX = "/api"


def _upload(client: TestClient, filename: str, content: bytes):
    files = {"file": (filename, content)}
    return client.post(f"{API_PREFIX}/documents/upload", files=files)


def _get_document(document_id: int):
    from app.database.session import SessionLocal
    from app.models.document import Document

    db = SessionLocal()
    try:
        return db.query(Document).filter(Document.id == document_id).first()
    finally:
        db.close()


def _tool_call_message(name="search_documents", arguments='{"query": "про что документ"}'):
    return {
        "role": "assistant",
        "content": None,
        "function_call": {"name": name, "arguments": arguments},
    }


def _scripted_functions(monkeypatch, script):
    """Queue (message, state_id) pairs for successive chat_with_functions calls.

    Records every (messages, functions, functions_state_id) tuple so tests can
    assert what the agent actually sent to (and received from) the model.
    """
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


def test_agent_answers_without_tool_call(client, monkeypatch):
    """A plain query that needs no documents -> direct answer, no tools."""
    calls = _scripted_functions(
        monkeypatch, [({"content": "Привет! Чем помочь с документами?"}, None)]
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "привет"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Привет! Чем помочь с документами?"
    assert data["tool_calls"] == []
    assert data["tool_results"] == []

    # The function spec must have been advertised even when unused.
    assert len(calls) == 1
    assert calls[0]["functions"] == agent_service.functions_spec()
    assert calls[0]["function_call"] == "auto"
    assert calls[0]["functions_state_id"] is None


def test_agent_calls_search_documents(client, monkeypatch):
    """When the model requests search_documents, the real pipeline runs and the
    compact result (id, filename, score, snippet) reaches the model."""
    marker = f"AGT{uuid.uuid4().hex[:6]}"
    text = (
        f"План продаж дронов {marker}. Рекламный бюджет 900000 рублей в квартал."
    ) * 20
    resp = _upload(client, "agent_doc.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text
    document_id = resp.json()[0]["id"]

    tool_msg = _tool_call_message(arguments='{"query": "какой рекламный бюджет"}')
    final_msg = {"content": "Рекламный бюджет составляет 900000 рублей."}
    calls = _scripted_functions(
        monkeypatch, [(tool_msg, "state-1"), (final_msg, None)]
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": f"какой рекламный бюджет дронов {marker}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Рекламный бюджет составляет 900000 рублей."

    # The tool call was recorded with its parsed arguments.
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["name"] == "search_documents"
    assert data["tool_calls"][0]["arguments"] == {"query": "какой рекламный бюджет"}

    # The tool result is a compact, structured JSON string.
    assert len(data["tool_results"]) == 1
    result = data["tool_results"][0]
    assert result["tool_call_id"] == "search_documents"
    assert result["name"] == "search_documents"
    assert "agent_doc.txt" in result["content"]
    assert str(document_id) in result["content"]
    assert '"score"' in result["content"]
    assert '"snippet"' in result["content"]
    assert marker in result["content"]

    # Exactly two LLM calls: one requesting the tool, one producing the answer.
    assert len(calls) == 2


def test_agent_threads_functions_state_id(client, monkeypatch):
    """GigaChat's functions_state_id from round one is echoed into round two."""
    tool_msg = _tool_call_message(arguments='{"query": "что там"}')
    final_msg = {"content": "Итог."}
    calls = _scripted_functions(
        monkeypatch, [(tool_msg, "state-abc"), (final_msg, None)]
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "что там"})
    assert resp.status_code == 200, resp.text

    assert calls[1]["functions_state_id"] == "state-abc"


def test_agent_passes_tool_result_back_to_model(client, monkeypatch):
    """The assistant function_call message and the role:function result are both
    sent back to the model in the correct order."""
    marker = f"PAS{uuid.uuid4().hex[:6]}"
    text = (f"Документ про запуск {marker}. Дата релиза 1 сентября.") * 20
    _upload(client, "passback.txt", text.encode("utf-8"))

    tool_msg = _tool_call_message(arguments='{"query": "когда релиз"}')
    final_msg = {"content": "Релиз 1 сентября."}
    calls = _scripted_functions(
        monkeypatch, [(tool_msg, "state-2"), (final_msg, None)]
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": f"когда релиз {marker}"})
    assert resp.status_code == 200, resp.text

    second = calls[1]["messages"]
    roles = [m["role"] for m in second]
    assert roles == ["system", "user", "assistant", "function"], roles

    assistant_msg = second[2]
    assert assistant_msg.get("function_call") == tool_msg["function_call"]

    tool_msg_out = second[3]
    assert tool_msg_out["role"] == "function"
    assert tool_msg_out["name"] == "search_documents"
    assert "passback.txt" in tool_msg_out["content"]
    assert marker in tool_msg_out["content"]


def test_agent_handles_search_error(client, monkeypatch):
    """A failing retrieval must not crash the loop: the model gets an error
    object in the tool result and still produces a final answer."""
    def boom(user_id, query, document_ids):
        raise RuntimeError("qdrant exploded")

    monkeypatch.setattr(agent_service, "_search_documents", boom)

    tool_msg = _tool_call_message(arguments='{"query": "что там в документах"}')
    final_msg = {"content": "Не удалось выполнить поиск по документам."}
    _scripted_functions(monkeypatch, [(tool_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "что в документах"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Не удалось выполнить поиск по документам."
    assert len(data["tool_results"]) == 1
    assert "error" in data["tool_results"][0]["content"]
    assert "qdrant exploded" in data["tool_results"][0]["content"]


def test_agent_unknown_tool_is_reported(client, monkeypatch):
    """An unexpected tool name is reported back instead of crashing."""
    unknown = _tool_call_message(name="write_file", arguments="{}")
    final_msg = {"content": "Такого инструмента нет."}
    _scripted_functions(monkeypatch, [(unknown, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "запиши файл"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert len(data["tool_results"]) == 1
    assert data["tool_results"][0]["name"] == "write_file"
    assert "Unknown tool" in data["tool_results"][0]["content"]


def test_agent_respects_user_permissions(client, monkeypatch, register_user):
    """A user can never search documents owned by another user: the tool runs
    scoped to the caller, so another user's document never appears."""
    marker = f"PRM{uuid.uuid4().hex[:6]}"
    text = (f"Секретные данные {marker}. Пароль от сервера = s3cr3t.") * 20
    resp = _upload(client, "private.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text
    private_id = resp.json()[0]["id"]

    tool_msg = _tool_call_message(arguments=f'{{"query": "{marker} секретный пароль"}}')
    final_msg = {"content": "Не нашёл такой информации."}
    _scripted_functions(monkeypatch, [(tool_msg, "s"), (final_msg, None)])

    with TestClient(app) as other:
        info = register_user(other)
        other.headers.update({"Authorization": f"Bearer {info['token']}"})
        resp = other.post(
            f"{API_PREFIX}/agent",
            json={"question": f"что за секрет {marker}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert len(data["tool_results"]) == 1
        content = data["tool_results"][0]["content"]
        assert "private.txt" not in content
        assert str(private_id) not in content
        assert marker not in content


def test_agent_requires_authentication():
    """No bearer token -> HTTP 401, same as every other protected endpoint."""
    with TestClient(app) as c:
        resp = c.post(f"{API_PREFIX}/agent", json={"question": "привет"})
    assert resp.status_code == 401, resp.text


def test_agent_degrades_when_gigachat_fails(client, monkeypatch):
    """An LLM outage degrades to an honest message instead of a 500."""
    def failing(messages, functions=None, function_call="auto", functions_state_id=None, client=None):
        raise gemini.GeminiError("boom")

    monkeypatch.setattr(gemini, "chat_with_functions", failing)

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "привет"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "unavailable" in data["answer"].lower()


def _insert_document(user_id: int, filename: str, content: str, file_type: str = "txt") -> int:
    """Insert a Document row directly (no embedding) for focused tool tests."""
    from app.database.session import SessionLocal
    from app.models.document import Document

    db = SessionLocal()
    try:
        doc = Document(
            user_id=user_id,
            filename="stored.txt",
            original_filename=filename,
            file_type=file_type,
            file_size=len(content.encode("utf-8")),
            filepath="/data/uploads/stored.txt",
            content=content,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id
    finally:
        db.close()


def test_agent_calls_read_document(client, monkeypatch, user_id):
    """When the model requests read_document, the stored document content,
    filename and type are returned to the model in a structured result."""
    marker = f"RED{uuid.uuid4().hex[:6]}"
    text = (f"Служебная записка {marker}. Должность Сергея Юрьевича — директор.") * 5
    document_id = _insert_document(user_id, "zapiska.txt", text)

    read_msg = _tool_call_message("read_document", {"document_id": document_id})
    final_msg = {"content": "Сергей Юрьевич — директор."}
    calls = _scripted_functions(monkeypatch, [(read_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": f"кто такой Сергей {marker}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Сергей Юрьевич — директор."

    # The agent advertised both tools to the model.
    names = {f["name"] for f in calls[0]["functions"]}
    assert {"search_documents", "read_document"} <= names

    # The read result is structured and carries the document metadata + text.
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["name"] == "read_document"
    assert data["tool_calls"][0]["arguments"] == {"document_id": document_id}

    assert len(data["tool_results"]) == 1
    result = json.loads(data["tool_results"][0]["content"])
    assert result["document_id"] == document_id
    assert result["filename"] == "zapiska.txt"
    assert result["file_type"] == "txt"
    assert result["file_size"] == len(text.encode("utf-8"))
    assert result["content_length"] == len(text)
    assert result["truncated"] is False
    assert marker in result["text"]
    assert "директор" in result["text"]


def test_read_document_respects_user_permissions(client, monkeypatch, user_id, register_user):
    """A user can never read another user's document: the tool returns the
    same 'document not found' error as for a nonexistent id and leaks nothing."""
    marker = f"SEC{uuid.uuid4().hex[:6]}"
    text = (f"Секретный устав {marker}. Пароль = s3cr3t.") * 5
    private_id = _insert_document(user_id, "private.txt", text)

    read_msg = _tool_call_message("read_document", {"document_id": private_id})
    final_msg = {"content": "Не могу прочитать документ."}
    _scripted_functions(monkeypatch, [(read_msg, "s"), (final_msg, None)])

    with TestClient(app) as other:
        info = register_user(other)
        other.headers.update({"Authorization": f"Bearer {info['token']}"})
        resp = other.post(
            f"{API_PREFIX}/agent",
            json={"question": "что за документ"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert len(data["tool_results"]) == 1
        result = json.loads(data["tool_results"][0]["content"])
        assert result["error"] == "document not found"
        assert "private.txt" not in result
        assert marker not in json.dumps(result, ensure_ascii=False)


def test_read_document_unknown_document(client, monkeypatch):
    """A nonexistent document_id yields a clean error result, not a crash."""
    read_msg = _tool_call_message("read_document", {"document_id": 999_999})
    final_msg = {"content": "Такого документа нет."}
    _scripted_functions(monkeypatch, [(read_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "прочитай 999999"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert len(data["tool_results"]) == 1
    result = json.loads(data["tool_results"][0]["content"])
    assert result["error"] == "document not found"
    assert result["document_id"] == 999_999


def test_read_document_bad_arguments(client, monkeypatch):
    """A missing/non-numeric document_id is reported instead of crashing."""
    for bad in ({}, {"document_id": "не число"}, {"document_id": None}):
        read_msg = _tool_call_message("read_document", bad)
        final_msg = {"content": "Некорректные аргументы."}
        _scripted_functions(monkeypatch, [(read_msg, "s"), (final_msg, None)])

        resp = client.post(f"{API_PREFIX}/agent", json={"question": "прочитай"})
        assert resp.status_code == 200, resp.text
        result = json.loads(resp.json()["tool_results"][0]["content"])
        assert "document_id" in result["error"]


def test_agent_search_then_read_then_answer(client, monkeypatch, user_id):
    """The full tool sequence the user cares about:
    search_documents -> read_document -> final answer."""
    marker = f"SEA{uuid.uuid4().hex[:6]}"
    text = (f"Кадровые данные {marker}. Должность Сергея Юрьевича — заместитель директора.") * 20
    resp = _upload(client, "kadry.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text
    document_id = resp.json()[0]["id"]

    search_msg = _tool_call_message(
        "search_documents", {"query": f"Сергей Юрьевич должность {marker}"}
    )
    read_msg = _tool_call_message("read_document", {"document_id": document_id})
    final_msg = {"content": "Сергей Юрьевич — заместитель директора."}
    calls = _scripted_functions(
        monkeypatch, [(search_msg, "s1"), (read_msg, "s2"), (final_msg, None)]
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": f"Найди информацию о Сергее Юрьевиче и расскажи его должность {marker}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Сергей Юрьевич — заместитель директора."

    # Both tool calls are recorded in order.
    names = [call["name"] for call in data["tool_calls"]]
    assert names == ["search_documents", "read_document"]
    assert data["tool_calls"][1]["arguments"] == {"document_id": document_id}

    # Exactly three LLM calls: search, read, final answer.
    assert len(calls) == 3

    # The second round receives the functions_state_id threaded from the first.
    assert calls[1]["functions_state_id"] == "s1"

    # Round three sees the accumulated conversation: user, assistant(2), function(2).
    third_roles = [m["role"] for m in calls[2]["messages"]]
    assert third_roles == [
        "system", "user", "assistant", "function", "assistant", "function",
    ], third_roles


def test_read_document_error_is_reported(client, monkeypatch):
    """A failing read must not crash the loop: the model gets an error object
    and still produces a final answer."""
    def boom(arguments, user_id):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(agent_service, "_read_document", boom)

    read_msg = _tool_call_message("read_document", {"document_id": 1})
    final_msg = {"content": "Не удалось прочитать документ."}
    _scripted_functions(monkeypatch, [(read_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "прочитай документ"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Не удалось прочитать документ."
    assert len(data["tool_results"]) == 1
    assert "error" in data["tool_results"][0]["content"]
    assert "db exploded" in data["tool_results"][0]["content"]


def test_read_document_limits_context(client, monkeypatch, user_id):
    """Oversized documents are returned in bounded windows: the first call
    truncates to AGENT_READ_MAX_CHARS and flags it, a follow-up call with an
    offset reads the remaining portion."""
    max_chars = settings.AGENT_READ_MAX_CHARS
    text = ("Параграф о полётах дронов и квотах. " * 300) + "КОНЕЦ ДОКУМЕНТА"
    assert max_chars < len(text) < 2 * max_chars
    document_id = _insert_document(user_id, "big.txt", text)

    # First read: no offset -> first window, truncated.
    read1 = _tool_call_message("read_document", {"document_id": document_id})
    final1 = {"content": "первая часть ок"}
    _scripted_functions(monkeypatch, [(read1, "s"), (final1, None)])
    resp = client.post(f"{API_PREFIX}/agent", json={"question": "первая часть"})
    assert resp.status_code == 200, resp.text
    first = json.loads(resp.json()["tool_results"][0]["content"])
    assert first["truncated"] is True
    assert first["length"] == max_chars
    assert first["text"] == text[:max_chars]
    assert first["offset"] == 0

    # Second read: offset = max_chars -> remainder, not truncated.
    read2 = _tool_call_message(
        "read_document", {"document_id": document_id, "offset": max_chars}
    )
    final2 = {"content": "вторая часть ок"}
    _scripted_functions(monkeypatch, [(read2, "s"), (final2, None)])
    resp = client.post(f"{API_PREFIX}/agent", json={"question": "вторая часть"})
    assert resp.status_code == 200, resp.text
    second = json.loads(resp.json()["tool_results"][0]["content"])
    assert second["truncated"] is False
    assert second["offset"] == max_chars
    assert second["text"] == text[max_chars:]


# ---------------------------------------------------------------- create_document


def _create_call(document_spec: dict, output_format: str = "docx"):
    return _tool_call_message(
        "create_document",
        {"document_spec": document_spec, "output_format": output_format},
    )


def test_agent_calls_create_document(client, monkeypatch, user_id):
    """create_document produces a real, parseable docx owned by the caller."""
    create_msg = _create_call(
        {
            "title": "Трудовой договор",
            "author": "ООО Ромашка",
            "blocks": [
                {"type": "heading", "level": 1, "text": "Общие положения"},
                {"type": "paragraph", "text": "Стороны заключили договор."},
            ],
        }
    )
    final_msg = {"content": "Договор создан и сохранён."}
    calls = _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай договор"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Договор создан и сохранён."
    names = {f["name"] for f in calls[0]["functions"]}
    assert "create_document" in names

    result = json.loads(data["tool_results"][0]["content"])
    assert result["success"] is True
    assert result["file_type"] == "docx"
    assert result["filename"].endswith(".docx")

    doc = _get_document(result["document_id"])
    assert doc is not None
    assert doc.user_id == user_id
    assert doc.file_type == "docx"
    assert result["file_size"] == doc.file_size
    assert result["document_id"] == doc.id

    # A real file on disk that python-docx can reopen, with Cyrillic intact.
    path = Path(doc.filepath)
    assert path.is_file()
    assert path.read_bytes()[:2] == b"PK"
    from docx import Document as DocxDocument

    parsed = DocxDocument(str(path))
    joined = " | ".join(p.text for p in parsed.paragraphs)
    assert "Трудовой договор" in joined
    assert "Стороны заключили договор." in joined
    assert "Общие положения" in joined

    # The content column stores the plain-text form (searchable/readable).
    assert "Трудовой договор" in doc.content
    assert "Стороны заключили договор." in doc.content


def test_agent_calls_create_document_odt(client, monkeypatch, user_id):
    """The same spec renders as a real, parseable odt."""
    create_msg = _create_call(
        {
            "title": "Отчёт по проекту",
            "blocks": [
                {"type": "heading", "level": 1, "text": "Итоги"},
                {"type": "paragraph", "text": "Проект завершён успешно."},
            ],
        },
        output_format="odt",
    )
    final_msg = {"content": "ODT готов."}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай odt"})
    assert resp.status_code == 200, resp.text
    result = json.loads(resp.json()["tool_results"][0]["content"])
    assert result["success"] is True
    assert result["file_type"] == "odt"
    assert result["filename"].endswith(".odt")

    doc = _get_document(result["document_id"])
    assert doc.user_id == user_id
    path = Path(doc.filepath)
    assert path.is_file()
    assert path.read_bytes()[:2] == b"PK"

    from odf import teletype
    from odf.opendocument import load

    loaded = load(str(path))
    text = " ".join(teletype.extractText(loaded.text).split())
    assert "Отчёт по проекту" in text
    assert "Проект завершён успешно." in text


def test_create_document_validates_spec(client, monkeypatch):
    """An invalid spec (missing title) yields a safe structured error and no file."""
    create_msg = _create_call({"blocks": []})
    final_msg = {"content": "Создать не удалось."}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай"})
    assert resp.status_code == 200, resp.text
    result = json.loads(resp.json()["tool_results"][0]["content"])
    assert result["success"] is False
    assert "title" in result["error"].lower()


def test_create_document_rejects_oversized_spec(client, monkeypatch):
    """An oversized spec is rejected before any file is generated."""
    create_msg = _create_call(
        {
            "title": "Док",
            "blocks": [{"type": "paragraph", "text": "x" * (settings.AGENT_DOCUMENT_MAX_LINE_CHARS + 1)}],
        }
    )
    final_msg = {"content": "Слишком большой."}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай большой"})
    assert resp.status_code == 200, resp.text
    result = json.loads(resp.json()["tool_results"][0]["content"])
    assert result["success"] is False
    assert "too long" in result["error"]


def test_create_document_bad_format(client, monkeypatch):
    """An unsupported output format is rejected."""
    create_msg = _create_call({"title": "Док"}, output_format="pdf")
    final_msg = {"content": "Не получилось."}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай pdf"})
    assert resp.status_code == 200, resp.text
    result = json.loads(resp.json()["tool_results"][0]["content"])
    assert result["success"] is False
    assert "unsupported output format" in result["error"]


def test_create_document_ignores_user_id_from_arguments(client, monkeypatch, user_id):
    """user_id is never trusted from tool arguments: ownership comes from the
    authenticated request context."""
    create_msg = _tool_call_message(
        "create_document",
        {
            "document_spec": {"title": "Секретный документ", "blocks": [{"type": "paragraph", "text": "демо"}]},
            "output_format": "docx",
            "user_id": 999_999,
        },
    )
    final_msg = {"content": "готово"}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай"})
    assert resp.status_code == 200, resp.text
    result = json.loads(resp.json()["tool_results"][0]["content"])
    assert result["success"] is True

    doc = _get_document(result["document_id"])
    assert doc is not None
    assert doc.user_id == user_id


def test_create_document_ownership(client, monkeypatch, register_user):
    """A document created by user B is never readable by user A."""
    with TestClient(app) as other:
        info = register_user(other)
        other.headers.update({"Authorization": f"Bearer {info['token']}"})
        create_msg = _create_call({"title": "Приватный договор", "blocks": [{"type": "paragraph", "text": "демо"}]})
        final_msg = {"content": "ок"}
        _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

        resp = other.post(f"{API_PREFIX}/agent", json={"question": "создай"})
        assert resp.status_code == 200, resp.text
        created_id = json.loads(resp.json()["tool_results"][0]["content"])["document_id"]

    # User A's read_document against B's created document -> not found.
    read_msg = _tool_call_message("read_document", {"document_id": created_id})
    final_msg = {"content": "Нет доступа."}
    _scripted_functions(monkeypatch, [(read_msg, "s"), (final_msg, None)])
    resp = client.post(f"{API_PREFIX}/agent", json={"question": "прочитай"})
    assert resp.status_code == 200, resp.text
    result = json.loads(resp.json()["tool_results"][0]["content"])
    assert result["error"] == "document not found"


def test_agent_search_read_create_answer(client, monkeypatch, user_id):
    """The full user scenario: search -> read -> create -> final answer."""
    marker = f"GEN{uuid.uuid4().hex[:6]}"
    text = (f"Данные сотрудника {marker}. Сергей Юрьевич, должность — директор.") * 20
    resp = _upload(client, "data.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text
    document_id = resp.json()[0]["id"]

    search_msg = _tool_call_message(
        "search_documents", {"query": f"Сергей Юрьевич должность {marker}"}
    )
    read_msg = _tool_call_message("read_document", {"document_id": document_id})
    create_msg = _create_call(
        {
            "title": "Договор",
            "blocks": [{"type": "paragraph", "text": f"Сергей Юрьевич — директор. {marker}"}],
        }
    )
    final_msg = {"content": "Договор создан на основе найденных данных."}
    calls = _scripted_functions(
        monkeypatch,
        [(search_msg, "s1"), (read_msg, "s2"), (create_msg, "s3"), (final_msg, None)],
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": f"Создай документ на основе найденных данных {marker}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Договор создан на основе найденных данных."
    names = [call["name"] for call in data["tool_calls"]]
    assert names == ["search_documents", "read_document", "create_document"]

    create_result = json.loads(data["tool_results"][-1]["content"])
    assert create_result["success"] is True
    assert create_result["file_type"] == "docx"

    created = _get_document(create_result["document_id"])
    assert created is not None
    assert created.user_id == user_id
    assert Path(created.filepath).is_file()
    assert marker in created.content

    # Three tool rounds consumed, the final answer comes from the last call.
    assert len(calls) == 4


# ------------------------------------------------------- create policy


def test_agent_creates_any_document_default_docx(client, monkeypatch, user_id):
    """'сгенерируй любой документ' -> the agent calls create_document with a
    minimal demo docx (no search, no clarifying question); a real owned file
    is created."""
    create_msg = _create_call(
        {
            "title": "Пример документа",
            "blocks": [{"type": "paragraph", "text": "Это демонстрационный документ."}],
        },
        output_format="docx",
    )
    final_msg = {"content": "Документ готов: Пример документа.docx"}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "сгенерируй любой документ"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert [c["name"] for c in data["tool_calls"]] == ["create_document"]
    assert data["tool_calls"][0]["arguments"]["output_format"] == "docx"

    result = json.loads(data["tool_results"][0]["content"])
    assert result["success"] is True
    assert result["file_type"] == "docx"
    created = _get_document(result["document_id"])
    assert created is not None
    assert created.user_id == user_id
    assert Path(created.filepath).is_file()


def test_agent_creates_example_document_with_random_data(client, monkeypatch, user_id):
    """'создай пример с рандомными данными' -> create_document with clearly
    fictional demo data; no search and no clarifying questions."""
    create_msg = _create_call(
        {
            "title": "Пример трудового договора",
            "blocks": [
                {"type": "heading", "level": 1, "text": "Стороны"},
                {
                    "type": "paragraph",
                    "text": "Работодатель: ООО «Альфа». Работник: Иванов Иван Иванович, инженер.",
                },
                {"type": "paragraph", "text": "Документ носит демонстрационный характер."},
            ],
        }
    )
    final_msg = {"content": "Пример договора создан с демонстрационными данными."}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": "создай пример документа с рандомными данными"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert [c["name"] for c in data["tool_calls"]] == ["create_document"]
    result = json.loads(data["tool_results"][0]["content"])
    assert result["success"] is True
    created = _get_document(result["document_id"])
    assert created is not None
    assert created.user_id == user_id
    assert "Иванов" in created.content


def test_agent_answers_salary_question_without_create(client, monkeypatch):
    """'сколько зарплата у Сергея' -> search -> read -> answer; NO
    create_document: answering a question is not creating a file."""
    marker = f"SAL{uuid.uuid4().hex[:6]}"
    text = (f"Кадровые данные {marker}. Зарплата Сергея Юрьевича — 180000 рублей.") * 20
    resp = _upload(client, "salary.txt", text.encode("utf-8"))
    assert resp.status_code == 201, resp.text
    document_id = resp.json()[0]["id"]

    search_msg = _tool_call_message("search_documents", {"query": f"зарплата Сергея {marker}"})
    read_msg = _tool_call_message("read_document", {"document_id": document_id})
    final_msg = {"content": "Зарплата Сергея Юрьевича — 180000 рублей в месяц."}
    _scripted_functions(
        monkeypatch, [(search_msg, "s1"), (read_msg, "s2"), (final_msg, None)]
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": f"сколько зарплата у Сергея {marker}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["answer"] == "Зарплата Сергея Юрьевича — 180000 рублей в месяц."
    assert [c["name"] for c in data["tool_calls"]] == ["search_documents", "read_document"]
    assert not any(c["name"] == "create_document" for c in data["tool_calls"])


def test_agent_answers_general_question_without_create(client, monkeypatch):
    """'расскажи кратко про налоговый кодекс' -> direct answer, no tools,
    and above all NO create_document: general knowledge is not a file request."""
    final_msg = {"content": "Налоговый кодекс РФ — основной акт налогового законодательства."}
    _scripted_functions(monkeypatch, [(final_msg, None)])

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": "расскажи кратко про налоговый кодекс"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["tool_calls"] == []
    assert data["tool_results"] == []
    assert "Налоговый кодекс" in data["answer"]


def test_agent_unsupported_format_is_never_generated(client, monkeypatch, user_id):
    """'создай PDF': even if the model passes pdf, the system must NOT create
    a pdf — it returns a safe structured error so the model can honestly say
    only docx/odt are supported, and no pdf document row is created."""
    create_msg = _create_call({"title": "Док", "blocks": []}, output_format="pdf")
    final_msg = {"content": "PDF пока не поддерживается. Могу создать DOCX или ODT."}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай pdf документ"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    result = json.loads(data["tool_results"][0]["content"])
    assert result["success"] is False
    assert "unsupported output format" in result["error"]
    assert data["answer"] == final_msg["content"]

    from app.database.session import SessionLocal
    from app.models.document import Document

    db = SessionLocal()
    try:
        pdf_docs = (
            db.query(Document)
            .filter(Document.user_id == user_id, Document.file_type == "pdf")
            .count()
        )
    finally:
        db.close()
    assert pdf_docs == 0


def test_create_document_from_markdown_content(client, monkeypatch, user_id):
    """create_document accepts the document body as a Markdown `content` string
    and the backend parses it into the structured spec (headings/paragraphs)."""
    create_msg = _tool_call_message(
        "create_document",
        {
            "title": "Справка",
            "content": (
                "# Справка\n\n"
                "## Общие сведения\n\n"
                "ФИО: Иванов Иван Иванович\n"
                "Должность: инженер\n\n"
                "## Список\n\n"
                "- первый\n"
                "- второй\n"
            ),
            "output_format": "docx",
        },
    )
    final_msg = {"content": "готово"}
    _scripted_functions(monkeypatch, [(create_msg, "s"), (final_msg, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": "создай"})
    assert resp.status_code == 200, resp.text
    result = json.loads(resp.json()["tool_results"][0]["content"])
    assert result["success"] is True

    doc = _get_document(result["document_id"])
    assert doc is not None
    assert "Иванов" in doc.content
    assert "первый" in doc.content


def test_markdown_to_spec_parses_structure():
    """Markdown maps to the right block types and heading levels."""
    from app.services.markdown_spec import markdown_to_spec

    spec = markdown_to_spec(
        "# Заголовок\n\n"
        "Текст абзаца\n\n"
        "## Подраздел\n\n"
        "- один\n"
        "- два\n\n"
        "| Имя | Возраст |\n"
        "|---|---|\n"
        "| Аня | 30 |\n",
        title="Явный заголовок",
    )
    assert spec.title == "Явный заголовок"
    types = [type(b).__name__ for b in spec.blocks]
    assert types[0].startswith("Heading")
    assert types[1].startswith("Paragraph")
    assert types[3].startswith("List")
    assert types[4].startswith("Table")
    assert spec.blocks[4].rows == [["Аня", "30"]]

