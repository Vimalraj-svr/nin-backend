LANGUAGE_NAMES = {
    'en': 'English',
    'ta': 'Tamil (தமிழ்)',
    'hi': 'Hindi (हिंदी)',
    'ml': 'Malayalam (മലയാളം)',
    'te': 'Telugu (తెలుగు)',
    'kn': 'Kannada (ಕನ್ನಡ)',
}

BILINGUAL_PAIRS = {
    'ta': 'Tamil and English',
    'hi': 'Hindi and English',
    'ml': 'Malayalam and English',
    'te': 'Telugu and English',
    'kn': 'Kannada and English',
}


def _language_instruction(preferred_language: str, output_mode: str) -> str:
    if output_mode == 'BILINGUAL':
        return """OUTPUT LANGUAGE: BILINGUAL (naturally code-switched — one entry)
Write ONE diary entry that naturally mixes the regional language and English in the same text,
the way a bilingual person actually speaks and writes — like Tanglish, Hinglish, etc.
Do NOT write two separate versions. Blend both languages in every paragraph naturally.
A sentence can start in Tamil and finish in English. A thought can switch mid-way.
Populate only `title_original` and `content_original`.
Set `title_english` and `content_english` to null."""

    if output_mode == 'ENGLISH_REFINED':
        return """OUTPUT LANGUAGE: English only
Write the diary entry entirely in clear, natural English.
Translate any non-English phrases faithfully — do not leave foreign words untranslated.
Only populate `title_english` and `content_english`."""

    # SAME_LANGUAGE — may force a specific language
    if preferred_language in LANGUAGE_NAMES and preferred_language not in ('en', 'auto'):
        lang = LANGUAGE_NAMES[preferred_language]
        return f"""OUTPUT LANGUAGE: {lang} only
Write the diary entry entirely in {lang} script.
Even if the user spoke in a mix of languages, the final diary MUST be in {lang}.
Only populate `title_original` and `content_original`."""

    return """OUTPUT LANGUAGE: Same as spoken
Write the diary entry in the same language the user naturally spoke in.
Preserve code-switches (e.g., Tanglish) as part of the voice.
Only populate `title_original` and `content_original`."""


def _schema_for(output_mode: str) -> str:
    base = '''{
  "dominant_emotion": "<joy|sadness|anger|longing|tenderness|anxiety|pride|relief|contentment|restlessness>",
  "detected_language_code": "<ta|hi|en|te|kn|ml>",'''

    if output_mode == 'BILINGUAL':
        return base + '''
  "title_original": "<slightly poetic but complete title — natural mix of regional language and English>",
  "content_original": "<full diary — 2 to 4 paragraphs of naturally code-switched text>",
  "title_english": null,
  "content_english": null,
  "mood_summary": "<one honest sentence about what this person is feeling>"
}'''

    if output_mode == 'ENGLISH_REFINED':
        return base + '''
  "title_original": null,
  "content_original": null,
  "title_english": "<slightly poetic but complete title in English — clearly describes what happened>",
  "content_english": "<full diary entry in English — 2 to 4 paragraphs>",
  "mood_summary": "<one honest sentence about what this person is feeling>"
}'''

    return base + '''
  "title_original": "<slightly poetic but complete title in the chosen language — clearly describes what happened>",
  "content_original": "<full diary entry — 2 to 4 paragraphs>",
  "title_english": null,
  "content_english": null,
  "mood_summary": "<one honest sentence about what this person is feeling>"
}'''


def build_prompt(
    transcript: str,
    memories: list[str],
    output_mode: str,
    preferred_language: str = 'auto',
) -> str:
    memory_block = "\n".join(f"  • {m}" for m in memories) if memories else None
    memory_section = f"""
══════════════════════════════════════════
RELATED PAST ENTRIES (for context only)
══════════════════════════════════════════
Reference these only if the user's transcript explicitly connects to them.
Do NOT weave them in unless the user mentioned it.

{memory_block}
""" if memory_block else ""

    lang_instruction = _language_instruction(preferred_language, output_mode)
    schema = _schema_for(output_mode)

    return f"""You are a personal diary writer. Your job is to take the user's raw spoken transcript and shape it into a warm, personal diary entry.

RULES — read carefully:

  FACTS — be faithful:
  • Never invent events, people, or places that the user did not mention.
  • Never add sensory details (smells, textures, weather) that were not said.
  • Every fact in the output must come from the transcript.

  EMOTIONS — be genuine, not invented:
  • If the user expressed a feeling (happy, excited, grateful, tired), give it full voice.
    Lean into what they felt. Make the reader feel it too.
  • Do not invent emotions they did not express. But if they said "I felt happy", don't just
    write "I felt happy" — write it in a way that is warm and alive.

  LANGUAGE — clean it up:
  • Fix obvious speech-to-text errors and mispronounced words. If the user likely said a
    brand name, place name, or proper noun that was slightly garbled, correct it to the
    most plausible spelling (e.g. "hundai" → "Hyundai", "chee-nai" → "Chennai").
  • Remove filler words (um, uh, like, basically) and false starts.
  • Do not write in direct speech. No "I went to the showroom and I looked at the bike
    and then I felt happy." — write as flowing diary prose.

  STYLE — close to heart:
  • Write in first person, diary style. Personal, warm, genuine.
  • Vary sentence rhythm. Short sentences for feeling, longer ones for detail.
  • The entry should feel like it was written by the person themselves — not a journalist,
    not a novelist. Just someone being honest and present.
  • Length: proportional to the transcript. A brief note becomes a short entry (1–2 paragraphs).
    A detailed account becomes 3–4 paragraphs.

  TITLE:
  • Slightly poetic but complete — clearly describes what happened with warmth.
  • Good: "An afternoon at the showroom, and one choice made"
  • Bad: "The machine calls" (too vague) · "Visited bike showroom" (too plain)

  MOOD SUMMARY:
  • One honest, specific sentence about what the person is feeling. Plain language.

------------------------------------------
{lang_instruction}
------------------------------------------

USER TRANSCRIPT (raw — your only source of facts):
{transcript}
{memory_section}
------------------------------------------
REQUIRED OUTPUT — JSON only, no markdown fences, no explanation:
{schema}"""
