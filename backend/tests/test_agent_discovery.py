"""Diagnostics + regression tests for the agent's ability to DISCOVER documents.

The user must not need to know file names, document ids, extensions or exact
document titles. A descriptive request such as "create a contract from the
template and the employee data file" must be resolved by the agent through
``search_documents`` and ``read_document`` on its own.

These tests exercise the REAL retrieval pipeline (PostgreSQL FTS + Qdrant)
with abstract, content-neutral file names (`template_contract.odt`,
`employee_information.txt`), so discovery can only happen via the document
CONTENT / FILENAME — never via a name hint the test gave the model.

The LLM itself is mocked at the ``chat_with_functions`` boundary (same
pattern as test_agent.py); what is tested here is that the pipeline reliably
maps descriptive queries to the right documents and that the full
search -> search -> read -> read -> create -> answer chain completes.
"""

import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import gemini, retrieval
from app.services.agent import SYSTEM_INSTRUCTION, agent_service

API_PREFIX = "/api"

# Abstract, content-neutral names: the tests never reveal what they hold.
TEMPLATE_NAME = "template_contract.odt"
DATA_NAME = "employee_information.txt"
DECOY_NAME = "notes.txt"

TEMPLATE_TEXT = "\n".join(
    [
        "ТРУДОВОЙ ДОГОВОР",
        "Шаблон трудового договора. Заполните данные сторон.",
        "1. Общие положения: Работодатель ______ и Работник ______ заключили настоящий договор.",
        "2. Предмет договора: Работник принимается на работу по должности ______.",
        "3. Оплата труда: ежемесячный оклад ______ рублей.",
        "4. Режим работы: пятидневная рабочая неделя, 40 часов.",
        "5. Ежегодный отпуск: 28 календарных дней.",
        "6. Подписи сторон: Работодатель ______ / Работник ______.",
    ]
)

DATA_TEXT = "\n".join(
    [
        "Данные сотрудника.",
        "ФИО: Сергей Юрьевич Волков.",
        "Дата рождения: 12.03.1985.",
        "Должность: заместитель директора по производству.",
        "Оклад: 250000 рублей в месяц.",
        "Подразделение: производственный департамент.",
        "Руководитель: Анна Петровна Смирнова.",
        "Адрес: г. Москва, ул. Промышленная, д. 5.",
    ]
)

DECOY_TEXT = (
    "План маркетинга на квартал. Бюджет рекламы 500000 рублей. "
    "Каналы: интернет, телевидение, наружная реклама. "
) * 5


def _make_odt_bytes(text: str) -> bytes:
    """Build a minimal but valid ODT whose body is `text` (one line per <p>)."""
    paragraphs = "\n".join(f"<text:p>{line}</text:p>" for line in text.splitlines())
    content_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  office:version="1.2">
  <office:body><office:text>{paragraphs}</office:text></office:body>
