"""Render a validated DocumentSpec into a real PDF file with PyMuPDF.

PDF has no flowing text, so this module lays the shared DocumentSpec blocks
out onto A4 pages: the title, bold headings sized by level, paragraphs,
bullet/numbered lists and bordered tables. A Cyrillic-capable Unicode TTF
(DejaVu Sans in the container, Arial on Windows dev) is embedded so non-ASCII
text is preserved exactly like the DOCX/ODT renderers. Content logic lives in
the shared DocumentSpec; this module only maps the neutral blocks onto PDF
primitives.
"""

import os
import sys

import fitz

from app.schemas.document_spec import (
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)

_PAGE_W = 595.28  # A4
_PAGE_H = 841.89
_MARGIN = 48.0
_CONTENT_W = _PAGE_W - 2 * _MARGIN
_PAGE_BOTTOM = _PAGE_H - _MARGIN

_TITLE_SIZE = 22.0
_BODY_SIZE = 11.0
_HEADING_SIZES = {1: 17.0, 2: 15.0, 3: 13.0, 4: 12.0, 5: 11.0, 6: 10.5}
_LINE_FACTOR = 1.30
_TABLE_PAD = 4.0
_LIST_INDENT = 18.0

# Font resource names registered per page (must contain no spaces).
_FONT_RES = {"regular": "reg", "bold": "bold"}
_BASE14 = {"regular": "helv", "bold": "hebo"}


