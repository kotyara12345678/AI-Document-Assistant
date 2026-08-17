"""Embedding service.

The model is loaded once and cached for the lifetime of the process.
Model downloads to HF_HOME on first use (set to /data/hf in Docker).

Loading is guarded by a double-checked lock so two cold requests can never
instantiate the sentence transformer twice (the first `retrieve_context` call
spawns worker threads that race for the shared model). Chunk size is validated
against the model's token budget at load time and reported loudly instead of
silently truncating every chunk tail.
"""

import logging
import threading

from sentence_transformers import SentenceTransformer

from app.core.config import settings

logger = logging.getLogger("app.embeddings")

_model_lock = threading.Lock()
_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = _load_model()
    return _model


def _load_model() -> SentenceTransformer:
    model = SentenceTransformer(settings.EMBEDDING_MODEL)
    max_len = getattr(model, "max_seq_length", None) or 256
    if settings.CHUNK_SIZE > 0 and max_len > 0:
        # Rough word -> token ratio for RU/EN text is ~1.2-1.4 tokens/word;
        # if a CHUNK_SIZE-word chunk cannot fit the model context, flag it now
        # rather than shipping indexes whose chunk tails are invisible to
        # semantic search.
        approx_tokens = settings.CHUNK_SIZE * 1.4
        if approx_tokens > max_len:
            logger.warning(
                "CHUNK_SIZE=%s words (~%s tokens) exceeds the embedding model's "
                "context of %s tokens; chunks will be silently truncated on "
                "encode. Reduce CHUNK_SIZE or switch to a longer-context model.",
                settings.CHUNK_SIZE,
                int(approx_tokens),
                max_len,
            )
    return model


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