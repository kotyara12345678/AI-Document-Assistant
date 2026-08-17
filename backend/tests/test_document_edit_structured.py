"""Tests for the structured (text + table) PDF editing architecture.

The GigaChat client is mocked. These cover the core invariant:
    one logical input item  ->  exactly one logical output item
    one table               ->  exactly one table of the same dimensions
"""

import io
import json
import os
import uuid
from pathlib import Path

import fitz
import pytest

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.document import Document
from app.services import gemini
from app.services.document_edit import (
    _apply_pdf_edits,
    _items_to_apply,
    _pdf_unicode_font,
    _sanitize_lxshow,
    _verify_no_lxshow,
    edit_document,
)
from app.services.errors import DocumentEditError


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


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


def _make_pdf_with_table(rows: int, cols: int, data, with_image: bool = True) -> bytes:
    """Draw a ruled grid with cell text so PyMuPDF's find_tables detects it."""
    doc = fitz.open()
    page = doc.new_page()
    if with_image:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 24, 24), False)
        page.insert_image(fitz.Rect(300, 700, 360, 760), stream=pix.tobytes("png"))
    x0, y0 = 50, 50
    x1, y1 = 50 + cols * 90, 50 + rows * 30
    cw = (x1 - x0) / cols
    ch = (y1 - y0) / rows
    for r in range(rows + 1):
        yy = y0 + r * ch
        page.draw_line(fitz.Point(x0, yy), fitz.Point(x1, yy))
    for c in range(cols + 1):
        xx = x0 + c * cw
        page.draw_line(fitz.Point(xx, y0), fitz.Point(xx, y1))
    for r in range(rows):
        for c in range(cols):
            page.insert_text(fitz.Point(x0 + c * cw + 4, y0 + r * ch + 12), data[r][c])
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    content = buf.getvalue()
    doc.close()
    return content


def _structured_echo_fake(remove_lxshow: bool = False, transform=None):
    """Mock that echoes the structured input back (optionally editing text)."""

    def fake(prompt, system_instruction=None, **kwargs):
        # The real user prompt is prose + a trailing {"items": [...]} JSON; the
        # model returns JSON only, so extract just the JSON object here.
        idx = prompt.index('{"items"')
        payload = json.loads(prompt[idx:])
        items = payload["items"]
        out = []
        for it in items:
            if it["type"] == "text":
                t = it["text"].strip()
                if remove_lxshow:
                    t = t.replace("LXSHOW", "").strip()
                if transform:
                    t = transform(t)
                out.append({"id": it["id"], "type": "text", "text": t})
            else:
                cells = []
                for row in it["cells"]:
                    new_row = []
                    for c in row:
                        c = c.strip()
                        if remove_lxshow:
                            c = c.replace("LXSHOW", "").strip()
                        if transform:
                            c = transform(c)
                        new_row.append(c)
                    cells.append(new_row)
                out.append({"id": it["id"], "type": "table", "cells": cells})
        return json.dumps({"items": out})

    return fake


def _with_fake(items, fake):
    """Call _request_edits_structured while temporarily patching gemini."""
    import app.services.document_edit as de

    original = de.gemini.generate_answer
    de.gemini.generate_answer = fake
    try:
        return de._request_edits_structured(items, "translate")
    finally:
        de.gemini.generate_answer = original


def _fake_returning(payload_dict):
    def fake(prompt, system_instruction=None, **kwargs):
        return json.dumps(payload_dict)

    return fake


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #


def test_structured_text_block_count_and_order():
    items = [{"id": i, "type": "text", "text": f"block {i}"} for i in range(5)]
    result = _with_fake(items, _structured_echo_fake())
    assert len(result) == 5
    assert [r["id"] for r in result] == [0, 1, 2, 3, 4]
    assert [r["text"] for r in result] == [f"block {i}" for i in range(5)]


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #


def test_structured_table_preserves_dimensions():
    items = [
        {
            "id": 0,
            "type": "table",
            "cells": [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]],
        }
    ]
    result = _with_fake(items, _structured_echo_fake())
    assert len(result) == 1
    assert result[0]["type"] == "table"
    assert result[0]["cells"] == [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]]


