"""Tests for the modular prompt builder."""

import pytest
from app.models.entry import OutputMode
from app.services.prompt_builder import build_prompt


SAMPLE_TRANSCRIPT = "Today was a hard day. I missed my grandmother a lot."
SAMPLE_MEMORIES = [
    "[sadness] She used to make amazing filter coffee every morning.",
    "[joy] We used to sing old Tamil songs together on Sundays.",
]


class TestBuildPrompt:
    def _make_prompt(self, mode: OutputMode, memories=None) -> str:
        return build_prompt(
            transcript=SAMPLE_TRANSCRIPT,
            language="Tamil",
            language_code="ta",
            emotion="sadness",
            memories=memories or [],
            mode=mode,
        )

    # ── SAME_LANGUAGE ────────────────────────────────────────────────────

    def test_same_language_contains_transcript(self):
        prompt = self._make_prompt(OutputMode.SAME_LANGUAGE)
        assert SAMPLE_TRANSCRIPT in prompt

    def test_same_language_instructs_tamil(self):
        prompt = self._make_prompt(OutputMode.SAME_LANGUAGE)
        assert "Tamil" in prompt
        assert "SAME_LANGUAGE" in prompt

    def test_same_language_schema_has_content_original(self):
        prompt = self._make_prompt(OutputMode.SAME_LANGUAGE)
        assert "content_original" in prompt
        assert "content_english" not in prompt

    # ── ENGLISH_REFINED ──────────────────────────────────────────────────

    def test_english_refined_schema_has_content_english(self):
        prompt = self._make_prompt(OutputMode.ENGLISH_REFINED)
        assert "content_english" in prompt
        assert "content_original" not in prompt

    def test_english_refined_no_content_original(self):
        prompt = self._make_prompt(OutputMode.ENGLISH_REFINED)
        assert "content_original" not in prompt

    # ── BILINGUAL ────────────────────────────────────────────────────────

    def test_bilingual_has_both_schemas(self):
        prompt = self._make_prompt(OutputMode.BILINGUAL)
        assert "content_original" in prompt
        assert "content_english" in prompt

    def test_bilingual_has_mood_summary(self):
        prompt = self._make_prompt(OutputMode.BILINGUAL)
        assert "mood_summary" in prompt

    # ── Memories ─────────────────────────────────────────────────────────

    def test_memories_included_in_prompt(self):
        prompt = self._make_prompt(OutputMode.BILINGUAL, memories=SAMPLE_MEMORIES)
        assert "filter coffee" in prompt
        assert "Tamil songs" in prompt

    def test_empty_memories_shows_no_past(self):
        prompt = self._make_prompt(OutputMode.SAME_LANGUAGE, memories=[])
        assert "No past memories" in prompt

    # ── General ──────────────────────────────────────────────────────────

    def test_emotion_in_prompt(self):
        prompt = self._make_prompt(OutputMode.SAME_LANGUAGE)
        assert "sadness" in prompt

    def test_language_code_in_prompt(self):
        prompt = self._make_prompt(OutputMode.SAME_LANGUAGE)
        assert "(ta)" in prompt

    def test_prompt_ends_with_json_instruction(self):
        prompt = self._make_prompt(OutputMode.SAME_LANGUAGE)
        assert "JSON" in prompt
