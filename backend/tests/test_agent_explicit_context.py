"""Regression tests: an explicitly pinned document must drive the edit target.

The GigaChat client is mocked at the ``chat_with_functions`` boundary. These
tests prove that when exactly one document is pinned via ``context_document_ids``
the agent edits THAT document directly -- no ``search_documents``, no read of any
other file. Explicit context > RAG, enforced at the backend, not just in the
prompt.
"""
import io
import json
import uuid
from pathlib import Path

import fitz

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.document import Document
from app.services import gemini


API_PREFIX = "/api"


def _make_pdf_with_text(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 50), text)
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    content = buf.getvalue()
    doc.close()
    return content


def _seed_pdf(user_id: int, data: bytes) -> tuple[int, Path]:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.pdf"
    path = upload_dir / stored
    path.write_bytes(data)
    db = SessionLocal()
    try:
        doc = Document(
            user_id=user_id,
            filename=stored,
            original_filename="manual.pdf",
            file_type="pdf",
            file_size=len(data),
            filepath=str(path),
            content="pdf seed",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id, path
    finally:
        db.close()


def _insert_document(user_id: int, filename: str, content: str, file_type: str = "txt") -> int:
    db = SessionLocal()
    try:
        doc = Document(
            user_id=user_id,
            filename=f"{uuid.uuid4().hex}.{file_type}",
            original_filename=filename,
            file_type=file_type,
            file_size=len(content.encode("utf-8")),
            filepath="/data/uploads/placeholder",
            content=content,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id
    finally:
        db.close()


def _scripted_functions(monkeypatch, script):
    """Queue (message, state_id) pairs; only consulted if the model is reached."""

    def fake(messages, functions=None, function_call="auto", functions_state_id=None, client=None, usage_hook=None):
        if not script:
            return {"content": "ok"}, None
        return script.pop(0)

    monkeypatch.setattr(gemini, "chat_with_functions", fake)
    return fake


def test_single_pinned_pdf_edited_directly_without_search(client, monkeypatch, identity):
    """A single pinned PDF is edited directly: no search, no read of another file."""
    pdf_id, pdf_path = _seed_pdf(identity.user_id, _make_pdf_with_text("LXSHOW manual content."))
    original = pdf_path.read_bytes()

    monkeypatch.setattr(
        "app.services.document_edit._request_edits",
        lambda blocks, instruction: [f"[EDITED] {b}" for b in blocks],
    )
    # Guard: if the model were ever consulted it must not change the outcome.
    _scripted_functions(monkeypatch, [({"content": "ignored"}, None)])

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={
            "question": (
                "Переведи руководство пользователя на русский язык. Убери LXSHOW. Верни PDF."
            ),
            "context_document_ids": [pdf_id],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    tool_names = {c["name"] for c in data["tool_calls"]}
    assert tool_names == {"edit_document"}, tool_names
    assert data["tool_calls"][0]["arguments"].get("file_id") == pdf_id

    result = json.loads(data["tool_results"][0]["content"])
    assert result["success"] is True
    assert result["file_type"] == "pdf"
    assert result["source_file_id"] == pdf_id
    # Original on disk is untouched.
    assert pdf_path.read_bytes() == original


def test_unpinned_doc_never_selected_when_one_pinned(client, monkeypatch, identity):
    """A library doc (Savvaland.txt) must never be read/searched when a PDF is pinned."""
    pdf_id, pdf_path = _seed_pdf(identity.user_id, _make_pdf_with_text("LXSHOW manual content."))
    decoy_id = _insert_document(identity.user_id, "Savvaland.txt", "unrelated text", file_type="txt")
    original_pdf = pdf_path.read_bytes()

    monkeypatch.setattr(
        "app.services.document_edit._request_edits",
        lambda blocks, instruction: [f"[EDITED] {b}" for b in blocks],
    )
    _scripted_functions(monkeypatch, [({"content": "ignored"}, None)])

    resp = client.post(
        f"{API_PREFIX}/agent",
        json={
            "question": "Переведи руководство пользователя на русский язык. Убери LXSHOW. Верни PDF.",
            "context_document_ids": [pdf_id],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    tool_names = {c["name"] for c in data["tool_calls"]}
    assert tool_names == {"edit_document"}, tool_names
    assert data["tool_calls"][0]["arguments"].get("file_id") == pdf_id

    # The decoy library document must never appear anywhere in the trace.
    assert "Savvaland" not in str(data)
    assert str(decoy_id) not in str(data)

    result = json.loads(data["tool_results"][0]["content"])
    assert result["source_file_id"] == pdf_id
    assert pdf_path.read_bytes() == original_pdf