def test_structured_table_lxshow_in_cell():
    items = [
        {
            "id": 0,
            "type": "table",
            "cells": [["LXSHOW Fiber Laser", "1000W"], ["Steel", "LXSHOW 2mm"]],
        }
    ]
    result = _with_fake(items, _structured_echo_fake(remove_lxshow=True))
    assert "LXSHOW" not in json.dumps(result)
    assert result[0]["cells"] == [["Fiber Laser", "1000W"], ["Steel", "2mm"]]


def test_structured_text_table_text_order():
    items = [
        {"id": 0, "type": "text", "text": "Intro"},
        {"id": 1, "type": "table", "cells": [["x", "y"], ["z", "w"]]},
        {"id": 2, "type": "text", "text": "Outro"},
    ]
    result = _with_fake(items, _structured_echo_fake())
    assert [r["id"] for r in result] == [0, 1, 2]
    assert result[0]["type"] == "text"
    assert result[1]["type"] == "table"
    assert result[2]["type"] == "text"
    assert result[1]["cells"] == [["x", "y"], ["z", "w"]]


# --------------------------------------------------------------------------- #
# Errors / validation
# --------------------------------------------------------------------------- #


def test_structured_fewer_items_raises():
    items = [{"id": i, "type": "text", "text": f"b{i}"} for i in range(5)]
    bad = {"items": [{"id": i, "type": "text", "text": f"b{i}"} for i in range(4)]}
    with pytest.raises(DocumentEditError):
        _with_fake(items, _fake_returning(bad))


def test_structured_more_items_raises():
    items = [{"id": i, "type": "text", "text": f"b{i}"} for i in range(5)]
    bad = {"items": [{"id": i, "type": "text", "text": f"b{i}"} for i in range(6)]}
    with pytest.raises(DocumentEditError):
        _with_fake(items, _fake_returning(bad))


def test_structured_reordered_ids_restore_order_by_id():
    """A bijective id permutation is bound by id, never by array position.

    The model labels every output with the id of the input item it edits; the
    array order is irrelevant, so a shuffled reply is reordered back onto the
    expected order and accepted (this replaces the old strict positional
    rejection of any id mismatch).
    """
    items = [{"id": 0, "type": "text", "text": "a"}, {"id": 1, "type": "text", "text": "b"}]
    shuffled = {"items": [{"id": 1, "type": "text", "text": "b-edit"}, {"id": 0, "type": "text", "text": "a-edit"}]}
    result = _with_fake(items, _fake_returning(shuffled))
    assert [r["id"] for r in result] == [0, 1]
    assert result[0]["text"] == "a-edit"
    assert result[1]["text"] == "b-edit"


def test_structured_duplicate_ids_rejected():
    """A duplicate id makes the mapping ambiguous -> never guessed -> error."""
    items = [{"id": 0, "type": "text", "text": "a"}, {"id": 1, "type": "text", "text": "b"}]
    bad = {"items": [{"id": 0, "type": "text", "text": "x"}, {"id": 0, "type": "text", "text": "y"}]}
    with pytest.raises(DocumentEditError):
        _with_fake(items, _fake_returning(bad))


def test_structured_table_invalid_reply_falls_back_to_passthrough():
    """A table whose reply fails validation is kept as extracted (best-effort).

    Tables are translated best-effort: never abort the whole document because a
    single table could not be validated. Text items remain strict (they raise).
    """
    items = [
        {"id": 0, "type": "text", "text": "intro"},
        {"id": 1, "type": "table", "cells": [["a", "b"], ["c", "d"]]},
    ]

    def fake(prompt, system_instruction=None, **kwargs):
        idx = prompt.index('{"items"')
        payload = json.JSONDecoder().raw_decode(prompt[idx:])[0]
        out = []
        for it in payload["items"]:
            if it["type"] == "text":
                out.append({"id": it["id"], "type": "text", "text": "вступление"})
            else:
                # Always-wrong grid dimensions -> reply rejected -> passthrough.
                out.append({"id": it["id"], "type": "table", "cells": [["x"]]})
        return json.dumps({"items": out})

    result = _with_fake(items, fake)
    assert result[0]["text"] == "вступление"
    assert result[1]["type"] == "table"
    assert result[1]["cells"] == [["a", "b"], ["c", "d"]]  # kept as extracted


