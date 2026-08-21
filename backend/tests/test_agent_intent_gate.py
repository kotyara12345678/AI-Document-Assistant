"""INTENT GATE regression tests.

Conversational messages («привет», «как дела?», «что ты умеешь?», «расскажи
на что ты способен», «спасибо», «понятно», «помоги разобраться») must NEVER
trigger the document tools, while genuine document requests (create / find /
read / compare / edit / list) still do.

The gate lives in app.services.agent_intent and the agent enforces it by
WITHHOLDING the GigaChat ``functions`` payload for non-document intents — an
LLM cannot call a tool it was not given. ``_sanitize_final_answer`` is also
intent-aware: it never rewrites a plain greeting into
"Файл не был создан: данных для подготовки документа не хватает".
"""

import json

from fastapi.testclient import TestClient

from app.services import gemini
from app.services.agent_intent import (
    CONVERSATIONAL,
    DOCUMENT,
    UNCERTAIN,
    is_creation_request,
    resolve_intent,
    tools_enabled,
)

API_PREFIX = "/api"

ORIGINAL = "Оригинальный текст документа. Строка первая.\nСтрока вторая.\n\n\n"
EDITED = "Оригинальный текст документа. Строка первая.\nСтрока вторая изменена.\n\n\n"


def _upload(client: TestClient, filename: str, content: bytes) -> int:
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": (filename, content)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]["id"]


def _tool_call_message(name: str, arguments: dict):
    return {
        "role": "assistant",
        "content": None,
        "function_call": {"name": name, "arguments": arguments},
    }


def _scripted_functions(monkeypatch, script):
    """Queue (message, state_id) pairs; record every outgoing request."""
    calls = []

    def fake(messages, functions=None, function_call="auto", functions_state_id=None, client=None, usage_hook=None):
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


def _ask(client, monkeypatch, question, answer="Здравствуйте! Чем могу помочь?"):
    calls = _scripted_functions(monkeypatch, [({"content": answer}, None)])
    resp = client.post(f"{API_PREFIX}/agent", json={"question": question})
    assert resp.status_code == 200, resp.text
    return calls, resp.json()


# ---------------------------------------------------------------------------
# Pure classifier unit tests (no DB / no LLM).
# ---------------------------------------------------------------------------


class TestIntentClassifier:
    def test_core_conversational_phrases_are_not_document(self):
        for q in (
            "привет",
            "Привет!",
            "здравствуйте",
            "добрый день",
            "как дела?",
            "как ты?",
            "спасибо",
            "понятно",
            "что ты умеешь?",
            "расскажи на что ты способен",
            "помоги разобраться",
        ):
            assert resolve_intent(q) == CONVERSATIONAL, q
            assert tools_enabled(q) is False, q

    def test_conversational_with_pinned_context_still_not_document(self):
        # Even if a document is attached in the UI, a pure greeting must never
        # route to the document tools.
        assert tools_enabled("привет", has_document_context=True) is False
        assert tools_enabled("что ты умеешь?", has_document_context=True) is False

    def test_document_requests_are_document(self):
        for q in (
            "создай трудовой договор",
            "сгенерируй PDF-отчёт по проекту",
            "сделай документ из шаблона",
            "подготовь файл с данными",
            "найди в документах договор",
            "прочитай этот документ",
            "сравни эти два документа",
            "в чём разница между этими файлами",
            "переведи этот документ на английский",
            "измени этот документ",
            "перечисли все мои файлы",
            "сколько у меня документов",
            "какие у меня файлы",
        ):
            assert resolve_intent(q) == DOCUMENT, q
            assert tools_enabled(q) is True, q

    def test_fact_questions_about_user_data_are_document(self):
        for q in (
            "сколько зарплата у Сергея?",
            "какой бюджет и срок у проекта?",
            "кто такой Иван Петров?",
        ):
            assert resolve_intent(q) == DOCUMENT, q
            assert tools_enabled(q) is True, q

    def test_uncertain_follow_up_requires_active_context_for_tools(self):
        for q in ("продолжай", "а?", "что там дальше", "дальше"):
            assert resolve_intent(q) == UNCERTAIN, q
            assert tools_enabled(q) is False, q
            assert tools_enabled(q, has_document_context=True) is True, q

    def test_is_creation_request(self):
        assert is_creation_request("создай договор")
        assert is_creation_request("сгенерируй PDF-отчёт по проекту")
        assert is_creation_request("создай в формате DOCX документ")
        assert not is_creation_request("привет")
        assert not is_creation_request("найди договор")
        assert not is_creation_request("переведи документ")


