"""Text extraction from uploaded files.

Supported: PDF (pypdf), DOCX (python-docx), TXT (UTF-8 with fallback).
"""

from io import BytesIO

from docx import Document as DocxDocument
from fastapi import HTTPException, status
from pypdf import PdfReader


def extract_text(content: bytes, file_type: str) -> str:
    if file_type == "pdf":
        return _extract_pdf(content)
    if file_type == "docx":
        return _extract_docx(content)
    if file_type == "txt":
        return _extract_txt(content)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported file type '{file_type}'",
    )


def _extract_pdf(content: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to extract text from PDF (corrupted or password-protected?)",
        )


def _extract_docx(content: bytes) -> str:
    try:
        doc = DocxDocument(BytesIO(content))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append("\t".join(cell.text for cell in row.cells))
        return "\n".join(parts).strip()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to extract text from DOCX (corrupted file?)",
        )


def _extract_txt(content: bytes) -> str:
    for encoding in ("utf-8", "windows-1251", "cp1252"):
        try:
            return content.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace").strip()


def sniff_file_type(content: bytes) -> str | None:
    """Best-effort MIME detection from magic bytes. Returns None if unknown."""
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        return "docx"
    return None