def test_structured_retry_then_success():
    calls = {"n": 0}
    items = [{"id": i, "type": "text", "text": f"b{i}"} for i in range(3)]

    def fake(prompt, system_instruction=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"items": [{"id": 0, "type": "text", "text": "x"}]})  # bad
        return json.dumps({"items": [{"id": i, "type": "text", "text": f"b{i}"} for i in range(3)]})

    result = _with_fake(items, fake)
    assert calls["n"] == 2
    assert len(result) == 3


def test_structured_retry_invalid_then_error():
    bad = {"items": [{"id": 0, "type": "text", "text": "x"}]}
    with pytest.raises(DocumentEditError):
        _with_fake(
            [{"id": i, "type": "text", "text": f"b{i}"} for i in range(3)],
            _fake_returning(bad),
        )


# --------------------------------------------------------------------------- #
# Recovery / tolerance: stable ids, merge/skip/split, wrappers, bare strings
# --------------------------------------------------------------------------- #


def _bare_array_fake(elements=None, calls=None):
    """Fake that returns a bare JSON array; optional per-call hook ``calls(n)``."""
    counter = {"n": 0}

    def fake(prompt, system_instruction=None, **kwargs):
        counter["n"] += 1
        if calls is not None:
            return calls(counter["n"])
        return json.dumps(elements)

    return fake


def test_structured_exact_count_bare_strings_success():
    """Text-only chunk, bare array of strings, correct count -> success."""
    items = [{"id": i, "type": "text", "text": f"block {i}"} for i in range(3)]
    result = _with_fake(items, _bare_array_fake(["A", "B", "C"]))
    assert [r["id"] for r in result] == [0, 1, 2]
    assert [r["text"] for r in result] == ["A", "B", "C"]


def test_structured_wrapper_result_items():
    """Nested wrapper {"result": {"items": [...]}} is unwrapped and accepted."""
    items = [{"id": i, "type": "text", "text": f"block {i}"} for i in range(3)]
    payload = {
        "result": {
            "items": [
                {"id": 0, "type": "text", "text": "А"},
                {"id": 1, "type": "text", "text": "Б"},
                {"id": 2, "type": "text", "text": "В"},
            ]
        }
    }
    result = _with_fake(items, _fake_returning(payload))
    assert [r["text"] for r in result] == ["А", "Б", "В"]


def test_structured_n_minus_1_verbatim_merge_recovers():
    """Production scenario (expected 8 -> got 7): an adjacent verbatim merge is
    split back unambiguously instead of failing."""
    texts = [f"Строка {i}" for i in range(8)]
    items = [{"id": i, "type": "text", "text": texts[i]} for i in range(8)]
    merged = texts[:3] + [f"{texts[3]} {texts[4]}"] + texts[5:]
    result = _with_fake(items, _bare_array_fake(merged))
    assert len(result) == 8
    assert [r["id"] for r in result] == list(range(8))
    assert [r["text"] for r in result] == texts


def test_structured_n_minus_1_skipped_empty_recovers():
    """The model dropped the single empty item; it is restored in place."""
    texts = ["one", "", "two", "three"]
    items = [{"id": i, "type": "text", "text": texts[i]} for i in range(4)]
    result = _with_fake(items, _bare_array_fake(["one", "two", "three"]))
    assert len(result) == 4
    assert [r["text"] for r in result] == texts


def test_structured_n_minus_1_unrecoverable_retries_then_success():
    """A translated N-1 reply cannot be aligned deterministically -> targeted
    retry for the chunk -> the model returns N items -> success."""
    items = [{"id": i, "type": "text", "text": f"block {i}"} for i in range(5)]

    def calls(n):
        if n == 1:
            return json.dumps(["x", "y", "z", "w"])  # 4 != 5, non-recoverable
        return json.dumps(
            [{"id": i, "type": "text", "text": f"edit {i}"} for i in range(5)]
        )

    result = _with_fake(items, _bare_array_fake(calls=calls))
    assert len(result) == 5
    assert [r["text"] for r in result] == [f"edit {i}" for i in range(5)]


