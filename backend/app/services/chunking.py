"""Simple, predictable text chunking.

Splits text on whitespace into windows of `chunk_size` tokens (words) with
`overlap` tokens of overlap between consecutive chunks. Empty chunks are
skipped. Chunk order is preserved via the `index` field.
"""

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    chunk_size = chunk_size if chunk_size is not None else settings.CHUNK_SIZE
    overlap = overlap if overlap is not None else settings.CHUNK_OVERLAP

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size")

    tokens = text.split()
    if not tokens:
        return []

    chunks: list[Chunk] = []
    step = chunk_size - overlap

    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_text_ = " ".join(tokens[start:end]).strip()
        if chunk_text_:
            chunks.append(Chunk(index=len(chunks), text=chunk_text_))
        if end >= len(tokens):
            break
        start += step

    return chunks
