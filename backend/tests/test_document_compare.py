"""Tests for document/version comparison: service, API and agent tool.

Covers the two comparison use cases the feature supports:
* any two documents (`POST /api/documents/compare`);
* the version chain of a document (`GET /api/documents/{id}/versions`),
  which exists in the data model via `documents.source_file_id`.

The compare API is owner-scoped: another user's document must be impossible
to compare or enumerate.
"""

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.document import Document
from app.services import document_compare as compare_service
from app.services.agent import agent_service

API_PREFIX = "/api"

ORIGINAL = [
    "Заголовок",
    "Первый абзац документа.",
    "Второй абзац с цифрами: 123 и 456.",
    "Строка, которая останется без изменений.",
    "Итоговая строка.",
]
EDITED = [
    "Заголовок",
    "Первый абзац документа.",
    "Второй абзац: числа изменены на 999 и 1000.",
    "Строка, которая останется без изменений.",
    "Итоговая строка.",
]


def _upload(client: TestClient, filename: str, lines: list[str]) -> int:
    resp = client.post(
        f"{API_PREFIX}/documents/upload",
        files={"file": (filename, b"\n".join(line.encode() for line in lines))},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]["id"]


def _session():
    """Open a fresh session so service-level calls share the test database."""
    return SessionLocal()


