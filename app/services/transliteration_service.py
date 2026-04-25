try:
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate as _translit
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_SCRIPT_MAP = {
    "ta": "TAMIL",
    "hi": "DEVANAGARI",
    "ml": "MALAYALAM",
    "te": "TELUGU",
    "kn": "KANNADA",
}


def name_in_script(name: str, lang: str) -> str | None:
    """
    Returns the first name transliterated into the target Indic script.
    Falls back to None for English/auto or if the library is unavailable.
    """
    if not _AVAILABLE:
        return None
    script_attr = _SCRIPT_MAP.get(lang)
    if not script_attr:
        return None
    try:
        first = name.strip().split()[0].lower()
        script = getattr(sanscript, script_attr)
        return _translit(first, sanscript.ITRANS, script)
    except Exception:
        return None
