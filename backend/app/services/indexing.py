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


def index_document(document: Document) -> IndexResult:
    """Chunk, embed and store a document's vectors in Qdrant."""
    chunks = chunk_text(document.content)

    if not chunks:
        logger.warning("Document %s has no chunks to index", document.id)
        return IndexResult(document_id=document.id, chunks_indexed=0)

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
