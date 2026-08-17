"""Tests for structure-preserving PDF editing (PyMuPDF / fitz).

The editing path is exercised at two levels:

* ``_edit_pdf_with_edits`` applies a caller-supplied list of edited texts
  without involving the LLM or the database -- this verifies the geometry
  guarantees (images / vectors / tables preserved, old text removed, new text
  present, overflow handled, scans rejected).
* ``edit_document`` integration test drives the full service with the LLM call
  stubbed, and checks ownership, ``source_file_id`` and that the original file
  on disk is never modified.
"""

import io
import uuid
from pathlib import Path

import fitz
import pytest

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.document import Document
from app.services.document_edit import (
    _edit_pdf,
    _edit_pdf_with_edits,
    _chunk_items,
    EDIT_CHUNK_SIZE,
    edit_document,
)
from app.services.errors import DocumentEditError


# --------------------------------------------------------------------------- #
# PDF builders
# --------------------------------------------------------------------------- #


def _make_pdf(with_image: bool = True, with_table: bool = True) -> bytes:
    """A one-page PDF with two text blocks, an image and a 'table' vector."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 50), "Original paragraph one.")
    page.insert_text(fitz.Point(50, 80), "Original paragraph two.")
    if with_image:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 24, 24), False)
        page.insert_image(fitz.Rect(300, 700, 360, 760), stream=pix.tobytes("png"))
    if with_table:
        # a simple drawn rectangle that must survive editing
        page.draw_rect(fitz.Rect(50, 120, 300, 160))
    src = io.BytesIO()
    doc.save(src, garbage=4, deflate=True)
    content = src.getvalue()
    doc.close()
    return content


def _make_scanned_pdf() -> bytes:
    """Image-only PDF with no text layer -> editing must be refused."""
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50), False)
    page.insert_image(fitz.Rect(40, 40, 200, 200), stream=pix.tobytes("png"))
    src = io.BytesIO()
    doc.save(src, garbage=4, deflate=True)
    content = src.getvalue()
    doc.close()
    return content


# --------------------------------------------------------------------------- #
# Unit-level behaviour (no DB, no LLM)
# --------------------------------------------------------------------------- #


def test_pdf_edit_preserves_images_tables_and_replaces_text():
    content = _make_pdf()
    before = fitz.open(stream=content, filetype="pdf")
    n_images = len(before[0].get_images())
    n_drawings = len(before[0].get_drawings())
    before.close()

    edited = _edit_pdf_with_edits(
        content,
        ["SHORTENED ONE.", "A MUCH LONGER REWRITTEN PARAGRAPH THAT OVERFLOWS."],
    )

    out = fitz.open(stream=edited, filetype="pdf")
    text = out[0].get_text()
    # images + vector table survive
    assert len(out[0].get_images()) == n_images
    assert len(out[0].get_drawings()) == n_drawings
    # old text gone, new text present
    assert "Original paragraph one." not in text
    assert "Original paragraph two." not in text
    assert "SHORTENED ONE." in text
    # longer text is kept (may wrap, so check individual words)
    assert "REWRITTEN" in text and "OVERFLOWS" in text and "PARAGRAPH" in text
    out.close()


def test_pdf_edit_keeps_same_page_count_and_format():
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc[0].insert_text(fitz.Point(50, 50), "Page one text.")
    doc[1].insert_text(fitz.Point(50, 50), "Page two text.")
    src = io.BytesIO()
    doc.save(src, garbage=4, deflate=True)
    content = src.getvalue()
    doc.close()

    edited = _edit_pdf_with_edits(content, ["ONE EDITED.", "TWO EDITED."])
    out = fitz.open(stream=edited, filetype="pdf")
    assert out.page_count == 2
    combined = "\n".join(out[p].get_text() for p in range(out.page_count))
    assert "ONE EDITED." in combined and "TWO EDITED." in combined
    out.close()


def test_pdf_scanned_without_text_layer_is_rejected():
    content = _make_scanned_pdf()
    with pytest.raises(DocumentEditError):
        _edit_pdf_with_edits(content, ["whatever"])


def test_pdf_edit_handles_unbreakable_long_token_without_losing_text():
    """A single token wider than the box must still be inserted (shrunk),
    never silently dropped."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 50), "Original paragraph two.")
    src = io.BytesIO()
    doc.save(src, garbage=4, deflate=True)
    content = src.getvalue()
    doc.close()

    long_token = "A_MUCH_LONGER_REWRITTEN_PARAGRAPH_THAT_OVERFLOWS_THE_ORIGINAL_BOX. " * 2
    edited = _edit_pdf_with_edits(content, [long_token])
    out = fitz.open(stream=edited, filetype="pdf")
    text = out[0].get_text()
    assert "A_MUCH_LONGER_REWRITTEN_PARAGRAPH" in text
    assert "Original paragraph two." not in text
    out.close()


# --------------------------------------------------------------------------- #
# Full-service integration (LLM stubbed)
# --------------------------------------------------------------------------- #


