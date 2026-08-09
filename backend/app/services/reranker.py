"""Cross-encoder re-ranking of hybrid retrieval candidates.

Uses the local ``BAAI/bge-reranker-v2-m3`` model via sentence-transformers.
The model is loaded lazily once and cached for the process lifetime (downloads
to HF_HOME on first use, like the embedding model).

Any failure to load or run the model raises so the caller can fall back to the
hybrid order instead of breaking the RAG request.
"""

import logging
import threading

from sentence_transformers import CrossEncoder

from app.core.config import settings

logger = logging.getLogger("app.reranker")

_model: CrossEncoder | None = None
_model_lock = threading.Lock()


def get_model() -> CrossEncoder:
    """Load the reranker once; concurrent callers wait on the lock."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        logger.info(
            "Loading reranker model %s on %s", settings.RERANKER_MODEL, settings.RERANKER_DEVICE
        )
        _model = CrossEncoder(
            settings.RERANKER_MODEL,
            device=settings.RERANKER_DEVICE,
            max_length=512,
        )
        return _model


def compute_scores(query: str, texts: list[str]) -> list[float]:
    """Cross-encode ``(query, chunk_text)`` pairs and return scores in [0, 1].

    Sigmoid squash keeps the scores on the same [0, 1] scale the frontend uses;
    ranking within a single query is identical to using raw logits.
    """
    if not texts:
        return []
    import torch

    model = get_model()
    clipped = [text[: settings.RERANKER_MAX_CHARS] for text in texts]
    raw = model.predict(
        [[query, chunk] for chunk in clipped],
        activation_fn=torch.nn.Sigmoid(),
        batch_size=32,
        show_progress_bar=False,
    )
    if raw is None:
        raise RuntimeError("Reranker predict returned no scores")
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    return [float(score) for score in raw]