def test_structured_n_plus_1_verbatim_split_recovers():
    """One item split into two adjacent reply pieces is rejoined by content."""
    texts = ["alpha", "beta gamma", "omega"]
    items = [{"id": i, "type": "text", "text": texts[i]} for i in range(3)]
    result = _with_fake(items, _bare_array_fake(["alpha", "beta", "gamma", "omega"]))
    assert [r["text"] for r in result] == texts


def test_structured_n_plus_1_unrecoverable_raises():
    """An N+1 reply that no verbatim split explains is rejected, then errors."""
    items = [{"id": i, "type": "text", "text": f"b{i}"} for i in range(3)]
    with pytest.raises(DocumentEditError):
        _with_fake(items, _bare_array_fake(["a", "b", "c", "d"]))


def test_structured_exhausted_retry_raises_informative_error():
    """After the controlled retry the DocumentEditError names the chunk, the
    expected/actual counts and the reason - never the document body."""
    items = [{"id": i, "type": "text", "text": f"block {i}"} for i in range(8)]
    bad = {"items": [{"id": i, "type": "text", "text": f"block {i}"} for i in range(7)]}
    with pytest.raises(DocumentEditError) as exc:
        _with_fake(items, _fake_returning(bad))
    msg = str(exc.value)
    assert "chunk 0" in msg
    assert "expected 8" in msg
    assert "got 7" in msg
    assert "reason" in msg


def test_structured_recovery_never_writes_to_wrong_block():
    """After recovery every edited text binds to its ORIGINAL block (by id),
    so no text can ever land in the wrong PDF block."""
    items = [
        {"id": 0, "type": "text", "text": "AA", "frag_index": 0},
        {"id": 1, "type": "text", "text": "BB", "frag_index": 1},
        {"id": 2, "type": "text", "text": "CC", "frag_index": 2},
    ]
    # N-1 reply: items 0 and 1 merged verbatim, item 2 unchanged.
    result = _with_fake(items, _bare_array_fake(["AA BB", "CC"]))
    assert [r["id"] for r in result] == [0, 1, 2]

    fragments = [
        {"page": 0, "bbox": [0, 0, 10, 10], "text": "AA", "style": {}},
        {"page": 0, "bbox": [0, 0, 10, 10], "text": "BB", "style": {}},
        {"page": 0, "bbox": [0, 0, 10, 10], "text": "CC", "style": {}},
    ]
    blocks, texts = _items_to_apply(result, fragments)
    assert texts == ["AA", "BB", "CC"]
    assert blocks == fragments


# --------------------------------------------------------------------------- #
# Special regression: 30 logical items, one is a huge table
# --------------------------------------------------------------------------- #


def test_structured_30_logical_items_one_big_table():
    """30 logical items must stay 30 outputs; a big table is ONE item.

    This is the exact production failure: a multi-row table used to be flattened
    into independent text blocks, so the model returned e.g. 213 items for 30
    inputs. Now the table is a single structured item.
    """
    table_cells = [[f"r{r}c{c}" for c in range(3)] for r in range(200)]
    items = [{"id": i, "type": "text", "text": f"text {i}"} for i in range(29)]
    items.append({"id": 29, "type": "table", "cells": table_cells})

    result = _with_fake(items, _structured_echo_fake())
    assert len(result) == 30  # NOT 229 (29 + 200)
    assert result[29]["type"] == "table"
    assert len(result[29]["cells"]) == 200
    assert len(result[29]["cells"][0]) == 3


def test_structured_items_to_apply_table_maps_to_cells():
    fragments = [{"page": 0, "bbox": [0, 0, 10, 10], "text": "x", "style": {}}]
    edited = [
        {"id": 0, "type": "table", "page": 0, "style": {}, "cells": [["A", "B"], ["C", "D"]],
         "cell_rects": [[fitz.Rect(0, 0, 5, 5), fitz.Rect(5, 0, 10, 5)],
                        [fitz.Rect(0, 5, 5, 10), fitz.Rect(5, 5, 10, 10)]]},
    ]
    blocks, texts = _items_to_apply(edited, fragments)
    assert len(blocks) == 4
    assert texts == ["A", "B", "C", "D"]
    assert all(b["bbox"] == [r.x0, r.y0, r.x1, r.y1] for b, r in
               zip(blocks, [fitz.Rect(0, 0, 5, 5), fitz.Rect(5, 0, 10, 5),
                            fitz.Rect(0, 5, 5, 10), fitz.Rect(5, 5, 10, 10)]))