def _seed_document(
    user_id: int,
    *,
    lines: list[str],
    file_type: str = "txt",
    source_file_id: int | None = None,
) -> int:
    """Create a Document row directly (no indexing side effects)."""
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.{file_type}"
    filepath = upload_dir / stored_name
    filepath.write_bytes(b"\n".join(line.encode() for line in lines))

    db = SessionLocal()
    try:
        doc = Document(
            user_id=user_id,
            filename=stored_name,
            original_filename=f"doc-{uuid.uuid4().hex[:6]}.{file_type}",
            file_type=file_type,
            file_size=0,
            filepath=str(filepath),
            content="\n".join(lines),
            source_file_id=source_file_id,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id
    finally:
        db.close()


# ---------------------------------------------------------------- unit: compute_diff

def _join(lines: list[str]) -> str:
    return "\n".join(lines)


def test_compute_diff_equal_texts():
    result = compare_service.compute_diff(_join(ORIGINAL), _join(ORIGINAL))
    assert result["equal"] is True
    assert result["summary"]["added_lines"] == 0
    assert result["summary"]["removed_lines"] == 0
    assert result["summary"]["changed_lines"] == 0
    assert result["summary"]["unchanged_lines"] == len(ORIGINAL)
    assert result["truncated"] is False


def test_compute_diff_finds_one_changed_line():
    result = compare_service.compute_diff(_join(ORIGINAL), _join(EDITED))
    assert result["equal"] is False
    assert result["summary"]["removed_lines"] == 1
    assert result["summary"]["added_lines"] == 1
    assert result["summary"]["changed_lines"] == 1
    assert result["summary"]["unchanged_lines"] == 4

    kinds = [op["kind"] for op in result["operations"]]
    assert "equal" in kinds
    assert "replace" in kinds


def test_compute_diff_line_numbering_is_consistent():
    left = ["a", "b", "c", "d"]
    right = ["a", "x", "x", "d"]
    result = compare_service.compute_diff("\n".join(left), "\n".join(right))
    for op in result["operations"]:
        assert op["left_start"] <= op["left_end"]
        assert op["right_start"] <= op["right_end"]
        assert result["left_lines"][op["left_start"] : op["left_end"]] is not None
        assert result["right_lines"][op["right_start"] : op["right_end"]] is not None
    added = result["left_lines"][0:2]
    assert added == ["a", "b"]


def test_compute_diff_empty_and_none():
    result = compare_service.compute_diff(None, "")
    assert result["equal"] is True
    assert result["left_lines"] == []
    assert result["right_lines"] == []


def test_compute_diff_crlf_normalised():
    result = compare_service.compute_diff("a\r\nb\r\n", "a\nb\n")
    assert result["equal"] is True


# ---------------------------------------------------------------- service: versions

def test_versions_single_document(client, user_id):
    doc_id = _upload(client, "a.txt", ["line 1"])
    with _session() as db:
        versions = compare_service.document_versions(doc_id, user_id, db)
    assert len(versions) == 1
    assert versions[0].id == doc_id
    assert versions[0].source_file_id is None


def test_versions_chain_oldest_first(user_id):
    original_id = _seed_document(user_id, lines=["v1"])
    edit1_id = _seed_document(
        user_id, lines=["v1", "edited"], source_file_id=original_id
    )
    edit2_id = _seed_document(
        user_id, lines=["v1", "edited", "again"], source_file_id=edit1_id
    )

    with _session() as db:
        versions = compare_service.document_versions(edit2_id, user_id, db)
    assert [v.id for v in versions] == [original_id, edit1_id, edit2_id]


def test_versions_of_unknown_document_raises_404(client, user_id):
    with _session() as db:
        with pytest.raises(Exception) as exc:
            compare_service.document_versions(999_999, user_id, db)
    assert getattr(exc.value, "status_code", 404) == 404


# ---------------------------------------------------------------- API: compare

def test_compare_two_documents(client):
    left_id = _upload(client, "left.txt", ORIGINAL)
    right_id = _upload(client, "right.txt", EDITED)

    resp = client.post(
        f"{API_PREFIX}/documents/compare",
        json={"left_id": left_id, "right_id": right_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["left"]["id"] == left_id
    assert data["right"]["id"] == right_id
    assert data["equal"] is False
    assert data["summary"]["added_lines"] == 1
    assert data["summary"]["removed_lines"] == 1
    assert data["summary"]["changed_lines"] == 1
    assert data["summary"]["unchanged_lines"] == 4
    assert len(data["operations"]) >= 3
    assert data["limit"] == compare_service.MAX_COMPARE_LINES
    assert data["truncated"] is False


def test_compare_identical_documents(client):
    doc_id = _upload(client, "same.txt", ORIGINAL)
    resp = client.post(
        f"{API_PREFIX}/documents/compare",
        json={"left_id": doc_id, "right_id": doc_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["equal"] is True


def test_compare_refuses_missing_document(client):
    doc_id = _upload(client, "ok.txt", ORIGINAL)
    resp = client.post(
        f"{API_PREFIX}/documents/compare",
        json={"left_id": doc_id, "right_id": 999_999},
    )
    assert resp.status_code == 404, resp.text


def test_compare_is_scoped_to_owner(client, register_user):
    left_id = _upload(client, "mine.txt", ORIGINAL)

    other = register_user(client)
    other_headers = {"Authorization": f"Bearer {other['token']}"}
    resp = client.post(
        f"{API_PREFIX}/documents/compare",
        json={"left_id": left_id, "right_id": left_id},
        headers=other_headers,
    )
    assert resp.status_code == 404, resp.text


def test_compare_requires_auth():
    from app.main import app

    with TestClient(app) as c:
        resp = c.post(
            f"{API_PREFIX}/documents/compare",
            json={"left_id": 1, "right_id": 2},
        )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------- API: versions

def test_api_versions_chain(client, user_id):
    def _lines(text: list[str]) -> str:
        return "\n".join(text)

    original_id = _seed_document(user_id, lines=["v1"])
    edit1_id = _seed_document(
        user_id, lines=["v1", "edited"], source_file_id=original_id
    )

    resp = client.get(f"{API_PREFIX}/documents/{edit1_id}/versions")
    assert resp.status_code == 200, resp.text
    ids = [v["id"] for v in resp.json()]
    assert ids == [original_id, edit1_id]
    assert resp.json()[0]["source_file_id"] is None
    assert resp.json()[-1]["source_file_id"] == original_id


def test_api_versions_scoped_to_owner(client, register_user):
    doc_id = _upload(client, "mine.txt", ["x"])
    other = register_user(client)
    other_headers = {"Authorization": f"Bearer {other['token']}"}
    resp = client.get(
        f"{API_PREFIX}/documents/{doc_id}/versions", headers=other_headers
    )
    assert resp.status_code == 404, resp.text


def test_api_versions_requires_auth():
    from app.main import app

    with TestClient(app) as c:
        resp = c.get(f"{API_PREFIX}/documents/1/versions")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------- agent tool

def test_agent_compare_tool_executes(client, user_id):
    """compare_documents runs against the real compare service and returns a
    compact, bounded summary (refs + counts + a few changed lines)."""
    left_id = _upload(client, "left.txt", ORIGINAL)
    right_id = _upload(client, "right.txt", EDITED)

    content = agent_service._execute_tool(
        "compare_documents",
        {"left_id": left_id, "right_id": right_id},
        user_id,
        None,
        db=None,
    )
    data = json.loads(content)

    assert "error" not in data
    assert data["left"]["id"] == left_id
    assert data["right"]["id"] == right_id
    assert data["equal"] is False
    assert data["summary"]["changed_lines"] == 1
    assert data["summary"]["unchanged_lines"] == 4
    # The model must never receive the whole text: only bounded changed blocks.
    assert isinstance(data["changed_blocks"], list)
    assert len(data["changed_blocks"]) >= 1
    for block in data["changed_blocks"]:
        assert "left" in block and "right" in block


def test_agent_compare_tool_missing_doc_is_safe_error(client, user_id):
    doc_id = _upload(client, "left.txt", ORIGINAL)
    content = agent_service._execute_tool(
        "compare_documents",
        {"left_id": doc_id, "right_id": 999_999},
        user_id,
        None,
        db=None,
    )
    data = json.loads(content)
    assert "error" in data


def test_agent_compare_tool_bad_arguments(client, user_id):
    content = agent_service._execute_tool(
        "compare_documents", {"left_id": "abc"}, user_id, None, db=None
    )
    data = json.loads(content)
    assert "error" in data


def test_agent_functions_spec_advertises_compare(monkeypatch):
    """The tool must be advertised so the model can be driven to call it."""
    names = [fn["name"] for fn in agent_service.functions_spec()]
    assert "compare_documents" in names