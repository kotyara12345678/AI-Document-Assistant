"""Document indexing pipeline: Document.content -> chunks -> embeddings -> Qdrant.

Failures here must never corrupt the PostgreSQL Document record: callers catch
exceptions from this module and keep the DB state intact.
"""

import logging
from dataclasses import dataclass

from app.models.document import Document
from app.services.chunking import Chunk, chunk_text
from app.vector import client as vector_client
from app.vector.embeddings import embed_texts

logger = logging.getLogger("app.indexing")

@dataclass(frozen=True)
class IndexResult:
    document_id: int
    chunks_indexed: int


def _persist_chunks(document_id: int, chunks: list[Chunk]) -> None:
    """Store the document's chunks in PostgreSQL for keyword (FTS) search.

    Replaces any existing chunk rows for the document so re-indexing stays
    idempotent.
    """
    if not chunks:
        return
    from app.database.session import SessionLocal
    from app.models.document_chunk import DocumentChunk

    db = SessionLocal()
    try:
        db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()
        db.add_all(
            DocumentChunk(document_id=document_id, chunk_index=chunk.index, text=chunk.text)
            for chunk in chunks
        )
        db.commit()
    finally:
        db.close()


def index_document(document: Document) -> IndexResult:
    """Chunk, embed and store a document's vectors in Qdrant.

    The same chunks are also persisted to PostgreSQL so keyword (FTS) search
    can run alongside the semantic one.
    """
    chunks = chunk_text(document.content)

    if not chunks:
        logger.warning("Document %s has no chunks to index", document.id)
        return IndexResult(document_id=document.id, chunks_indexed=0)

    try:
        _persist_chunks(document.id, chunks)
    except Exception:
        logger.exception("Failed to persist chunks for document %s", document.id)

    vectors = embed_texts([c.text for c in chunks])

    payloads = [
        {
            "document_id": document.id,
            "user_id": document.user_id,
            "chunk_index": chunk.index,
            "text": chunk.text,
            "original_filename": document.original_filename,
        }
        for chunk in chunks
    ]

    count = vector_client.upsert_chunks(vectors, payloads)
    logger.info("Indexed %s chunks for document %s", count, document.id)
    return IndexResult(document_id=document.id, chunks_indexed=count)


def delete_document_index(document_id: int) -> int:
    """Remove all vectors of a document from Qdrant."""
    deleted = vector_client.delete_document_vectors(document_id)
    logger.info("Deleted vectors for document %s (%s)", document_id, deleted)
    return deleted


def reindex_missing_documents() -> int:
    """Index documents that have no chunks in Qdrant yet.

    Used on startup so that documents uploaded before a collection wipe
    (e.g. by tests) get their chunks + MiniLM embeddings written back.
    Returns the number of documents that were (re)indexed.
    """
    from sqlalchemy.orm import Session

    from app.database.session import SessionLocal

    db: Session = SessionLocal()
    reindexed = 0
    try:
        documents = db.query(Document).order_by(Document.id).all()
        for document in documents:
            if vector_client.document_vector_count(document.id) > 0:
                continue
            try:
                result = index_document(document)
                logger.info(
                    "Re-indexed document %s (%s chunks) during startup",
                    document.id,
                    result.chunks_indexed,
                )
                reindexed += 1
            except Exception:
                logger.exception("Startup re-index failed for document %s", document.id)
    finally:
        db.close()
    return reindexed
