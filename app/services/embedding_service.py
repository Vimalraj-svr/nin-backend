"""
Multilingual sentence embedding service.

Model: paraphrase-multilingual-MiniLM-L12-v2
  - 384-dimensional embeddings
  - Supports: English, Tamil, Hindi, Telugu, Kannada, Malayalam (and 50+ others)
  - Runs fully offline after first download (~120 MB)
"""

import logging
from functools import lru_cache

import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    logger.info("Loading embedding model: %s", MODEL_NAME)
    return SentenceTransformer(MODEL_NAME)


def generate_embedding(text: str) -> np.ndarray:
    """
    Generate a 384-dim L2-normalized float32 embedding for `text`.
    Returns a zero vector on failure.
    """
    if not text or not text.strip():
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)

    try:
        model = _get_model()
        vec = model.encode(text, normalize_embeddings=True)
        return vec.astype(np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Embedding generation failed: %s", exc)
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
