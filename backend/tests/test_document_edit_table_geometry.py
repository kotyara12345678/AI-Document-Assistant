"""Regression tests for irregular / partially-detected PDF table geometry.

The previous bug: in ``_extract_pdf_structure`` the table cell geometry was
rebuilt with ``flat[r * n_cols + c]`` which assumes ``len(table.cells) ==
n_rows * n_cols``. Real PDFs with merged / missing / ragged cells break that
assumption and raise ``IndexError`` before the LLM is ever called.

These tests pin the corrected behaviour:
  * the matrix is always rectangular (n_rows x n_cols),
  * cells without geometry become ``None`` (never a fabricated bbox),
  * no ``IndexError`` is ever raised,
  * existing cells keep their rects.
"""

import io

import fitz

from app.services.document_edit import _build_table_cell_rects, _extract_pdf_structure


# --------------------------------------------------------------------------- #
# Fake PyMuPDF table objects (so we can drive exact geometries without a real
# irregular PDF, which find_tables cannot be forced to emit on demand).
# --------------------------------------------------------------------------- #


class _FakeCell:
    """Mimics a PyMuPDF Cell object returned by ``to_cells()``."""

    def __init__(self, r, c, bbox):
        self.row = r
        self.col = c
        self.bbox = bbox


class _FakeTableToCells:
    """A table whose geometry is exposed via ``to_cells()`` (explicit row/col)."""

    def __init__(self, rc_bboxes):
        # rc_bboxes: iterable of (r, c, (x0, y0, x1, y1))
        self._rc = list(rc_bboxes)

    def to_cells(self):
        return [_FakeCell(r, c, bbox) for (r, c, bbox) in self._rc]

    @property
    def cells(self):
        # Also expose the flat list (only the cells we have) for completeness.
        return [bbox for (_, _, bbox) in self._rc]


class _FakeTableFlat:
    """A table whose geometry is only the flat ``cells`` list (older PyMuPDF)."""

    def __init__(self, flat_cells, bbox=(0, 0, 100, 100)):
        self._flat = list(flat_cells)
        self._bbox = bbox

    @property
    def cells(self):
        return self._flat

    @property
    def bbox(self):
        return self._bbox


# --------------------------------------------------------------------------- #
# _build_table_cell_rects unit tests
# --------------------------------------------------------------------------- #


def test_build_cell_rects_normal_grid_is_rectangular():
    cells = [["c" for _ in range(4)] for _ in range(3)]  # 3x4
    flat = [(c * 10, r * 10, c * 10 + 8, r * 10 + 8) for r in range(3) for c in range(4)]
    table = _FakeTableFlat(flat)
    matrix = _build_table_cell_rects(cells, table, page_index=0)
    assert len(matrix) == 3
    assert all(len(row) == 4 for row in matrix)
    assert all(isinstance(rect, fitz.Rect) for row in matrix for rect in row)
    # bbox preserved
    assert matrix[1][2].x0 == 20 and matrix[1][2].y0 == 10


def test_build_cell_rects_shape_mismatch_no_indexerror():
    """The exact doc #848 scenario: 4x5 logical grid but only 17 detected rects."""
    cells = [["x" for _ in range(5)] for _ in range(4)]  # 20 text cells
    # Only 17 rects present -> the old code raised IndexError at cell 17.
    flat = [(i, i, i + 1, i + 1) for i in range(17)]
    table = _FakeTableFlat(flat)
    matrix = _build_table_cell_rects(cells, table, page_index=0)
    assert len(matrix) == 4
    assert all(len(row) == 5 for row in matrix)
    rects = [rect for row in matrix for rect in row]
    assert sum(1 for r in rects if isinstance(r, fitz.Rect)) == 17
    assert sum(1 for r in rects if r is None) == 3


def test_build_cell_rects_missing_cells_are_none_not_fake_bbox():
    cells = [["x" for _ in range(5)] for _ in range(4)]
    flat = [(i, i, i + 1, i + 1) for i in range(17)]  # 3 missing
    table = _FakeTableFlat(flat)
    matrix = _build_table_cell_rects(cells, table, page_index=0)
    # Every missing cell must be None - we must NOT invent coordinates.
    for r in range(4):
        for c in range(5):
            idx = r * 5 + c
            if idx >= 17:
                assert matrix[r][c] is None


