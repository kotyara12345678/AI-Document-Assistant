import logging
import re
import uuid
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.services import extraction
from app.services.entity_locks import lock_for
from app.services.indexing import delete_document_index, index_document

logger = logging.getLogger("app.documents")


def _validate_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in settings.ALLOWED_EXTENSIONS:
        allowed = ", ".join(settings.ALLOWED_EXTENSIONS)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '.{ext}'. Allowed: {allowed}",
        )
    return ext


def _sanitize_original_filename(filename: str | None) -> str:
    """Keep only the file's basename and drop control characters.

    The stored name is used for display, search and metadata only (the payload
    is written to disk under a server-generated UUID), so this is defense in
    depth: never let a client-chosen name carry path separators or control
    characters anywhere it could be echoed back or sent to the LLM.
    """
    name = (filename or "untitled").replace("\\", "/").split("/")[-1].strip()
    name = "".join(ch for ch in name if "\u0020" <= ch <= "\u007e" or ord(ch) > 0x7F)
    return name or "untitled"


# Control characters (incl. NUL 0x00) that PostgreSQL TEXT columns reject and
# that PyMuPDF's text extraction can emit for embedded Unicode (Identity-H)
# fonts lacking a ToUnicode map. Whitespace \t \n \r is preserved.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control_chars(text: str | None) -> str:
    """Drop NUL/control characters so extracted text is safe to persist/index.

    The binary PDF payload is stored on disk (write_bytes) and is unaffected;
    only the derived ``content`` text column is cleaned. This is purely a
    storage-safety measure -- it does not alter the rendered document.
    """
    if not text:
        return ""
    return _CONTROL_CHAR_RE.sub("", text)


def _cap_extracted(text: str) -> str:
    """Bound extracted text so a pathological file cannot grow unbounded."""
    return text[: settings.MAX_EXTRACTED_CHARS]


def _reject_zip_bomb(content: bytes, file_type: str) -> None:
    """Refuse container formats whose UNCOMPRESSED size explodes.

    DOCX and ODT are ZIP packs: raw bytes are capped by MAX_UPLOAD_SIZE_MB, but
    a tiny pack can expand far beyond that during extraction (classic zip bomb).
    Inspect the stored entry sizes up front and reject anything that would
    decompress past ZIP_UNCOMPRESSED_MAX_MB.
    """
    if file_type not in ("docx", "odt"):
        return
    try:
        with ZipFile(BytesIO(content)) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except BadZipFile:
        return  # extraction will surface its own 422 later
    max_bytes = settings.ZIP_UNCOMPRESSED_MAX_MB * 1024 * 1024
    if total > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File expands beyond the allowed size limit",
        )


def _read_upload_bounded(file: UploadFile) -> bytes:
    """Read the upload in chunks, refusing early once it exceeds the limit.

    Returns the full content when it fits, otherwise raises HTTP 413 without
    having buffered an unbounded blob in memory.
    """
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    blocks: list[bytes] = []
    size = 0
    while True:
        block = file.file.read(1024 * 1024)
        if not block:
            break
        size += len(block)
        if size > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB limit",
            )
        blocks.append(block)
    return b"".join(blocks)


def _process_upload(file: UploadFile) -> tuple[str, str, bytes, str]:
    """Validate one uploaded file and extract its text, without persisting.

    Returns ``(original_filename, file_type, content, extracted_text)`` and
    raises the same HTTP 4xx as the old inline checks. Splitting validation
    from persistence lets a multi-file request abort cleanly BEFORE any file of
    the batch is written, so a bad file can never leave earlier files of the
    same batch half-committed.
    """
    original_filename = _sanitize_original_filename(file.filename)
    file_type = _validate_extension(original_filename)

    content = _read_upload_bounded(file)

    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    _reject_zip_bomb(content, file_type)
    _validate_magic_bytes(content, file_type)

    extracted = extraction.extract_text(content, file_type)
    if not extracted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No text could be extracted from the file",
        )
    extracted = _cap_extracted(extracted)

    return original_filename, file_type, content, extracted


def store_upload(file: UploadFile, user_id: int, db: Session) -> Document:
    """Persist an uploaded file, extract its text and store a Document record.

    The binary file is saved to the uploads volume; PostgreSQL stores only
    metadata and the extracted plain text.
    """
    original_filename, file_type, content, extracted = _process_upload(file)
    return persist_file(
        content,
        file_type,
        user_id,
        db,
        original_filename=original_filename,
        content_text=extracted,
    )