def test_structured_items_to_apply_skips_cell_without_geometry_or_none():
    """Cells without geometry, or left as None by the model, keep original text."""
    fragments = [{"page": 0, "bbox": [0, 0, 10, 10], "text": "x", "style": {}},
                 {"page": 0, "bbox": [0, 0, 20, 20], "text": "y", "style": {}}]
    edited = [
        {"id": 0, "type": "table", "page": 0, "style": {}, "cells": [[None, "B"]],
         "cell_rects": [[None, fitz.Rect(5, 0, 10, 5)]]},
        {"id": 1, "type": "text", "frag_index": 1, "text": "переведённый текст"},
    ]
    blocks, texts = _items_to_apply(edited, fragments)
    assert len(blocks) == 2
    assert texts == ["B", "переведённый текст"]
    assert blocks[1] is fragments[1]


# --------------------------------------------------------------------------- #
# PDF integration
# --------------------------------------------------------------------------- #


def _draw_table(page, data, x0, y0, cw=90, ch=30):
    rows = len(data)
    cols = len(data[0])
    x1, y1 = x0 + cols * cw, y0 + rows * ch
    for r in range(rows + 1):
        yy = y0 + r * ch
        page.draw_line(fitz.Point(x0, yy), fitz.Point(x1, yy))
    for c in range(cols + 1):
        xx = x0 + c * cw
        page.draw_line(fitz.Point(xx, y0), fitz.Point(xx, y1))
    for r in range(rows):
        for c in range(cols):
            page.insert_text(fitz.Point(x0 + c * cw + 4, y0 + r * ch + 12), data[r][c])


def test_pdf_translate_table_preserves_rows_cols_and_images(monkeypatch, user_id):
    data = [["Material", "Thickness", "Power"], ["Steel", "1 mm", "1000W"],
            ["Steel", "2 mm", "1500W"]]
    content = _make_pdf_with_table(3, 3, data, with_image=True)
    source_id, source_path = _seed_pdf(user_id, content)

    monkeypatch.setattr(gemini, "generate_answer", _structured_echo_fake(remove_lxshow=False))

    db = SessionLocal()
    try:
        result = edit_document(
            source_id, "РџРµСЂРµРІРµРґРё РЅР° СЂСѓСЃСЃРєРёР№, СѓР±РµСЂРё LXSHOW, PDF", user_id, db
        )
    finally:
        db.close()

    assert result["success"] is True
    assert source_path.read_bytes() == content  # original untouched

    new_doc = SessionLocal().query(Document).filter(Document.id == result["document_id"]).first()
    out = fitz.open(stream=Path(new_doc.filepath).read_bytes(), filetype="pdf")
    src = fitz.open(stream=content, filetype="pdf")
    assert out.page_count == src.page_count
    # image preserved
    assert len(out[0].get_images()) == len(src[0].get_images())
    # table still detected with same dimensions
    out_tables = out[0].find_tables().tables
    assert len(out_tables) == 1
    assert len(out_tables[0].extract()) == 3
    assert len(out_tables[0].extract()[0]) == 3
    out.close()
    src.close()


def test_pdf_lxshow_removed_from_table_cell(monkeypatch, user_id):
    data = [["LXSHOW Model", "Power"], ["LXSHOW X1", "1000W"]]
    content = _make_pdf_with_table(2, 2, data, with_image=False)
    source_id, source_path = _seed_pdf(user_id, content)

    monkeypatch.setattr(gemini, "generate_answer", _structured_echo_fake(remove_lxshow=True))

    db = SessionLocal()
    try:
        result = edit_document(source_id, "СѓР±РµСЂРё LXSHOW, PDF", user_id, db)
    finally:
        db.close()

    assert result["success"] is True
    new_doc = SessionLocal().query(Document).filter(Document.id == result["document_id"]).first()
    out = fitz.open(stream=Path(new_doc.filepath).read_bytes(), filetype="pdf")
    text = out[0].get_text()
    assert "LXSHOW" not in text
    out.close()