</office:document-content>"""
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest '
        'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" manifest:version="1.2" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="mimetype" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        "</manifest:manifest>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", b"application/vnd.oasis.opendocument.text")
        zf.writestr("META-INF/manifest.xml", manifest.encode("utf-8"))
        zf.writestr("content.xml", content_xml.encode("utf-8"))
    return buf.getvalue()


def _upload(client: TestClient, filename: str, content: bytes) -> int:
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": (filename, content)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]["id"]


def _upload_template(client: TestClient) -> int:
    return _upload(client, TEMPLATE_NAME, _make_odt_bytes(TEMPLATE_TEXT))


def _upload_data(client: TestClient) -> int:
    return _upload(client, DATA_NAME, DATA_TEXT.encode("utf-8"))


def _get_document(document_id: int):
    from app.database.session import SessionLocal
    from app.models.document import Document

    db = SessionLocal()
    try:
        return db.query(Document).filter(Document.id == document_id).first()
    finally:
        db.close()


def _tool_call_message(name="search_documents", arguments=None):
    return {
        "role": "assistant",
        "content": None,
        "function_call": {"name": name, "arguments": arguments or {}},
    }


def _create_call(document_spec: dict, output_format: str = "docx"):
    return _tool_call_message(
        "create_document",
        {"document_spec": document_spec, "output_format": output_format},
    )


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


# ------------------------------------------------------------- diagnostics


def test_retrieval_maps_descriptions_to_abstract_documents(client, user_id):
    """DIAGNOSTIC: descriptive queries must find the right documents even
    though the file names give no hint (template_contract.odt etc.)."""
    template_id = _upload_template(client)
    data_id = _upload_data(client)

    hits_tpl = retrieval.retrieve_context(
        question="шаблон трудового договора",
        user_id=user_id,
        top_k=5,
    )
    assert hits_tpl, "The template must be findable by its description"
    tpl_ids = [c.source.document_id for c in hits_tpl]
    assert template_id in tpl_ids, (
        f"template_id {template_id} missing from template search results"
    )
    assert tpl_ids[0] == template_id, "template must be the top template-search hit"

    hits_data = retrieval.retrieve_context(
        question="данные для трудового договора сотрудника",
        user_id=user_id,
        top_k=5,
    )
    assert hits_data, "The employee data file must be findable by its description"
    data_ids = [c.source.document_id for c in hits_data]
    assert data_id in data_ids, (
        f"data_id {data_id} missing from data search results"
    )

    # A targeted data query (what the system prompt directs the model to
    # derive) must rank the data file FIRST — the template must not crowd it
    # out just because it also mentions 'договор'.
    targeted = retrieval.retrieve_context(
        question="данные сотрудника",
        user_id=user_id,
        top_k=5,
    )
    targeted_ids = [c.source.document_id for c in targeted]
    assert targeted_ids[0] == data_id, (
        "the data file must be the top hit for a targeted data query "
        f"(got {targeted_ids})"
    )


def test_keyword_search_finds_document_by_filename_words(client, user_id):
    """DIAGNOSTIC: a document must be findable through its FILE NAME words even
    when its content has nothing in common with the query.

    'Данные_для_договора_Сергей.txt' must be retrievable for the query
    'данные для трудового договора' purely via the filename lexemes.
    """
    name = f"Данные_для_договора_{uuid.uuid4().hex[:4]}.txt"
    unrelated = (
        "Отчёт о производственной линии X. Объём выпуска 1200 единиц в месяц. "
        "Показатели качества стабильны. "
    ) * 5
    doc_id = _upload(client, name, unrelated.encode("utf-8"))

    chunks = retrieval.retrieve_context(
        question="данные для трудового договора",
        user_id=user_id,
        top_k=5,
    )
    assert chunks, "Filename words must surface the document in keyword search"
    assert doc_id in {c.source.document_id for c in chunks}, (
        f"document_id {doc_id} ({name}) not found via its filename"
    )


# ------------------------------------------------------- end-to-end chain


USER_QUESTION = (
    "Создай трудовой договор по шаблону трудового договора, "
    "используя данные из файла с данными трудового договора."
)


def _chain_script(template_id, data_id, decoy_id=None):
    return [
        (_tool_call_message("search_documents", {"query": "шаблон трудового договора"}), "s1"),
        (_tool_call_message("search_documents", {"query": "данные сотрудника"}), "s2"),
        (_tool_call_message("read_document", {"document_id": template_id}), "s3"),
        (_tool_call_message("read_document", {"document_id": data_id}), "s4"),
        (
            _create_call(
                {
                    "title": "Трудовой договор",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": (
                                "Работник: Сергей Юрьевич Волков, "
                                "заместитель директора по производству, "
                                "оклад 250000 рублей в месяц."
                            ),
                        }
                    ],
                }
            ),
            "s5",
        ),
        ({"content": "Трудовой договор создан на основе шаблона и данных сотрудника."}, None),
    ]


def test_agent_creates_contract_from_abstract_documents(client, monkeypatch, user_id):
    """The full user scenario with abstract names:
    search -> search -> read -> read -> create -> answer. The test never tells
    the agent the file names; only the descriptive question is sent."""
    template_id = _upload_template(client)
    data_id = _upload_data(client)

    calls = _scripted_functions(
        monkeypatch, _chain_script(template_id, data_id)
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": USER_QUESTION})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    names = [c["name"] for c in data["tool_calls"]]
    assert names == [
        "search_documents",
        "search_documents",
        "read_document",
        "read_document",
        "create_document",
    ], names

    # The two searches found the right abstract documents — and ranked the
    # right one first, so a real model would pick it.
    tpl_hits = json.loads(data["tool_results"][0]["content"])
    assert tpl_hits[0]["document_id"] == template_id, "template must be the top template-search hit"
    assert TEMPLATE_NAME in data["tool_results"][0]["content"]

    data_hits = json.loads(data["tool_results"][1]["content"])
    assert data_hits[0]["document_id"] == data_id, "data file must be the top data-search hit"
    assert DATA_NAME in data["tool_results"][1]["content"]

    # The reads targeted the ids returned by those searches.
    assert data["tool_calls"][2]["arguments"] == {"document_id": template_id}
    assert data["tool_calls"][3]["arguments"] == {"document_id": data_id}

    # A real docx was created from the read data.
    create_result = json.loads(data["tool_results"][-1]["content"])
    assert create_result["success"] is True
    assert create_result["file_type"] == "docx"
    created = _get_document(create_result["document_id"])
    assert created is not None
    assert created.user_id == user_id
    assert Path(created.filepath).is_file()
    assert "Сергей Юрьевич" in created.content

    assert data["answer"] == "Трудовой договор создан на основе шаблона и данных сотрудника."
    # 5 tool rounds + 1 final turn.
    assert len(calls) == 6


def test_agent_ignores_decoy_when_searching_by_description(client, monkeypatch):
    """With several uploaded documents, the descriptive template search must
    surface the template (and not an unrelated decoy), and the chain still
    completes."""
    template_id = _upload_template(client)
    data_id = _upload_data(client)
    decoy_id = _upload(client, DECOY_NAME, DECOY_TEXT.encode("utf-8"))

    calls = _scripted_functions(
        monkeypatch, _chain_script(template_id, data_id, decoy_id)
    )

    resp = client.post(f"{API_PREFIX}/agent", json={"question": USER_QUESTION})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    tpl_hits = json.loads(data["tool_results"][0]["content"])
    assert tpl_hits[0]["document_id"] == template_id, "template must be the top template-search hit"
    assert str(decoy_id) not in data["tool_results"][0]["content"], (
        "The unrelated decoy must not be offered as a template"
    )

    names = [c["name"] for c in data["tool_calls"]]
    assert names[-1] == "create_document"
    assert len(calls) == 6


# -------------------------------------------- create policy: references


def test_agent_creates_contract_from_template(client, monkeypatch, user_id):
    """'создай трудовой договор по шаблону трудового договора' ->
    search_documents -> read_document(template) -> create_document: the
    template must be the top hit of the descriptive search."""
    template_id = _upload_template(client)
    _upload_data(client)

    search_msg = _tool_call_message("search_documents", {"query": "шаблон трудового договора"})
    read_msg = _tool_call_message("read_document", {"document_id": template_id})
    create_msg = _create_call(
        {
            "title": "Трудовой договор",
            "blocks": [
                {"type": "heading", "level": 1, "text": "Общие положения"},
                {
                    "type": "paragraph",
                    "text": "Договор сформирован по шаблону из документов пользователя.",
                },
            ],
        }
    )
    final_msg = {"content": "Трудовой договор создан по шаблону."}
    _scripted_functions(
        monkeypatch,
        [(search_msg, "s1"), (read_msg, "s2"), (create_msg, "s3"), (final_msg, None)],
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": "создай трудовой договор по шаблону трудового договора"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    names = [c["name"] for c in data["tool_calls"]]
    assert names == ["search_documents", "read_document", "create_document"], names

    hits = json.loads(data["tool_results"][0]["content"])
    assert hits[0]["document_id"] == template_id, "template must be the top hit"
    assert data["tool_calls"][1]["arguments"] == {"document_id": template_id}

    result = json.loads(data["tool_results"][-1]["content"])
    assert result["success"] is True
    created = _get_document(result["document_id"])
    assert created is not None
    assert created.user_id == user_id


def test_agent_creates_contract_using_doc_reference(client, monkeypatch, user_id):
    """'используя данные Doc_алексей': the referenced name is NOT in the
    library, but the agent must resolve it via a descriptive search and still
    create the contract from the actual data file — never claim the document
    is unavailable before searching."""
    data_id = _upload_data(client)

    search_msg = _tool_call_message(
        "search_documents", {"query": "данные сотрудника для трудового договора"}
    )
    read_msg = _tool_call_message("read_document", {"document_id": data_id})
    create_msg = _create_call(
        {
            "title": "Трудовой договор",
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        "Работник: Сергей Юрьевич Волков, "
                        "заместитель директора по производству."
                    ),
                }
            ],
        }
    )
    final_msg = {"content": "Трудовой договор создан по данным из ваших документов."}
    _scripted_functions(
        monkeypatch,
        [(search_msg, "s1"), (read_msg, "s2"), (create_msg, "s3"), (final_msg, None)],
    )

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={"question": "создай трудовой договор, используя данные Doc_алексей"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    names = [c["name"] for c in data["tool_calls"]]
    assert names == ["search_documents", "read_document", "create_document"], names

    hits = json.loads(data["tool_results"][0]["content"])
    assert hits[0]["document_id"] == data_id, "descriptive search must find the data file"
    assert DATA_NAME in data["tool_results"][0]["content"]

    result = json.loads(data["tool_results"][-1]["content"])
    assert result["success"] is True
    created = _get_document(result["document_id"])
    assert created is not None
    assert created.user_id == user_id
    assert "Сергей Юрьевич" in created.content


# --------------------------------------------------------- missing pieces


def test_agent_missing_template_answers_honestly(client, monkeypatch):
    """Only the data file exists: the agent searches for the template, finds
    no template, and must NOT fabricate a contract."""
    _upload_data(client)

    search_tpl = _tool_call_message("search_documents", {"query": "шаблон трудового договора"})
    search_data = _tool_call_message("search_documents", {"query": "данные для трудового договора сотрудника"})
    final = {"content": "Я не нашёл шаблон трудового договора в ваших документах, поэтому не могу его создать."}
    _scripted_functions(monkeypatch, [(search_tpl, "s1"), (search_data, "s2"), (final, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": USER_QUESTION})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # The template search did NOT return a template (only the data file can be
    # a weak semantic hit; that must not be mistaken for a template).
    assert TEMPLATE_NAME not in data["tool_results"][0]["content"]
    names = [c["name"] for c in data["tool_calls"]]
    assert names == ["search_documents", "search_documents"]
    assert "шаблон" in data["answer"]
    assert not any(c["name"] == "create_document" for c in data["tool_calls"])


def test_agent_missing_employee_data_answers_honestly(client, monkeypatch):
    """Only the template exists: no employee data -> the agent says so instead
    of creating a contract with invented numbers."""
    _upload_template(client)

    search_tpl = _tool_call_message("search_documents", {"query": "шаблон трудового договора"})
    search_data = _tool_call_message("search_documents", {"query": "данные для трудового договора сотрудника"})
    final = {"content": "В ваших документах нет данных о сотруднике — не могу заполнить договор."}
    _scripted_functions(monkeypatch, [(search_tpl, "s1"), (search_data, "s2"), (final, None)])

    resp = client.post(f"{API_PREFIX}/agent", json={"question": USER_QUESTION})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # The data search did NOT return employee data (a weak template hit is not data).
    assert DATA_NAME not in data["tool_results"][1]["content"]
    names = [c["name"] for c in data["tool_calls"]]
    assert names == ["search_documents", "search_documents"]
    assert "данных" in data["answer"]
    assert not any(c["name"] == "create_document" for c in data["tool_calls"])


def test_agent_asks_user_when_read_data_is_insufficient(client, monkeypatch):
    """Both documents exist and are read, but the data lacks the fields needed
    for a contract: the agent must ask the user AFTER reading, not invent the
    missing values."""
    template_id = _upload_template(client)
    sparse_data_id = _upload(
        client, DATA_NAME, "Данные сотрудника: Сергей Юрьевич Волков.".encode("utf-8")
    )

    script = [
        (_tool_call_message("search_documents", {"query": "шаблон трудового договора"}), "s1"),
        (_tool_call_message("search_documents", {"query": "данные для трудового договора сотрудника"}), "s2"),
        (_tool_call_message("read_document", {"document_id": template_id}), "s3"),
        (_tool_call_message("read_document", {"document_id": sparse_data_id}), "s4"),
        ({"content": "Уточните, пожалуйста, должность, оклад и подразделение сотрудника."}, None),
    ]
    _scripted_functions(monkeypatch, script)

    resp = client.post(f"{API_PREFIX}/agent", json={"question": USER_QUESTION})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    names = [c["name"] for c in data["tool_calls"]]
    assert names == [
        "search_documents",
        "search_documents",
        "read_document",
        "read_document",
    ]
    assert "должность" in data["answer"]
    assert not any(c["name"] == "create_document" for c in data["tool_calls"])


# ------------------------------------------------------------- prompt guard


def test_system_instruction_requires_search_before_claiming_missing_data():
    """The system prompt must mandate searching (including descriptive
    references) and forbid a premature 'no data' answer."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "search_documents" in text
    assert "read_document" in text
    assert "create_document" in text
    # Descriptive references -> a search query (never wait for exact names).
    assert "description" in text
    assert "file name" in text or "file_name" in text
    # Never claim data is missing before actually searching.
    assert "missing" in text or "absent" in text
    assert "before" in text
    assert "search" in text
    # Search several times if the first attempt finds nothing.
    assert "more" in text or "different" in text or "broader" in text
    # Search must come first — before reading or creating.
    assert "first search" in text


