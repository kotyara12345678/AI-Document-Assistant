"""Embedding service.

The model is loaded once and cached for the lifetime of the process.
Model downloads to HF_HOME on first use (set to /data/hf in Docker).
"""

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.core.config import settings


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    return SentenceTransformer(settings.EMBEDDING_MODEL)


def get_embedding_dimension() -> int:
    return get_model().get_sentence_embedding_dimension()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts in one batch. Returns list of vectors."""
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=32,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]
