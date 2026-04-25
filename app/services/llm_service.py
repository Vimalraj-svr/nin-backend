import json
import logging
import os
import re

import httpx
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CF_TEXT_WORKER_URL = os.getenv("CF_TEXT_WORKER_URL", "").rstrip("/")
CF_WORKER_SECRET = os.getenv("CF_WORKER_SECRET", "")
MODEL_NAME = "gemini-2.5-flash"

_gemini_client: genai.Client | None = None

def _get_gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


async def _generate_via_cf(prompt: str) -> dict:
    """Try Cloudflare text worker. Raises on any failure."""
    headers = {"Content-Type": "application/json"}
    if CF_WORKER_SECRET:
        headers["X-Worker-Secret"] = CF_WORKER_SECRET

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            CF_TEXT_WORKER_URL,
            json={"prompt": prompt},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"Unexpected CF response shape: {data}")
        return result


async def _generate_via_gemini(prompt: str) -> dict:
    """Generate diary via Gemini."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = _get_gemini()
    response = await client.aio.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.5,
            response_mime_type="application/json",
        ),
    )
    return _parse_json_response(response.text)


async def generate_diary(prompt: str) -> dict:
    """
    Generate a diary entry. Uses Cloudflare Llama worker first (if configured),
    then falls back to Gemini.
    """
    if CF_TEXT_WORKER_URL:
        try:
            logger.info("Generating diary via Cloudflare text worker…")
            result = await _generate_via_cf(prompt)
            logger.info("CF generation OK")
            return result
        except Exception as e:
            logger.warning("CF text worker failed (%s) — falling back to Gemini", e)

    # Gemini fallback
    if not GEMINI_API_KEY:
        return _fallback_error("No text generation service configured.")
    try:
        logger.info("Generating diary via Gemini (%s)…", MODEL_NAME)
        return await _generate_via_gemini(prompt)
    except Exception as e:
        logger.error("Gemini generation failed: %s", e)
        return _fallback_error(str(e))


def _parse_json_response(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Could not parse LLM JSON — returning raw text as fallback")
        return _fallback_error(raw[:2000] if raw else "The model did not return valid output.")


def _fallback_error(detail: str) -> dict:
    return {
        "title_original": None,
        "content_original": None,
        "title_english": "Generation Error",
        "content_english": detail,
        "mood_summary": "unknown",
    }


LANG_NAMES = {
    "ta": "Tamil", "hi": "Hindi", "ml": "Malayalam",
    "te": "Telugu", "kn": "Kannada", "en": "English",
}

def _pronoun_note(gender: str | None) -> str:
    if gender == "male":
        return "Use he/him/his pronouns when referring to the user."
    if gender == "female":
        return "Use she/her/her pronouns when referring to the user."
    return ""

async def generate_weekly_letter(
    name: str,
    entries_summary: str,
    preferred_language: str = "en",
    gender: str | None = None,
) -> str:
    """Generate a warm weekly letter summarising the user's diary entries."""
    lang = LANG_NAMES.get(preferred_language, "English")
    pronoun = _pronoun_note(gender)
    prompt = f"""You are Ninaivugal, a close companion and diary keeper for {name}.
Based on their diary entries from this week (summarised below), write them a warm, literary, personal weekly letter.

Write in {lang}. If the language is not English, include a gentle English translation in italics below each paragraph.
{f"Pronoun note: {pronoun}" if pronoun else ""}

The letter should:
- Open with a warm, personal greeting using {name}'s name (not "Dear {name}" — something more intimate)
- Reflect on the emotional arc of the week — what they carried, what lifted them
- Name specific moments or feelings from their entries with care
- Close with an encouraging, grounding thought for the week ahead
- Feel like it was written by someone who truly knows and cares for {name}
- Be 3–4 paragraphs, literary but never flowery

Their week in entries:
{entries_summary}

Write only the letter — no subject line, no metadata."""

    if not GEMINI_API_KEY:
        return f"Your week had its own quiet rhythm, {name}. I saw it all."
    try:
        client = _get_gemini()
        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.85, max_output_tokens=1200),
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Weekly letter generation failed: %s", e)
        return ""


async def generate_memory_threads(entries_text: str, name: str, gender: str | None = None) -> list[dict]:
    """
    Finds recurring emotional themes and people across entries.
    Returns a list of { theme, observation, count_hint } objects.
    """
    pronoun = _pronoun_note(gender)
    prompt = f"""Analyse these diary entries written by {name} and identify 3–5 recurring emotional themes, people, or patterns.
{f"Pronoun note: {pronoun}" if pronoun else ""}

For each thread you find, return a JSON object with:
- "theme": a 2–4 word label (e.g. "longing for home", "quiet joy", "Meera")
- "observation": one warm, specific sentence noting what you noticed — as if you're a perceptive friend, not a therapist
- "icon": a single emoji that fits

Return ONLY a JSON array of these objects. No explanation.

Entries:
{entries_text}"""

    if not GEMINI_API_KEY:
        return []
    try:
        client = _get_gemini()
        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.6,
                response_mime_type="application/json",
            ),
        )
        import json, re
        raw = response.text.strip()
        raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error("Memory threads generation failed: %s", e)
        return []


async def answer_from_entries(
    question: str,
    entries_context: str,
    name: str,
    preferred_language: str = "en",
    gender: str | None = None,
) -> str:
    """Answers a question using only the user's own diary entries as context."""
    lang = LANG_NAMES.get(preferred_language, "English")
    pronoun = _pronoun_note(gender)
    prompt = f"""You are a warm, perceptive assistant answering {name}'s question about their own diary.
{f"Pronoun note: {pronoun}" if pronoun else ""}

RULES:
- Answer ONLY from the diary entries provided below. Do not invent anything.
- Address {name} by name naturally, as a close companion would.
- Each entry is labelled with its date [DD Mon YYYY] — use these dates to answer time-based questions (e.g. "this week", "last month").
- If the answer is not in the entries, say clearly: "I don't see anything about that in your recent entries, {name}."
- Be direct and specific. Quote or reference the actual entry dates and content.
- Keep the response concise — 2 to 4 sentences is usually enough.
- Respond in {lang}.

{name}'s diary entries (most recent first):
{entries_context}

Question: {question}"""

    if not GEMINI_API_KEY:
        return "I don't have enough memories yet to answer that."
    try:
        client = _get_gemini()
        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.7, max_output_tokens=600),
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Past self chat failed: %s", e)
        return "Something went quiet. Try again in a moment."


async def generate_birthday_wish(name: str, preferred_language: str = "en", gender: str | None = None) -> str:
    if not GEMINI_API_KEY:
        return f"Happy birthday, {name}! 🎂"

    lang = LANG_NAMES.get(preferred_language, "English")
    pronoun = _pronoun_note(gender)
    prompt = (
        f"Write a warm, heartfelt birthday wish for {name}. "
        f"{f'{pronoun} ' if pronoun else ''}"
        f"Write it in {lang} (if not English, also include an English translation in brackets). "
        f"Keep it personal, joyful, and under 3 sentences. No JSON — just the wish text."
    )
    try:
        client = _get_gemini()
        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.9),
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Birthday wish generation failed: %s", e)
        return f"Happy birthday, {name}! 🎂"