def test_system_instruction_does_not_force_tools_for_general_questions():
    """The prompt must tell the model to answer directly WITHOUT any tool when
    a question does not depend on the user's documents (greetings, small talk,
    general knowledge), so the agent does not search unnecessarily."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "do not call any tool" in text
    assert "greeting" in text or "small talk" in text
    assert "general knowledge" in text
    # The no-tools rule is the counterpart of the mandatory-search rule: both
    # directions (when documents are involved and when they are not) are stated.
    assert "search" in text
    assert "documents" in text


# -------------------------------------------------- create policy guard


def test_system_instruction_mandates_create_for_create_intents():
    """Explicit create/generate/prepare intents must map to create_document,
    and a plain-text refusal is forbidden for file requests."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "must call create_document" in text
    # The exact Russian policy sentence the task asked for is embedded.
    assert "не должны ограничиваться текстовым ответом" in text
    assert "сделай любой документ" in text


def test_system_instruction_allows_random_demo_data():
    """'рандомные данные' / 'любые данные' / 'сделай пример' explicitly permit
    fictional demo data; the agent must not ask the user for real data."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "рандомные данные" in text
    assert "любые данные" in text
    assert "сделай пример" in text
    assert "demo data" in text
    assert "do not ask the user for real data" in text


def test_system_instruction_defaults_any_document_to_docx():
    """'сгенерируй любой документ' without a format -> docx by default and a
    small demo document, no clarifying questions."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "сгенерируй любой документ" in text
    assert "docx" in text
    assert "by default" in text
    assert "do not ask clarifying questions" in text