def _font_file(bold: bool) -> str | None:
    """Return an embeddable Cyrillic-capable TTF path, or None."""
    if sys.platform.startswith("win"):
        candidates = [
            r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        ]
    else:
        leaf = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        candidates = [
            os.path.join("/usr/share/fonts/truetype/dejavu", leaf),
            os.path.join("/usr/share/fonts/dejavu", leaf),
            os.path.join(os.path.dirname(__file__), "fonts", leaf),
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _load_font(bold: bool) -> tuple["fitz.Font", str | None]:
    """Return ``(font_for_metrics, fontfile_path_or_None)``."""
    path = _font_file(bold)
    if path:
        return fitz.Font(fontfile=path), path
    return fitz.Font("hebo" if bold else "helv"), None


def _wrap(text: str, font: "fitz.Font", fontsize: float, max_width: float) -> list[str]:
    """Word-wrap ``text`` into lines that fit ``max_width`` points."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}" if current else word
        if current and font.text_length(trial, fontsize=fontsize) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


class _DocumentLayout:
    """Cursor-based A4 layout: owns the current page and the Y position."""

    def __init__(self, doc: "fitz.Document", fonts: dict) -> None:
        self.doc = doc
        self.fonts = fonts  # {key: (Font, fontfile|None)}
        self.y = _MARGIN
        self.page = None

    def _register_fonts(self) -> None:
        for key, (_font, path) in self.fonts.items():
            if path:
                self.page.insert_font(fontname=_FONT_RES[key], fontfile=path)

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=_PAGE_W, height=_PAGE_H)
        self._register_fonts()
        self.y = _MARGIN

    def ensure_page(self) -> None:
        if self.page is None:
            self._new_page()

    def _line_height(self, fontsize: float) -> float:
        return _LINE_FACTOR * fontsize

    def place_line(self, x: float, text: str, font_key: str, fontsize: float) -> None:
        """Insert one already-wrapped line at the cursor baseline."""
        font, path = self.fonts[font_key]
        line_h = self._line_height(fontsize)
        if self.y + line_h > _PAGE_BOTTOM:
            self._new_page()
        baseline = self.y + font.ascender * fontsize
        fontname = _FONT_RES[font_key] if path else _BASE14[font_key]
        self.page.insert_text(
            (x, baseline), text, fontname=fontname, fontsize=fontsize
        )
        self.y += line_h

    def place_lines(
        self, x: float, lines: list[str], font_key: str, fontsize: float
    ) -> None:
        for line in lines:
            self.place_line(x, line, font_key, fontsize)


def render_pdf(spec: DocumentSpec) -> bytes:
    """Return the bytes of a PDF built from ``spec``."""
    doc = fitz.open()
    layout = _DocumentLayout(
        doc,
        fonts={"regular": _load_font(False), "bold": _load_font(True)},
    )
    layout.ensure_page()

    title_lines = _wrap(spec.title, layout.fonts["bold"][0], _TITLE_SIZE, _CONTENT_W)
    if title_lines:
        layout.place_lines(_MARGIN, title_lines, "bold", _TITLE_SIZE)
        layout.y += 8

    for block in spec.blocks:
        _render_block(layout, block)

    output = doc.tobytes()
    doc.close()
    return output


def _render_block(layout: _DocumentLayout, block) -> None:
    if isinstance(block, HeadingBlock):
        fontsize = _HEADING_SIZES.get(block.level, _BODY_SIZE)
        lines = _wrap(block.text, layout.fonts["bold"][0], fontsize, _CONTENT_W)
        if lines:
            layout.y += 4
            layout.place_lines(_MARGIN, lines, "bold", fontsize)
            layout.y += 3
    elif isinstance(block, ParagraphBlock):
        lines = _wrap(block.text, layout.fonts["regular"][0], _BODY_SIZE, _CONTENT_W)
        layout.y += 2
        layout.place_lines(_MARGIN, lines, "regular", _BODY_SIZE)
        layout.y += 6
    elif isinstance(block, ListBlock):
        _render_list(layout, block)
    elif isinstance(block, TableBlock):
        _render_table(layout, block)


def _render_list(layout: _DocumentLayout, block: ListBlock) -> None:
    if not block.items:
        return
    font = layout.fonts["regular"][0]
    body_width = _CONTENT_W - _LIST_INDENT
    for index, item in enumerate(block.items, start=1):
        prefix = f"{index}. " if block.ordered else "\u2022 "
        wrapped = _wrap(item, font, _BODY_SIZE, body_width)
        if not wrapped:
            continue
        layout.place_line(_MARGIN, prefix + wrapped[0], "regular", _BODY_SIZE)
        for rest in wrapped[1:]:
            layout.place_line(_MARGIN + _LIST_INDENT, rest, "regular", _BODY_SIZE)
        layout.y += 3


def _measure_row(cell_wraps: list[list[str]]) -> float:
    line_h = _LINE_FACTOR * _BODY_SIZE
    return (
        max((len(lines) * line_h for lines in cell_wraps), default=line_h)
        + 2 * _TABLE_PAD
    )


def _draw_table_row(
    layout: _DocumentLayout,
    cell_wraps: list[list[str]],
    is_header: bool,
    column_width: float,
) -> float:
    """Draw one table row's borders and text; advance the cursor. Returns height."""
    font_key = "bold" if is_header else "regular"
    font = layout.fonts[font_key][0]
    line_h = _LINE_FACTOR * _BODY_SIZE
    row_height = _measure_row(cell_wraps)
    y0 = layout.y
    y1 = y0 + row_height
    for column, lines in enumerate(cell_wraps):
        x0 = _MARGIN + column * column_width
        rect = fitz.Rect(x0, y0, x0 + column_width, y1)
        if is_header:
            layout.page.draw_rect(
                rect, color=(0.72, 0.75, 0.80), fill=(0.93, 0.94, 0.96), width=0.4
            )
        else:
            layout.page.draw_rect(rect, color=(0.72, 0.75, 0.80), width=0.4)
        y = y0 + _TABLE_PAD
        for line in lines:
            baseline = y + font.ascender * _BODY_SIZE
            layout.page.insert_text(
                (x0 + _TABLE_PAD, baseline),
                line,
                fontname=_FONT_RES[font_key] if layout.fonts[font_key][1] else _BASE14[font_key],
                fontsize=_BODY_SIZE,
            )
            y += line_h
    layout.y = y1
    return row_height


def _render_table(layout: _DocumentLayout, block: TableBlock) -> None:
    column_count = len(block.headers)
    if column_count == 0:
        column_count = max((len(row) for row in block.rows), default=0)
    if column_count == 0:
        return

    layout.y += 6

    rows: list[list[str]] = []
    if block.headers:
        rows.append(block.headers)
    rows.extend(block.rows)

    column_width = _CONTENT_W / column_count
    header_font = layout.fonts["bold"][0]
    body_font = layout.fonts["regular"][0]
    wraps: list[list[list[str]]] = []
    for index, row in enumerate(rows):
        font = header_font if (block.headers and index == 0) else body_font
        cell_wraps = []
        for cell_index in range(column_count):
            value = row[cell_index] if cell_index < len(row) else ""
            cell_wraps.append(
                _wrap(value, font, _BODY_SIZE, column_width - 2 * _TABLE_PAD)
            )
        wraps.append(cell_wraps)

    header_wraps = wraps[0] if block.headers else None
    for index, cell_wraps in enumerate(wraps):
        layout.y += 6 if (index == 0 and block.headers) else 2
        row_height = _measure_row(cell_wraps)
        if layout.y + row_height > _PAGE_BOTTOM:
            layout._new_page()
            if block.headers:
                _draw_table_row(layout, header_wraps or cell_wraps, True, column_width)
                layout.y += 2
                if layout.y + row_height > _PAGE_BOTTOM:
                    layout._new_page()
        _draw_table_row(
            layout,
            cell_wraps,
            bool(block.headers and index == 0),
            column_width,
        )

    layout.y += 8