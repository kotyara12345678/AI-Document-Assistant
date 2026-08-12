"""Dispatch a DocumentSpec to the DOCX or ODT renderer.

Content is defined once in the DocumentSpec; this module only picks the
binary format. ``spec_to_text`` produces the plain-text form used for the
Document.content column, so generated documents stay searchable and readable
through the existing retrieval / read_document pipeline.
"""

from app.schemas.document_spec import (
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)
from app.services.docx_renderer import render_docx
from app.services.odt_renderer import render_odt

OUTPUT_FORMATS = ("docx", "odt")


def render_document(spec: DocumentSpec, output_format: str) -> bytes:
    """Render a validated spec to bytes in the requested format."""
    if output_format == "docx":
        return render_docx(spec)
    if output_format == "odt":
        return render_odt(spec)
    raise ValueError(f"unsupported output format: {output_format!r}")


def spec_to_text(spec: DocumentSpec) -> str:
    """Flatten the spec into plain text (title, headings, paragraphs, lists)."""
    lines = [spec.title, ""]
    for block in spec.blocks:
        if isinstance(block, (HeadingBlock, ParagraphBlock)):
            lines.append(block.text)
        elif isinstance(block, ListBlock):
            lines.extend(f"- {item}" for item in block.items)
        elif isinstance(block, TableBlock):
            if block.headers:
                lines.append(" | ".join(block.headers))
            lines.extend(" | ".join(row) for row in block.rows)
    return "\n".join(lines).strip()
