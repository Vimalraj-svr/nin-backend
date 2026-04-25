"""
Language detection service using langdetect.

Supports: English, Tamil, Hindi, Telugu, Kannada, Malayalam.
Handles code-mixed inputs (e.g. Tanglish) by detecting the dominant language.
Falls back to English on low confidence or unsupported language codes.
"""

import logging
from langdetect import detect_langs, LangDetectException

logger = logging.getLogger(__name__)

# Supported ISO 639-1 codes → display names
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "ta": "Tamil",
    "hi": "Hindi",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
}

# Minimum confidence to trust a detection
CONFIDENCE_THRESHOLD = 0.50

# Fallback
DEFAULT_LANG = ("en", "English")


def detect_language(text: str) -> tuple[str, str, float]:
    """
    Detect the dominant language of `text`.

    Returns:
        (iso_code, language_name, confidence)
        e.g. ("ta", "Tamil", 0.87)

    Edge cases:
      - Code-mixed input (Tanglish): returns dominant supported language
      - Low confidence: falls back to English
      - Unsupported script: falls back to English
    """
    if not text or not text.strip():
        return DEFAULT_LANG[0], DEFAULT_LANG[1], 0.0

    try:
        lang_probs = detect_langs(text)
    except LangDetectException as exc:
        logger.warning("langdetect failed: %s", exc)
        return DEFAULT_LANG[0], DEFAULT_LANG[1], 0.0

    if not lang_probs:
        return DEFAULT_LANG[0], DEFAULT_LANG[1], 0.0

    # First pass: find highest-confidence supported language
    for lp in lang_probs:
        if lp.lang in SUPPORTED_LANGUAGES and lp.prob >= CONFIDENCE_THRESHOLD:
            return lp.lang, SUPPORTED_LANGUAGES[lp.lang], round(lp.prob, 3)

    # Second pass: any supported language even below threshold (code-mixed case)
    for lp in lang_probs:
        if lp.lang in SUPPORTED_LANGUAGES and lp.prob >= 0.20:
            logger.info(
                "Low-confidence detection: %s (%.2f) — treating as dominant", lp.lang, lp.prob
            )
            return lp.lang, SUPPORTED_LANGUAGES[lp.lang], round(lp.prob, 3)

    # Fallback
    top = lang_probs[0]
    logger.info("Unsupported language '%s' (%.2f) — defaulting to English", top.lang, top.prob)
    return DEFAULT_LANG[0], DEFAULT_LANG[1], round(top.prob, 3)


def get_language_name(iso_code: str) -> str:
    """Return display name for an ISO code, defaulting to English."""
    return SUPPORTED_LANGUAGES.get(iso_code, "English")
