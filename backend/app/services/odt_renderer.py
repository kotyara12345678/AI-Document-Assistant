"""Render a validated DocumentSpec into a real .odt file.

Uses the same DocumentSpec as the DOCX renderer — this module only knows how
to map the neutral blocks (heading / paragraph / list / table) onto ODF
elements. ODT is XML with UTF-8 text, so Cyrillic is preserved as-is and
empty or long strings are handled by the library.

Lists are rendered through automatic list styles so ordered lists get
numbered items and unordered lists get bullets in LibreOffice/Word-compatible
viewers.
"""

from io import BytesIO

from app.schemas.document_spec import (
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)

_HEADING_LEVELS = 6
_BULLET_STYLE = "ADA_Bullet"
_NUMBERED_STYLE = "ADA_Numbered"


def render_odt(spec: DocumentSpec) -> bytes:
    """Return the bytes of an .odt built from ``spec``."""
    from odf import dc
    from odf.opendocument import OpenDocumentText

    document = OpenDocumentText()

    meta = document.meta
    meta.addElement(dc.Title(text=spec.title))
    if spec.metadata.author:
        meta.addElement(dc.Creator(text=spec.metadata.author))
    if spec.metadata.subject:
        meta.addElement(dc.Subject(text=spec.metadata.subject))
    if spec.metadata.keywords:
        meta.addElement(dc.Description(text=spec.metadata.keywords))

    _add_heading_styles(document)
    _add_list_styles(document)

    body = document.text
    _add_heading(body, spec.title, 1)

    for block in spec.blocks:
        _render_block(body, block)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _add_heading_styles(document) -> None:
    from odf.style import Style, TextProperties

    for level in range(1, _HEADING_LEVELS + 1):
        style = Style(
            name=f"Heading {level}",
            family="paragraph",
            parentstylename="Standard",
        )
        style.addElement(TextProperties(fontweight="bold"))
        document.automaticstyles.addElement(style)


def _add_list_styles(document) -> None:
    """Register automatic list styles for bullets and numbered items."""
    from odf.easyliststyle import ListLevelStyleBullet, ListLevelStyleNumber, ListStyle

    bullet = ListStyle(name=_BULLET_STYLE)
    bullet.addElement(ListLevelStyleBullet(level=1, bulletchar="•"))
    document.automaticstyles.addElement(bullet)

    numbered = ListStyle(name=_NUMBERED_STYLE)
    numbered.addElement(ListLevelStyleNumber(level=1, numformat="1"))
    document.automaticstyles.addElement(numbered)


def _add_heading(body, text: str, level: int) -> None:
    from odf.text import H

    heading = H(outlinelevel=level, stylename=f"Heading {level}", text=text)
    body.addElement(heading)


def _render_block(body, block) -> None:
    if isinstance(block, HeadingBlock):
        _add_heading(body, block.text, block.level)
    elif isinstance(block, ParagraphBlock):
        _add_paragraph(body, block.text)
    elif isinstance(block, ListBlock):
        _add_list(body, block)
    elif isinstance(block, TableBlock):
        _add_table(body, block)


def _add_paragraph(body, text: str) -> None:
    from odf.text import P

    body.addElement(P(text=text))


def _add_list(body, block: ListBlock) -> None:
    from odf.text import List as OdfList
    from odf.text import ListItem, P

    style = _NUMBERED_STYLE if block.ordered else _BULLET_STYLE
    odf_list = OdfList(stylename=style)
    for item in block.items:
        list_item = ListItem()
        list_item.addElement(P(text=item))
        odf_list.addElement(list_item)
    body.addElement(odf_list)


def _add_table(body, block: TableBlock) -> None:
    from odf.table import Table, TableCell, TableColumn, TableRow
    from odf.text import P

    column_count = len(block.headers)
    if column_count == 0:
        column_count = max((len(row) for row in block.rows), default=0)
    if column_count == 0:
        return

    table = Table(name="GeneratedTable")
    for _ in range(column_count):
        table.addElement(TableColumn())

    header_row = TableRow()
    for header in block.headers:
        cell = TableCell()
        cell.addElement(P(text=header))
        header_row.addElement(cell)
    table.addElement(header_row)

    for row in block.rows:
        table_row = TableRow()
        for index in range(column_count):
            value = row[index] if index < len(row) else ""
            cell = TableCell()
            cell.addElement(P(text=value))
            table_row.addElement(cell)
        table.addElement(table_row)

    body.addElement(table)
