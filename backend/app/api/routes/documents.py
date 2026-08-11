from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.database.session import get_db
from app.models.document import Document
from app.schemas.document import DocumentOut
from app.services import documents as document_service
from app.services import indexing as indexing_service

router = APIRouter()

_FILE_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "txt": "text/plain",
    "md": "text/markdown",
}


def _file_media_type(file_type: str) -> str:
    return _FILE_MEDIA_TYPES.get(file_type, "application/octet-stream")


@router.post("/upload", response_model=list[DocumentOut], status_code=status.HTTP_201_CREATED)
def upload_documents(
    file: list[UploadFile] = File(...),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    documents = document_service.store_uploads(files=file, user_id=user_id, db=db)
    return [DocumentOut.model_validate(document) for document in documents]


@router.get("", response_model=list[DocumentOut])
def list_documents(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    documents = document_service.list_documents(user_id=user_id, db=db)
    return [DocumentOut.model_validate(doc) for doc in documents]


@router.post("/{document_id}/index")
def index_document(
    document_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
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

    result = indexing_service.index_document(document)
    return {
        "document_id": document_id,
        "chunks_indexed": result.chunks_indexed,
        "status": "ok",
    }


@router.delete("", status_code=status.HTTP_200_OK)
def delete_all_documents(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Delete all documents (and their vectors/files) of the current user."""
    count = document_service.delete_all_documents(user_id=user_id, db=db)
    return {"deleted": count, "status": "ok"}


@router.delete("/{document_id}", status_code=status.HTTP_200_OK)
def delete_document(
    document_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a single document (and its vectors/file)."""
    document = document_service.delete_document(
        document_id=document_id, user_id=user_id, db=db
    )
    return {
        "deleted": 1,
        "status": "ok",
        "document_id": document.id,
        "original_filename": document.original_filename,
    }


@router.get("/{document_id}/content")
def get_document_content(
    document_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Return the extracted text of a document so the UI can render it inline."""
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

    return {
        "id": document.id,
        "original_filename": document.original_filename,
        "file_type": document.file_type,
        "content_length": document.content_length,
        "content": document.content,
    }


@router.get("/{document_id}/file")
def get_document_file(
    document_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream the original uploaded file to its owner (used by the PDF viewer).

    Only the owning user can read the bytes; the on-disk path comes from the
    database, never from the client. The extracted-text preview stays in
    ``/{document_id}/content``.
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

    filepath = Path(document.filepath)
    if not filepath.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File is missing on disk",
        )

    return FileResponse(
        str(filepath),
        media_type=_file_media_type(document.file_type),
        filename=document.original_filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