# ---------------------------------------------------------------------------
# _sanitize_final_answer intent-awareness (white-box, no DB).
# ---------------------------------------------------------------------------


class TestSanitizeIntentAwareness:
    def test_greeting_is_never_rewritten_to_file_failure(self):
        from app.services.agent import _sanitize_final_answer

        answer = "Здравствуйте! Я готов помочь с вашими документами."
        out = _sanitize_final_answer(answer, [], "привет")
        # REGRESSION: "готов" used to be read as a fabricated success claim and
        # the whole greeting was replaced with "Файл не был создан: ...".
        assert out == answer
        assert "не был создан" not in out.lower()
        assert "данных для подготовки документа" not in out.lower()

    def test_non_creation_document_request_gets_neutral_replacement(self):
        from app.services.agent import _sanitize_final_answer

        out = _sanitize_final_answer("Ваш договор создан и готов!", [], "найди договор")
        assert "подготовки документа не хватает" not in out
        assert "ваш договор создан" not in out.lower()
        assert "не было создано" in out

    def test_search_results_with_ready_word_not_replaced(self):
        """After a search_documents call, 'документ доступен' is a legitimate
        description — NOT a fabricated creation claim."""
        from app.schemas.agent import AgentToolResult
        from app.services.agent import _sanitize_final_answer

        results = [
            AgentToolResult(
                name="search_documents",
                content=json.dumps([{"document_id": 1, "filename": "doc.txt", "score": 0.9, "text": "..."}]),
                tool_call_id="s1",
            )
        ]
        answer = "Документ доступен: в файле doc.txt зарплата 50000."
        out = _sanitize_final_answer(answer, results, "какая зарплата?")
        assert out == answer, f"search result response must not be replaced, got {out}"

    def test_list_results_with_ready_word_not_replaced(self):
        """After a list_documents call, 'файлы готовы' is legitimate."""
        from app.schemas.agent import AgentToolResult
        from app.services.agent import _sanitize_final_answer

        results = [
            AgentToolResult(
                name="list_documents",
                content=json.dumps([{"document_id": 1, "filename": "a.txt"}]),
                tool_call_id="l1",
            )
        ]
        answer = "Ваши файлы готовы к просмотру."
        out = _sanitize_final_answer(answer, results, "список всех файлов")
        assert out == answer, f"list result response must not be replaced, got {out}"

    def test_creation_request_keeps_honest_failure(self):
        from app.services.agent import _sanitize_final_answer

        out = _sanitize_final_answer("Отчёт создан и готов!", [], "создай PDF-отчёт")
        assert "не был создан" in out or "не создан" in out


# ---------------------------------------------------------------------------
# Behavioural: conversational requests never call document tools.
# ---------------------------------------------------------------------------


class TestConversationalNeverCallsDocumentTools:
    def test_greeting_does_not_call_document_tools(self, client, monkeypatch):
        calls, data = _ask(client, monkeypatch, "привет")
        assert data["tool_calls"] == []
        assert data["tool_results"] == []
        assert calls[0]["functions"] is None

    def test_each_conversational_phrase_keeps_tools_withheld(self, client, monkeypatch):
        for q in (
            "как дела?",
            "что ты умеешь?",
            "расскажи на что ты способен",
            "спасибо",
            "понятно",
            "помоги разобраться",
        ):
            calls, data = _ask(client, monkeypatch, q)
            assert data["tool_calls"] == [], q
            assert data["tool_results"] == [], q
            assert calls[0]["functions"] is None, q

    def test_greeting_with_pinned_document_still_never_calls_tools(
        self, client, monkeypatch
    ):
        doc_id = _upload(client, "pinned.txt", "Проверочный документ.".encode("utf-8"))
        calls, data = _ask(client, monkeypatch, "привет", answer="Привет!")
        assert data["tool_calls"] == []
        assert data["tool_results"] == []
        assert calls[0]["functions"] is None
        assert doc_id is not None

    def test_sanitize_regression_greeting_with_ready_word(self, client, monkeypatch):
        # The real reported bug: a greeting whose prose contains a success-ish
        # word ("готов") used to be replaced with the "file was not created"
        # message — even though no document tool ever ran.
        calls, data = _ask(
            client,
            monkeypatch,
            "привет",
            answer="Здравствуйте! Я готов помочь с вашими документами.",
        )
        assert data["answer"] == "Здравствуйте! Я готов помочь с вашими документами."
        assert "не был создан" not in data["answer"].lower()
        assert "данных для подготовки документа" not in data["answer"].lower()
        assert data["tool_calls"] == []
        assert calls[0]["functions"] is None