def test_pdf_multiple_tables_and_text(monkeypatch, user_id):
    t1 = [["Material", "Power"], ["Steel", "1000W"]]
    t2 = [["Speed", "Unit"], ["100", "m/min"]]
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text(fitz.Point(50, 40), "Cover page text.")
    _draw_table(p1, t1, 50, 60)
    p2 = doc.new_page()
    _draw_table(p2, t2, 50, 60)
    p2.insert_text(fitz.Point(50, 300), "Footer page text.")
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    content = buf.getvalue()
    doc.close()

    source_id, source_path = _seed_pdf(user_id, content)
    monkeypatch.setattr(gemini, "generate_answer", _structured_echo_fake(remove_lxshow=True))

    db = SessionLocal()
    try:
        result = edit_document(source_id, "СѓР±РµСЂРё LXSHOW, PDF", user_id, db)
    finally:
        db.close()

    assert result["success"] is True
    new_doc = SessionLocal().query(Document).filter(Document.id == result["document_id"]).first()
    out = fitz.open(stream=Path(new_doc.filepath).read_bytes(), filetype="pdf")
    assert out.page_count == 2
    assert len(out[0].find_tables().tables) == 1
    assert len(out[1].find_tables().tables) == 1
    out.close()


def test_pdf_original_unchanged_on_structured_error(monkeypatch, user_id):
    # The PDF has both a text item and a table item: a malformed LLM reply
    # (wrong item count/type) then trips the STRICT text-path validation.
    # (A table-only document would instead hit the best-effort table fallback,
    # which keeps the extracted table - see test_structured_table_invalid_reply
    # _falls_back_to_passthrough.)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 40), "Standalone paragraph to translate.")
    _draw_table(page, [["a", "b"], ["c", "d"]], 60, 50)
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    content = buf.getvalue()
    doc.close()

    source_id, source_path = _seed_pdf(user_id, content)

    bad = {"items": []}
    monkeypatch.setattr(gemini, "generate_answer", _fake_returning(bad))

    db = SessionLocal()
    try:
        with pytest.raises(DocumentEditError):
            edit_document(source_id, "translate, PDF", user_id, db)
    finally:
        db.close()

    assert source_path.read_bytes() == content  # original untouched on error



# --------------------------------------------------------------------------- #
# Regression: Cyrillic/Unicode PDF rendering
# --------------------------------------------------------------------------- #


def test_pdf_cyrillic_rendering():
    import fitz as _fitz

    doc = _fitz.open()
    doc.new_page()
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    content = buf.getvalue()
    doc.close()

    blocks = [
        {
            "page": 0,
            "bbox": [40, 40, 520, 220],
            "style": {"font": "Helvetica", "size": 14, "color": 0, "align": 0},
        }
    ]
    text = "РџСЂРѕРІРµСЂРєР° СЂСѓСЃСЃРєРѕРіРѕ С‚РµРєСЃС‚Р°: РџСЂРёРІРµС‚ РјРёСЂ"
    out = _apply_pdf_edits(content, blocks, [text])
    assert isinstance(out, (bytes, bytearray)) and len(out) > 0
    out_doc = _fitz.open(stream=out, filetype="pdf")
    try:
        extracted = out_doc[0].get_text()
        # Cyrillic must not be dropped to '?' (the old base-14 Helvetica bug).
        assert "?" not in extracted, f"Cyrillic dropped to '?': {extracted!r}"
        # A Unicode TTF (Type0 / Identity-H) must be embedded, not a base-14 font.
        fonts = out_doc[0].get_fonts(full=True)
        assert any(f[2] == "Type0" or f[1] == "ttf" for f in fonts), fonts
    finally:
        out_doc.close()


def test_pdf_unicode_fontfile_resolves_and_exists():
    # The chosen Unicode TTF must actually exist; otherwise the render would
    # silently fall back to a base-14 font and garble Cyrillic.
    path, name = _pdf_unicode_font("Helvetica")
    assert path is not None, (
        "No Unicode (Cyrillic) TTF found. Install fonts-dejavu-core in the "
        "Docker image or bundle backend/app/services/fonts/DejaVuSans.ttf."
    )
    assert os.path.isfile(path), f"resolved font file missing: {path}"


