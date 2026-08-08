import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.services import extraction
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


def store_upload(file: UploadFile, user_id: int, db: Session) -> Document:
    """Persist an uploaded file, extract its text and store a Document record.

    The binary file is saved to the uploads volume; PostgreSQL stores only
    metadata and the extracted plain text.
    """
    original_filename = file.filename or "untitled"
    file_type = _validate_extension(original_filename)

    content = file.file.read()

    if len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB limit",
        )

    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    _validate_magic_bytes(content, file_type)

    extracted = extraction.extract_text(content, file_type)
    if not extracted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No text could be extracted from the file",
        )

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
        content=extracted,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Indexing must not break the upload: the Document record is already
    # committed, so failures here are logged and reported separately.
    try:
        index_document(document)
    except Exception:
        logger.exception("Indexing failed for document %s", document.id)

    return document


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
    """Remove a document: its vectors from Qdrant, its file from disk and its DB row."""
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

    _remove_vectors_and_file(document)
    db.delete(document)
    db.commit()
    return document


def delete_all_documents(user_id: int, db: Session) -> int:
    """Remove every document of a user: vectors, files and DB rows. Returns count."""
    documents = db.query(Document).filter(Document.user_id == user_id).all()
    for document in documents:
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
