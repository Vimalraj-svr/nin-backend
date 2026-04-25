"""Tests for the language detection service."""

import pytest
from app.services.language_detector import detect_language, get_language_name


class TestDetectLanguage:
    def test_pure_tamil(self):
        text = "இன்று என்னுடைய நாள் மிகவும் அழகாக இருந்தது. நான் மிகவும் மகிழ்ச்சியாக இருந்தேன்."
        code, name, conf = detect_language(text)
        assert code == "ta"
        assert name == "Tamil"
        assert conf > 0.3

    def test_pure_hindi(self):
        text = "आज का दिन बहुत अच्छा था। मैं बहुत खुश हूँ।"
        code, name, conf = detect_language(text)
        assert code == "hi"
        assert name == "Hindi"

    def test_english(self):
        text = "Today was a wonderful day. I went for a walk in the park."
        code, name, conf = detect_language(text)
        assert code == "en"
        assert name == "English"

    def test_tanglish_dominant_tamil(self):
        # Code-mixed — langdetect may vary but should return a supported language
        text = "Naan today office poren, traffic romba heavy ah irukku."
        code, name, conf = detect_language(text)
        # Should be a supported language (not crash)
        from app.services.language_detector import SUPPORTED_LANGUAGES
        assert code in SUPPORTED_LANGUAGES

    def test_empty_text_returns_english_fallback(self):
        code, name, conf = detect_language("")
        assert code == "en"
        assert name == "English"

    def test_whitespace_text_returns_english_fallback(self):
        code, name, conf = detect_language("   ")
        assert code == "en"

    def test_hindi_english_mix(self):
        text = "Main aaj bahut tired hoon. Office mein bohot kaam tha."
        code, name, conf = detect_language(text)
        from app.services.language_detector import SUPPORTED_LANGUAGES
        assert code in SUPPORTED_LANGUAGES


class TestGetLanguageName:
    def test_known_codes(self):
        assert get_language_name("ta") == "Tamil"
        assert get_language_name("hi") == "Hindi"
        assert get_language_name("te") == "Telugu"
        assert get_language_name("kn") == "Kannada"
        assert get_language_name("ml") == "Malayalam"
        assert get_language_name("en") == "English"

    def test_unknown_code_defaults_to_english(self):
        assert get_language_name("zh") == "English"
        assert get_language_name("xx") == "English"