# ---------------------------------------------------------------------------
# Behavioural: document requests still reach the real tools.
# ---------------------------------------------------------------------------


class TestDocumentIntentStillCallsTools:
    def test_create_request_calls_create_document(self, client, monkeypatch):
        create_msg = _tool_call_message(
            "create_document",
            {
                "document_spec": {
                    "title": "Отчёт",
                    "blocks": [{"type": "paragraph", "text": "Содержимое отчёта."}],
                },
                "output_format": "docx",
            },
        )
        calls = _scripted_functions(
            monkeypatch, [(create_msg, "s"), ({"content": "Отчёт создан."}, None)]
        )
        resp = client.post(
            f"{API_PREFIX}/agent",
            json={"question": "создай PDF-отчёт по проекту"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert [c["name"] for c in data["tool_calls"]] == ["create_document"]
        assert calls[0]["functions"] is not None

    def test_search_request_calls_search_documents(self, client, monkeypatch):
        search_msg = _tool_call_message("search_documents", {"query": "договор аренды"})
        calls = _scripted_functions(
            monkeypatch, [(search_msg, "s"), ({"content": "Нашёл документ."}, None)]
        )
        resp = client.post(
            f"{API_PREFIX}/agent",
            json={"question": "найди в документах договор аренды"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert [c["name"] for c in data["tool_calls"]] == ["search_documents"]
        assert calls[0]["functions"] is not None

    def test_read_request_calls_read_document(self, client, monkeypatch, user_id):
        doc_id = _upload(
            client, "fact.txt", f"Служебная записка. Зарплата Сергея — 180000.".encode("utf-8")
        )
        read_msg = _tool_call_message("read_document", {"document_id": doc_id})
        calls = _scripted_functions(
            monkeypatch, [(read_msg, "s"), ({"content": "Зарплата Сергея — 180000."}, None)]
        )
        resp = client.post(
            f"{API_PREFIX}/agent",
            json={
                "question": "прочитай этот документ",
                "context_document_ids": [doc_id],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert [c["name"] for c in data["tool_calls"]] == ["read_document"]
        assert calls[0]["functions"] is not None

    def test_compare_request_calls_compare_documents(self, client, monkeypatch):
        left_id = _upload(client, "left.txt", ORIGINAL.encode("utf-8"))
        right_id = _upload(client, "right.txt", EDITED.encode("utf-8"))
        compare_msg = _tool_call_message(
            "compare_documents", {"left_id": left_id, "right_id": right_id}
        )
        calls = _scripted_functions(
            monkeypatch, [(compare_msg, "s"), ({"content": "Есть отличия."}, None)]
        )
        resp = client.post(
            f"{API_PREFIX}/agent",
            json={"question": "сравни эти два документа"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert [c["name"] for c in data["tool_calls"]] == ["compare_documents"]
        assert calls[0]["functions"] is not None

    def test_list_request_calls_list_documents(self, client, monkeypatch):
        list_msg = _tool_call_message("list_documents", {})
        calls = _scripted_functions(
            monkeypatch, [(list_msg, "s"), ({"content": "Список файлов ниже."}, None)]
        )
        resp = client.post(
            f"{API_PREFIX}/agent",
            json={"question": "перечисли все мои файлы"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert [c["name"] for c in data["tool_calls"]] == ["list_documents"]
        assert calls[0]["functions"] is not None

    def test_edit_request_still_advertises_document_tools(self, client, monkeypatch):
        # Without a pinned target the model may reply plainly, but the document
        # tools MUST still be offered for an edit request.
        calls = _scripted_functions(
            monkeypatch, [({"content": "Какой документ редактировать?"}, None)]
        )
        resp = client.post(
            f"{API_PREFIX}/agent",
            json={"question": "переведи этот документ на английский"},
        )
        assert resp.status_code == 200, resp.text
        assert calls[0]["functions"] is not None
        assert calls[0]["functions"][0]["name"] == "search_documents"
        assert any(
            f["name"] == "edit_document" for f in calls[0]["functions"]
        )

    def test_trailing_number_is_not_creation_request(self):
        """A trailing digit in a search query is data, not a create directive."""
        assert not is_creation_request("найди инн алексея 4")
        assert not is_creation_request("найди зарплату сергея 3")
        assert not is_creation_request("найди статью 105 ук рф")
        assert resolve_intent("найди инн алексея 4") == DOCUMENT
        assert resolve_intent("найди статью 3 ук рф") == DOCUMENT