def test_system_instruction_forbids_unsupported_formats():
    """PDF and other non-docx/odt formats must never be passed to
    create_document; the agent says the format is unsupported instead."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "unsupported format" in text
    assert "pdf" in text
    assert "do not call create_document" in text
    assert "docx or odt" in text


def test_system_instruction_distinguishes_answer_and_create():
    """The prompt must separate plain answers from file creation and from
    find-and-answer requests."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "расскажи, что такое трудовой договор" in text
    assert "сгенерируй трудовой договор" in text
    assert "plain answer" in text
    assert "search -> read -> create" in text
    assert "search -> read -> answer" in text


def test_system_instruction_forbids_claiming_unavailable_without_search():
    """The agent must not say a referenced document is unavailable before it
    has actually searched: an exact name missing from the message is not
    'no document'."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "не утверждайте, что данных нет, пока не попытались" in text
    assert "exact name" in text
    assert "unavailable" in text
    assert "before" in text
    assert "search" in text


def test_system_instruction_does_not_require_filenames_or_ids():
    """The agent must never make the user supply a filename or document_id
    when semantic search can find the document."""
    text = SYSTEM_INSTRUCTION.lower()
    assert "не просите пользователя вручную предоставлять filename" in text
    assert "document_id" in text
    assert "search_documents" in text


# --------------------------------------------------------------- security


def test_descriptive_search_never_leaks_other_users_documents(
    client, monkeypatch, register_user
):
    """User B's descriptive search must never return user A's abstract-named
    documents: the search runs scoped to the caller."""
    marker_a = f"TMPL{uuid.uuid4().hex[:6]}"
    template_id = _upload(
        client, TEMPLATE_NAME, _make_odt_bytes(f"{TEMPLATE_TEXT}\n{marker_a}")
    )
    data_id = _upload_data(client)

    search_msg = _tool_call_message("search_documents", {"query": "данные для трудового договора сотрудника"})
    final_msg = {"content": "Документы не найдены."}
    _scripted_functions(monkeypatch, [(search_msg, "s"), (final_msg, None)])

    with TestClient(app) as other:
        info = register_user(other)
        other.headers.update({"Authorization": f"Bearer {info['token']}"})
        resp = other.post(
            f"{API_PREFIX}/agent",
            json={"question": "данные для трудового договора сотрудника"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert len(data["tool_results"]) == 1
        content = data["tool_results"][0]["content"]
        assert content == "[]"
        assert TEMPLATE_NAME not in content
        assert DATA_NAME not in content
        assert str(template_id) not in content
        assert str(data_id) not in content
        assert marker_a not in content