def _seed_document(user_id: int, data: bytes, file_type: str = "pdf") -> tuple[int, Path]:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.{file_type}"
    filepath = upload_dir / stored_name
    filepath.write_bytes(data)

    db = SessionLocal()
    try:
        doc = Document(
            user_id=user_id,
            filename=stored_name,
            original_filename=f"doc.{file_type}",
            file_type=file_type,
            file_size=len(data),
            filepath=str(filepath),
            content="seed content",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id, filepath
    finally:
        db.close()


def test_edit_document_pdf_integrates_and_keeps_original(monkeypatch, client, identity):
    content = _make_pdf()
    source_id, source_path = _seed_document(identity.user_id, content)

    # Stub the LLM step (structured path) with a deterministic rewrite of every
    # logical item. This exercises the real extraction -> chunk -> apply pipeline
    # without depending on the live GigaChat API.
    def fake_request_edits_structured(items, instruction, chunk_size=EDIT_CHUNK_SIZE):
        out = []
        for it in items:
            new = dict(it)
            if it["type"] == "text":
                new["text"] = f"[EDITED] {it.get('text', '')}"
            else:
                new["cells"] = [
                    [f"[EDITED] {c}" if isinstance(c, str) else c for c in row]
                    for row in it.get("cells", [])
                ]
            out.append(new)
        return out

    monkeypatch.setattr(
        "app.services.document_edit._request_edits_structured", fake_request_edits_structured
    )

    db = SessionLocal()
    try:
        result = edit_document(source_id, "improve the text", identity.user_id, db)
    finally:
        db.close()

    assert result["success"] is True
    assert result["source_file_id"] == source_id
    assert result["file_type"] == "pdf"

    # Original file on disk is byte-for-byte unchanged.
    assert source_path.read_bytes() == content

    # The produced document really contains the edited text.
    new_doc = (
        SessionLocal()
        .query(Document)
        .filter(Document.id == result["document_id"])
        .first()
    )
    assert new_doc is not None
    produced = fitz.open(stream=Path(new_doc.filepath).read_bytes(), filetype="pdf")
    assert "[EDITED]" in produced[0].get_text()
    produced.close()


def test_edit_document_pdf_refuses_other_user(monkeypatch, client, identity):
    from tests.conftest import _register_user

    content = _make_pdf()
    source_id, _ = _seed_document(identity.user_id, content)
    other = _register_user(client)

    monkeypatch.setattr(
        "app.services.document_edit._request_edits",
        lambda blocks, instruction: [f"[EDITED] {b}" for b in blocks],
    )

    db = SessionLocal()
    try:
        with pytest.raises(DocumentEditError):
            edit_document(source_id, "improve the text", other["user_id"], db)
    finally:
        db.close()


def test_chunk_size_is_8_and_items_grouped():
    """The structured edit path must keep EDIT_CHUNK_SIZE == 8 and group items
    by that count (a table is one item, never split into per-row blocks)."""
    assert EDIT_CHUNK_SIZE == 8
    items = [{"id": i, "type": "text", "text": "x"} for i in range(60)]
    chunks = _chunk_items(items)
    assert all(len(c) <= 8 for c in chunks)
    # 60 items / 8 per chunk -> 8 chunks (7*8 + 4).
    assert len(chunks) == 8
    assert sum(len(c) for c in chunks) == 60


def test_edit_pdf_aborts_without_partial_output_on_chunk_failure(monkeypatch):
    """If a chunk ultimately fails (network/timeout), the whole edit aborts and
    no (partially broken) PDF is produced -- the original stays untouched."""
    content = _make_pdf()

    def _boom(items, instruction, chunk_size=EDIT_CHUNK_SIZE):
        raise DocumentEditError("simulated chunk failure")

    monkeypatch.setattr(
        "app.services.document_edit._request_edits_structured", _boom
    )
    with pytest.raises(DocumentEditError):
        _edit_pdf(content, "translate everything")
    # The call never returned bytes, so nothing was written back over the source.


def test_system_prompt_allows_pdf_editing_and_keeps_format():
    """The agent must route an edit/translate of a PDF to edit_document and keep
    PDF as the output format. The prompt must not contain the old refusal text
    ('PDF not supported / cannot be edited / only analyse / use DOCX/ODT')."""
    from app.services.agent import SYSTEM_INSTRUCTION

    low = SYSTEM_INSTRUCTION.lower()
    # Forbidden refusal language is gone.
    assert "pdf is not supported" not in low
    assert "pdf cannot be edited" not in low
    assert "pdf can only be analyzed" not in low
    assert "do not call create_document with pdf" not in low
    # The real capability is stated: editing a PDF keeps/returns PDF.
    assert "editing a pdf yields a pdf" in low
    assert "returns a new pdf" in low
    # And the edit tool must not be described as PDF-incapable.
    from app.services.agent import EDIT_FUNCTION

    assert "pdf" in EDIT_FUNCTION["description"].lower()
    assert "docx, odt, pdf, txt, md" in EDIT_FUNCTION["description"].lower()
