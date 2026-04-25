"""
Emotion detection service using a lightweight offline Transformers model.

Model: j-hartmann/emotion-english-distilroberta-base
Labels: joy, sadness, anger, fear, surprise, disgust, neutral

Works best with English text. For other languages the model still produces
reasonable inference via shared multilingual representations.
"""

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

EMOTION_MODEL_NAME = "j-hartmann/emotion-english-distilroberta-base"

# Friendly colour hints for frontend (returned as-is in the label string)
VALID_EMOTIONS = {"joy", "sadness", "anger", "fear", "surprise", "disgust", "neutral"}


@lru_cache(maxsize=1)
def _get_pipeline():
    """Lazy-load the Transformers pipeline (cached for the process lifetime)."""
    from transformers import pipeline  # noqa: PLC0415

    logger.info("Loading emotion model: %s", EMOTION_MODEL_NAME)
    return pipeline(
        "text-classification",
        model=EMOTION_MODEL_NAME,
        top_k=1,
        device=-1,  # CPU only
    )


def detect_emotion(text: str) -> str:
    """
    Returns the dominant emotion label for the given text.
    Truncates at 512 characters to stay within model limits.
    Falls back to 'neutral' on any error.
    """
    if not text or not text.strip():
        return "neutral"

    try:
        pipe = _get_pipeline()
        truncated = text[:512]
        results = pipe(truncated)
        if results and results[0]:
            label = results[0][0]["label"].lower()
            return label if label in VALID_EMOTIONS else "neutral"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Emotion detection failed: %s", exc)

    return "neutral"