def test_build_cell_rects_ragged_rows_stay_rectangular():
    cells = [["a", "b", "c", "d"], ["e", "f", "g"], ["h", "i", "j", "k"]]
    n_cols = 4
    # (row, col, bbox) for every present cell.
    rc = []
    for r, row in enumerate(cells):
        for c, _ in enumerate(row):
            rc.append((r, c, (c, r, c + 1, r + 1)))
    table = _FakeTableToCells(rc)
    matrix = _build_table_cell_rects(cells, table, page_index=0)
    assert len(matrix) == 3
    assert all(len(row) == n_cols for row in matrix)
    # row 1, col 3 is missing -> None
    assert matrix[1][3] is None
    assert isinstance(matrix[0][0], fitz.Rect)
    assert isinstance(matrix[2][3], fitz.Rect)


def test_build_cell_rects_to_cells_path_maps_explicit_coords():
    cells = [["a", "b"], ["c", "d"]]
    rc = [(0, 0, (0, 0, 9, 9)), (0, 1, (10, 0, 19, 9)), (1, 0, (0, 10, 9, 19))]
    # cell (1,1) intentionally absent -> None
    table = _FakeTableToCells(rc)
    matrix = _build_table_cell_rects(cells, table, page_index=0)
    assert isinstance(matrix[0][0], fitz.Rect)
    assert isinstance(matrix[0][1], fitz.Rect)
    assert isinstance(matrix[1][0], fitz.Rect)
    assert matrix[1][1] is None


# --------------------------------------------------------------------------- #
# End-to-end: _extract_pdf_structure must not crash on an irregular table
# --------------------------------------------------------------------------- #


def _make_simple_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 50), "Standalone paragraph.")
    page.insert_text(fitz.Point(50, 80), "Second paragraph.")
    src = io.BytesIO()
    doc.save(src, garbage=4, deflate=True)
    content = src.getvalue()
    doc.close()
    return content


class _IrregularTable:
    """extract() reports a 4x5 grid, but only 17 cell rects exist."""

    def extract(self):
        return [["x" for _ in range(5)] for _ in range(4)]

    @property
    def bbox(self):
        return (0, 0, 100, 100)

    @property
    def cells(self):
        return [(i, i, i + 1, i + 1) for i in range(17)]


class _TablesFinder:
    tables = [_IrregularTable()]


def test_extract_pdf_structure_irregular_table_no_indexerror(monkeypatch):
    content = _make_simple_pdf()

    def fake_find_tables(self):
        return _TablesFinder()

    monkeypatch.setattr(fitz.Page, "find_tables", fake_find_tables)

    # Must not raise IndexError.
    fragments, items = _extract_pdf_structure(content)

    table_items = [it for it in items if it["type"] == "table"]
    assert table_items, "irregular table should still be a logical table item"
    ti = table_items[0]
    # Rectangular matrix preserved; geometry length matches the text grid.
    assert len(ti["cell_rects"]) == len(ti["cells"]) == 4
    assert all(len(row) == 5 for row in ti["cell_rects"])
    present = sum(1 for row in ti["cell_rects"] for r in row if r is not None)
    assert present == 17
    # A text item still exists (the standalone paragraph), proving the logical
    # chunking is intact and the table did not flatten into text blocks.
    assert any(it["type"] == "text" for it in items)


def test_extract_pdf_structure_normal_table_end_to_end():
    """A real detected table yields one logical table item with matching geometry."""
    doc = fitz.open()
    page = doc.new_page()
    cols, rows = 4, 3
    x0, y0 = 50, 50
    w, h = 120, 24
    for r in range(rows):
        for c in range(cols):
            page.insert_text(fitz.Point(x0 + c * w, y0 + r * h + 16), f"R{r}C{c}")
    for r in range(rows + 1):
        page.draw_line(fitz.Point(x0, y0 + r * h), fitz.Point(x0 + cols * w, y0 + r * h))
    for c in range(cols + 1):
        page.draw_line(fitz.Point(x0 + c * w, y0), fitz.Point(x0 + c * w, y0 + rows * h))
    src = io.BytesIO()
    doc.save(src, garbage=4, deflate=True)
    content = src.getvalue()
    doc.close()

    fragments, items = _extract_pdf_structure(content)
    table_items = [it for it in items if it["type"] == "table"]
    assert table_items
    ti = table_items[0]
    # geometry matrix is exactly 3x4 and every cell has a real rect
    assert len(ti["cell_rects"]) == 3
    assert all(len(row) == 4 for row in ti["cell_rects"])
    assert all(isinstance(r, fitz.Rect) for row in ti["cell_rects"] for r in row)


