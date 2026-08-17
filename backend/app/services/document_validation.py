"""Post-render validation of generated document files.

After a renderer produces the binary file we must prove, *before* registering
it in the database, that the file is real and matches the spec:

* the file is non-empty,
* it is a valid OOXML/ODF container (a ZIP archive) for docx/odt, or a valid
  PDF (starts with ``%PDF`` and opens with PyMuPDF) for pdf, or valid UTF-8
  text for md/txt,
* ``word/document.xml`` (docx) / ``content.xml`` (odt) exists,
* the expected text is actually present inside the document,
* the required repetition counts are honoured (e.g. "витек лох" x50).

All failures raise ``RendererError`` with a precise reason so the agent can log
it and never tell the user the document was created when it was not.
"""

import io
import logging
import re
import zipfile
from xml.sax.saxutils import escape

from app.services.errors import RendererError
from app.schemas.document_spec import (
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)

logger = logging.getLogger("app.document_validation")

_WS_RE = re.compile(r"\s+")


def _read_inner_xml(content_bytes: bytes, output_format: str) -> str:
    """Return the main content XML of a docx/odt package, or raise RendererError."""
    if not content_bytes or len(content_bytes) == 0:
        raise RendererError("generated file is empty")

    if not zipfile.is_zipfile(io.BytesIO(content_bytes)):
        raise RendererError(
            f"generated {output_format} is not a valid ZIP/OOXML package"
        )

    entry = "word/document.xml" if output_format == "docx" else "content.xml"
    with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
        names = zf.namelist()
        if entry not in names:
            raise RendererError(f"{output_format} package is missing {entry}")
        return zf.read(entry).decode("utf-8", "replace")


def _expected_text_frequencies(spec: DocumentSpec) -> dict[str, int]:
    """Map each distinct text fragment to how many times it must appear."""
    freq: dict[str, int] = {}
    for block in spec.blocks:
        if isinstance(block, (HeadingBlock, ParagraphBlock)):
            freq[block.text] = freq.get(block.text, 0) + 1
        elif isinstance(block, ListBlock):
            for item in block.items:
                freq[item] = freq.get(item, 0) + 1
        elif isinstance(block, TableBlock):
            for header in block.headers:
                freq[header] = freq.get(header, 0) + 1
            for row in block.rows:
                for cell in row:
                    freq[cell] = freq.get(cell, 0) + 1
    return freq


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace runs into single spaces for PDF matching.

    PDF text is extracted line-by-line (wrapped lines, list prefixes, table
    cell boundaries), so whitespace-sensitive substring counts would be brittle
    unless we normalise both the extracted text and the expected fragments.
    """
    return _WS_RE.sub(" ", text).strip()


def _pdf_text(content_bytes: bytes, output_format: str) -> str:
    """Return the plain text of a generated PDF, or raise RendererError."""
    if not content_bytes or not content_bytes.startswith(b"%PDF"):
        raise RendererError("generated pdf is not a valid PDF file")

    try:
        import fitz

        doc = fitz.open(stream=content_bytes, filetype="pdf")
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    except Exception as exc:  # pragma: no cover - renderer bug surface
        raise RendererError(f"generated {output_format} cannot be read: {exc}") from exc


def validate_document_bytes(
    content_bytes: bytes,
    output_format: str,
    spec: DocumentSpec | None = None,
) -> None:
    """Validate the rendered file. Raises ``RendererError`` on any problem."""
    if output_format == "pdf":
        content = _pdf_text(content_bytes, output_format)
    elif output_format in ("md", "txt"):
        if not content_bytes or not content_bytes.strip():
            raise RendererError(f"generated {output_format} file is empty")
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RendererError(f"generated {output_format} is not valid UTF-8") from exc
    else:
        content = _read_inner_xml(content_bytes, output_format)

    if spec is not None:
        freq = _expected_text_frequencies(spec)
        for text, count in freq.items():
            if not text or not text.strip():
                continue
            if output_format == "pdf":
                actual = _normalize_ws(content).count(_normalize_ws(text))
            elif output_format in ("md", "txt"):
                actual = content.count(text)
            else:
                # python-docx escapes &, <, > exactly like xml.sax.saxutils.escape.
                actual = content.count(escape(text))
            if actual < count:
                raise RendererError(
                    f"document text {text!r} expected at least {count} times, "
                    f"found {actual}"
                )
    logger.debug("Document validation passed (%s)", output_format)
