from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user_id
from app.database.session import get_db
from app.models.document import Document
from app.schemas.document import DocumentOut
from app.services import documents as document_service
from app.services import indexing as indexing_service

router = APIRouter()


@router.post("/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> DocumentOut:
    document = document_service.store_upload(file=file, user_id=user_id, db=db)
    return DocumentOut.model_validate(document)


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
