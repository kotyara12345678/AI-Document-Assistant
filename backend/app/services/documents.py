import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document


def _validate_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in settings.ALLOWED_EXTENSIONS:
        allowed = ", ".join(settings.ALLOWED_EXTENSIONS)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '.{ext}'. Allowed: {allowed}",
        )


def store_upload(file: UploadFile, user_id: int, db: Session) -> Document:
    """Persist an uploaded file to disk and create a Document record.

    Extraction, chunking and vector indexing are intentionally NOT
    implemented yet — they will be added in the indexing iteration.
    """
    _validate_extension(file.filename or "")

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    filepath = upload_dir / stored_name

    content = file.file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB limit",
        )

    filepath.write_bytes(content)

    document = Document(user_id=user_id, filename=file.filename, filepath=str(filepath))
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def list_documents(user_id: int, db: Session) -> list[Document]:
    return db.query(Document).filter(Document.user_id == user_id).order_by(Document.created_at.desc()).all()