def persist_file(
    content: bytes,
    file_type: str,
    user_id: int,
    db: Session,
    *,
    original_filename: str,
    content_text: str | None = None,
    source_file_id: int | None = None,
    chat_id: int | None = None,
) -> Document:
    """Write a file to storage and create its Document row (shared by uploads,
    generated documents and edited documents).

    The original is never overwritten: the payload is written under a fresh
    server-generated UUID. ``source_file_id`` records the immutable original a
    generated/edited file was derived from; ``chat_id`` links the file to the
    chat that produced it. Both are informational and nullable so the file
    itself survives chat/original deletion.
    """
    original_filename = _sanitize_original_filename(original_filename)
    if file_type not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '.{file_type}'.",
        )

    if content_text is None:
        try:
            content_text = extraction.extract_text(content, file_type)
        except Exception:
            content_text = ""
    # Extracted text may carry NUL/control chars (e.g. from embedded Unicode
    # fonts); strip them so the TEXT column and the search index stay valid.
    # The extraction cap also applies here, so generated/edited documents can
    # never push more than MAX_EXTRACTED_CHARS into the content column either.
    content_text = _cap_extracted(_strip_control_chars(content_text))

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}.{file_type}"
    filepath = upload_dir / stored_name
    filepath.write_bytes(content)

    document = Document(
        user_id=user_id,
        filename=stored_name,
        original_filename=original_filename,
        file_type=file_type,
        file_size=len(content),
        filepath=str(filepath),
        content=content_text,
        chat_id=chat_id,
        source_file_id=source_file_id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Indexing must not break creation: the Document row is already committed,
    # so failures here are logged and reported separately.
    try:
        index_document(document)
    except Exception:
        logger.exception("Indexing failed for document %s", document.id)

    return document


def store_uploads(files: list[UploadFile], user_id: int, db: Session) -> list[Document]:
    """Persist several uploaded files in a single request.

    Enforces the per-request file count limit and fully validates and extracts
    EVERY file before any of them is persisted, so a bad file (empty, wrong
    magic bytes, unreadable) aborts the whole batch without leaving earlier
    files of the same request half-committed.
    """
    if len(files) > settings.MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Too many files: {len(files)}. "
                f"Maximum is {settings.MAX_UPLOAD_FILES} files per upload request."
            ),
        )

    processed = [_process_upload(upload) for upload in files]

    return [
        persist_file(
            content,
            file_type,
            user_id,
            db,
            original_filename=original_filename,
            content_text=extracted,
        )
        for original_filename, file_type, content, extracted in processed
    ]


def _validate_magic_bytes(content: bytes, declared_type: str) -> None:
    """Best-effort check that the file content matches its declared type."""
    detected = extraction.sniff_file_type(content)
    if detected is None:
        return
    if detected != declared_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File content does not match its extension (detected '{detected}', expected '{declared_type}')",
        )


def list_documents(user_id: int, db: Session) -> list[Document]:
    return db.query(Document).filter(Document.user_id == user_id).order_by(Document.created_at.desc()).all()


def delete_document(document_id: int, user_id: int, db: Session) -> Document:
    """Remove a document: its vectors from Qdrant, its file from disk and its DB row.

    Runs under the per-document index/delete lock so a re-index of the same
    document in flight cannot resurrect ghost vectors after the cleanup (see
    indexing.index_document for the lock pairing).
    """
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.user_id == user_id)
        .first()
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    with lock_for(document.id):
        _remove_vectors_and_file(document)
        db.delete(document)
        db.commit()
    return document


def delete_all_documents(user_id: int, db: Session) -> int:
    """Remove every document of a user: vectors, files and DB rows. Returns count."""
    documents = db.query(Document).filter(Document.user_id == user_id).all()
    for document in documents:
        with lock_for(document.id):
            _remove_vectors_and_file(document)
    count = len(documents)
    db.query(Document).filter(Document.user_id == user_id).delete(synchronize_session=False)
    db.commit()
    return count


def _remove_vectors_and_file(document: Document) -> None:
    """Best-effort cleanup of Qdrant vectors and the on-disk file."""
    try:
        delete_document_index(document.id)
    except Exception:
        logger.exception("Failed to delete vectors for document %s", document.id)

    filepath = Path(document.filepath)
    try:
        filepath.unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed to remove file %s for document %s", document.filepath, document.id)