def test_extract_pdf_structure_no_cross_page_table_coverage():
    """Table coverage must be page-local.

    Regression: frag_indices were computed over ALL fragments regardless of
    page, and because every PDF page shares the same coordinate space (0..width,
    0..height), a large table on page 1 could numerically swallow fragments on
    page 0 whose (x, y) centre happened to fall inside the table bbox. Those
    fragments were then excluded from the logical items, so the title/leading
    text of a document was never sent to the LLM and stayed untranslated.
    """
    doc = fitz.open()
    # Page 0: standalone title text (same numeric coords a big table would use).
    p0 = doc.new_page()
    p0.insert_text(fitz.Point(100, 300), "Document title to be translated")
    # Page 1: a ruled grid occupying most of the page, incl. coords overlapping
    # the page-0 fragment centre (300, ~305).
    p1 = doc.new_page()
    cols, rows = 4, 3
    x0, y0 = 50, 250
    w, h = 120, 30
    for r in range(rows):
        for c in range(cols):
            p1.insert_text(fitz.Point(x0 + c * w, y0 + r * h + 20), f"C{r}{c}")
    for r in range(rows + 1):
        p1.draw_line(fitz.Point(x0, y0 + r * h), fitz.Point(x0 + cols * w, y0 + r * h))
    for c in range(cols + 1):
        p1.draw_line(fitz.Point(x0 + c * w, y0), fitz.Point(x0 + c * w, y0 + rows * h))
    src = io.BytesIO()
    doc.save(src, garbage=4, deflate=True)
    content = src.getvalue()
    doc.close()

    fragments, items = _extract_pdf_structure(content)
    p0_texts = [it for it in items if it["page"] == 0 and it["type"] == "text"]
    p1_tables = [it for it in items if it["page"] == 1 and it["type"] == "table"]
    # The page-0 title must survive as an editable text item even though its
    # coordinates sit inside the page-1 table bbox.
    assert any("Document title" in it["text"] for it in p0_texts), (
        "page-0 text was swallowed by a page-1 table bbox"
    )
    assert p1_tables, "the page-1 grid should still be a table item"


def test_extract_pdf_structure_merges_split_sentence_fragments():
    """Fragments that split one sentence must merge into a single logical item.

    Regression: some PDFs split a sentence across two text blocks (first block
    ends without terminating punctuation, second starts with a lowercase
    continuation, e.g. ``'...angle'`` + ``'steel,extra nesting software ...'``).
    The LLM then merges them into one translation and the strict 1:1 item-count
    check rejects the reply. Merging them up-front keeps the model reply aligned.
    """
    doc = fitz.open()
    page = doc.new_page()
    # First block: sentence WITHOUT a trailing terminator (it continues below).
    page.insert_text(fitz.Point(90, 100), "It is suitable for angle")
    # Second block: lowercase continuation of the same sentence, below, same col.
    page.insert_text(fitz.Point(90, 120), "steel,extra nesting software is needed")
    # Unrelated new sentence (capitalised) must NOT merge with the first block.
    page.insert_text(fitz.Point(90, 150), "For H steel a special machine is needed")
    src = io.BytesIO()
    doc.save(src, garbage=4, deflate=True)
    content = src.getvalue()
    doc.close()

    fragments, items = _extract_pdf_structure(content)
    text_items = [it for it in items if it["type"] == "text"]
    assert len(text_items) == 2, [it["text"] for it in text_items]
    merged = text_items[0]["text"]
    assert "angle" in merged and "steel,extra nesting software" in merged
    assert text_items[1]["text"].startswith("For H steel")
    # The merged fragment has a union bbox (spans both original blocks).
    assert text_items[0]["bbox"][3] >= 120
