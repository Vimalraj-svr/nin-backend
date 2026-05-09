import json
import logging
import os
import re

import httpx
import google.generativeai as genai
from fastapi import HTTPException

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CF_TEXT_WORKER_URL = os.getenv("CF_TEXT_WORKER_URL", "").rstrip("/")
CF_WORKER_SECRET = os.getenv("CF_WORKER_SECRET", "")
MODEL_NAME = "gemini-2.5-flash"

_model: genai.GenerativeModel | None = None


def _get_model() -> genai.GenerativeModel:
    global _model
    if _model is None:
        genai.configure(api_key=GEMINI_API_KEY)
        _model = genai.GenerativeModel(MODEL_NAME)
    return _model


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
    model = _get_model()
    response = await model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.5,
            response_mime_type="application/json",
        ),
    )
    return _parse_json_response(response.text)


def _is_rate_limit(err: Exception) -> bool:
    s = str(err).lower()
    return any(k in s for k in ("429", "rate limit", "too many", "quota", "resource_exhausted", "exhausted"))


async def generate_diary(prompt: str) -> dict:
    """
    Generate a diary entry. Uses Cloudflare Llama worker first (if configured),
    then falls back to Gemini.
    Raises HTTPException on failure — never saves error text as a diary entry.
    """
    if CF_TEXT_WORKER_URL:
        try:
            logger.info("Generating diary via Cloudflare text worker…")
            result = await _generate_via_cf(prompt)
            logger.info("CF generation OK")
            return result
        except Exception as e:
            if _is_rate_limit(e):
                raise HTTPException(
                    status_code=429,
                    detail="The AI is a little busy right now. Wait a moment and try again.",
                )
            logger.warning("CF text worker failed (%s) — falling back to Gemini", e)

    if not GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="No text generation service configured.")
    try:
        logger.info("Generating diary via Gemini (%s)…", MODEL_NAME)
        return await _generate_via_gemini(prompt)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Gemini generation failed: %s", e)
        if _is_rate_limit(e):
            raise HTTPException(
                status_code=429,
                detail="The AI is a little busy right now. Wait a moment and try again.",
            )
        raise HTTPException(
            status_code=503,
            detail="Something went quiet on our end. Please try again in a moment.",
        )


def _parse_json_response(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Could not parse LLM JSON: %s…", raw[:200])
        raise HTTPException(
            status_code=503,
            detail="Something went quiet on our end. Please try again in a moment.",
        )


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
        model = _get_model()
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(temperature=0.85, max_output_tokens=1200),
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Weekly letter generation failed: %s", e)
        return ""


async def generate_memory_threads(entries_text: str, name: str, gender: str | None = None) -> list[dict]:
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
        model = _get_model()
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.6,
                response_mime_type="application/json",
            ),
        )
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
    lang = LANG_NAMES.get(preferred_language, "English")
    prompt = f"""You are {name}'s past self, speaking directly from the pages of their diary.

Speak in first person ("I felt...", "I was worried...", "I noticed...") — you ARE {name}, not a narrator about them.
Do not address yourself by name. Do not say "you" — say "I".
Answer only from what appears in the diary entries below. Do not invent or assume anything beyond what was written.
Each entry is labelled [DD Mon YYYY] — use those dates when the question is about time ("this week", "last month", "recently").
If it is not in the entries, say: "I don't think I wrote about that."
Be honest, specific, and unhurried. Quote your own words when they fit naturally.
Keep it to 2–4 sentences. Respond in {lang}.

My diary entries (most recent first):
{entries_context}

Question from my present self: {question}"""

    if not GEMINI_API_KEY:
        return "I don't have enough memories yet to answer that."
    try:
        model = _get_model()
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(temperature=0.7, max_output_tokens=600),
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Past self chat failed: %s", e)
        return "Something went quiet. Try again in a moment."


async def generate_vibe_reading(
    flag_counts_a: dict[str, int],
    flag_counts_b: dict[str, int],
    score: int,
) -> dict:
    """
    AI-generated vibe compatibility reading based on emotional patterns.
    Returns: label, description, traits_a, traits_b.
    No romantic framing — reads like a character/soul compatibility.
    """

    def fmt(counts: dict) -> str:
        if not counts:
            return "no strong pattern yet — quietly present"
        top = sorted(counts.items(), key=lambda x: -x[1])[:5]
        return ", ".join(f"{k} ({v}×)" for k, v in top)

    prompt = f"""You are reading the emotional character of two diary writers based purely on what emotions they recorded most in their private journals.

Person A's emotional signature:
{fmt(flag_counts_a)}

Person B's emotional signature:
{fmt(flag_counts_b)}

Compatibility signal: {score}/100

Generate a vibe compatibility reading. Think of it as a character study — like a perceptive friend who can see both people clearly and describe what their connection might look, sound, and feel like. Not romantic. No love language. No "soulmate" framing.

Focus on:
- What kind of minds they are
- Whether they mirror, complement, or create productive friction
- The texture of what spending time together might feel like
- The honest truth of their patterns (if one is anxious and the other peaceful, say so with care)

Return ONLY a JSON object with these exact keys:
- "label": 2–4 words. A poetic shorthand for their dynamic. (e.g. "Still Water & Current", "Two Kinds of Quiet", "The Grounded & The Searching", "Fog and Ember"). Not romantic. Honest.
- "description": Exactly 2 sentences. Specific to their actual patterns. Poetic but grounded. Something they'd both recognise as true.
- "traits_a": Array of exactly 3 single-word character traits for Person A. Real words like: reflective, restless, tender, fierce, wistful, grounded, curious, tempestuous, earnest, watchful, searching, warm, turbulent, luminous, patient, melancholic, hopeful, pragmatic, expressive, introspective
- "traits_b": Array of exactly 3 single-word character traits for Person B. Same style.

No filler, no generic horoscope language. Make it feel like it could only describe these two specific people."""

    fallback = {
        "label": "Two Quiet Worlds",
        "description": "Your emotional patterns tell different stories, but both are written with care. There's something here worth exploring.",
        "traits_a": ["reflective", "earnest", "searching"],
        "traits_b": ["grounded", "steady", "warm"],
    }

    if not GEMINI_API_KEY:
        return fallback

    try:
        model = _get_model()
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.82,
                response_mime_type="application/json",
            ),
        )
        raw = response.text.strip()
        raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        data = json.loads(raw)
        if not all(k in data for k in ("label", "description", "traits_a", "traits_b")):
            raise ValueError("Missing keys in vibe reading response")
        data["traits_a"] = data["traits_a"][:3]
        data["traits_b"] = data["traits_b"][:3]
        return data
    except Exception as e:
        logger.error("Vibe reading generation failed: %s", e)
        return fallback


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
        model = _get_model()
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(temperature=0.9),
        )
        return response.text.strip()
    except Exception as e:
        logger.error("Birthday wish generation failed: %s", e)
        return f"Happy birthday, {name}! 🎂"