# --------------------------------------------------------------------------- #
# Regression: deterministic LXSHOW removal
# --------------------------------------------------------------------------- #


def test_lxshow_sanitization():
    assert _sanitize_lxshow("LXSHOW Laser") == ""
    assert _sanitize_lxshow("lxshow laser") == ""
    assert _sanitize_lxshow("LXSHOW Р»Р°Р·РµСЂ") in ("", "Р»Р°Р·РµСЂ")
    assert "LXSHOW" not in _sanitize_lxshow("Product LXSHOW Model X")
    # ordinary text is untouched
    assert _sanitize_lxshow("Normal text without brand") == "Normal text without brand"
    # word fragments must not be damaged
    assert _sanitize_lxshow("relaxshow") == "relaxshow"


def test_final_pdf_rejects_lxshow():
    import fitz as _fitz

    # PDF whose text layer contains LXSHOW -> must be rejected.
    doc = _fitz.open()
    doc.new_page()
    doc[0].insert_text(_fitz.Point(50, 50), "LXSHOW Laser machine", color=(0, 0, 0))
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    content = buf.getvalue()
    doc.close()
    with pytest.raises(DocumentEditError):
        _verify_no_lxshow(content=content)

    # Clean PDF -> accepted.
    doc = _fitz.open()
    doc.new_page()
    doc[0].insert_text(_fitz.Point(50, 50), "Clean laser machine", color=(0, 0, 0))
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    content2 = buf.getvalue()
    doc.close()
    _verify_no_lxshow(content=content2)  # must not raise


def test_final_pdf_lxshow_check_scoped_to_edited_text():
    # The final check must validate only the text we PRODUCED, not the whole
    # source PDF -- otherwise an unedited region that still mentions the brand
    # (e.g. a table we intentionally leave untouched) would wrongly reject a
    # perfectly good edit.
    # Edited text is clean -> accepted, even if the source PDF had LXSHOW.
    _verify_no_lxshow(texts=["Normal paragraph one.", "Translated clean text."])
    # Edited text leaks the brand -> rejected.
    with pytest.raises(DocumentEditError):
        _verify_no_lxshow(texts=["Normal text", "LXSHOW Laser model X"])


# --------------------------------------------------------------------------- #
# Regression: tables are excluded from LLM translation
# --------------------------------------------------------------------------- #


def test_tables_sent_to_llm_and_translated():
    """Tables now go to the LLM too (best-effort) and their cells are translated.

    The model sees BOTH the text and the table item; a table reply with the
    correct extracted grid dimensions is accepted and merged onto the geometry.
    """
    items = [
        {"id": 0, "type": "text", "text": "Hello world"},
        {
            "id": 1,
            "type": "table",
            "cells": [["LXSHOW part", "x"], ["y", "z"]],
            "cell_rects": [],
            "page": 0,
            "bbox": [0, 0, 10, 10],
            "style": {},
            "frag_indices": [],
        },
    ]
    captured = []

    def fake(prompt, system_instruction=None, **kwargs):
        idx = prompt.index('{"items"')
        payload = json.JSONDecoder().raw_decode(prompt[idx:])[0]
        captured.append(payload["items"])
        out = []
        for it in payload["items"]:
            if it["type"] == "text":
                out.append({"id": it["id"], "type": "text", "text": "Привет мир"})
            else:
                out.append(
                    {"id": it["id"], "type": "table", "cells": [["деталь", "х"], ["у", "з"]]}
                )
        return json.dumps({"items": out})

    import app.services.document_edit as de

    original = de.gemini.generate_answer
    de.gemini.generate_answer = fake
    try:
        result = de._request_edits_structured(items, "translate")
    finally:
        de.gemini.generate_answer = original

    by_id = {it["id"]: it for it in result}
    assert by_id[0]["text"] == "Привет мир"
    # Table cells translated and LXSHOW stripped deterministically.
    assert by_id[1]["type"] == "table"
    assert by_id[1]["cells"] == [["деталь", "х"], ["у", "з"]]
    # The model was actually called and saw the table item.
    assert any(any(it["type"] == "table" for it in call) for call in captured)
