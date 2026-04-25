import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pytz
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.entry import output_mode_for
from app.services.embedding_service import generate_embedding
from app.services.encryption_service import encrypt_if_present
from app.services.llm_service import generate_diary
from app.services.memory_service import retrieve_similar
from app.services.prompt_builder import build_prompt

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def _parse_entry_date(entry_date: Optional[str]) -> date:
    """Return a date object for the given ISO string, or today IST."""
    if entry_date:
        try:
            return date.fromisoformat(entry_date)
        except ValueError:
            pass
    return datetime.now(IST).date()


def _date_window_utc(d: date) -> tuple[datetime, datetime]:
    """Return (start_utc, end_utc) spanning the full IST day `d`."""
    start_ist = IST.localize(datetime(d.year, d.month, d.day, 0, 0, 0))
    end_ist   = start_ist + timedelta(days=1)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


async def has_entry_for_date(db: AsyncIOMotorDatabase, user_id: str, d: date) -> bool:
    """Returns True if the user already has an entry for IST date `d`."""
    start_utc, end_utc = _date_window_utc(d)
    count = await db["entries"].count_documents({
        "user_id": user_id,
        "created_at": {"$gte": start_utc, "$lt": end_utc},
    })
    return count > 0


async def get_available_dates(db: AsyncIOMotorDatabase, user_id: str, days: int = 3) -> list[str]:
    """
    Returns ISO date strings (YYYY-MM-DD, IST) for the last `days` days
    that do NOT yet have an entry — up to `days` results, newest first.
    """
    today = datetime.now(IST).date()
    available = []
    for i in range(days):
        d = today - timedelta(days=i)
        if not await has_entry_for_date(db, user_id, d):
            available.append(d.isoformat())
    return available


# Keep old name as an alias so nothing else breaks
async def has_entry_today(db: AsyncIOMotorDatabase, user_id: str) -> bool:
    return await has_entry_for_date(db, user_id, datetime.now(IST).date())


async def run_pipeline(
    transcript: str,
    preferred_language: str,
    user_id: str,
    db: AsyncIOMotorDatabase,
    language_override: Optional[str] = None,
    entry_date: Optional[str] = None,
) -> dict:
    output_mode = output_mode_for(preferred_language)
    target_date = _parse_entry_date(entry_date)

    # ── TRANSCRIPT LOG ────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("TRANSCRIPT [user=%s, lang=%s, mode=%s, date=%s]",
                user_id, preferred_language, output_mode, target_date)
    logger.info("-" * 60)
    logger.info("%s", transcript)
    logger.info("=" * 60)
    # ─────────────────────────────────────────────────────────────────────

    # 1. Embedding (use raw transcript — before encryption)
    embedding = generate_embedding(transcript)
    embedding_list = embedding.tolist()

    # 2. Past memories (per-user, isolated)
    memories = await retrieve_similar(db, user_id, embedding_list, k=3)
    logger.info("Retrieved %d past memories for user %s", len(memories), user_id)

    # 3. Prompt
    prompt = build_prompt(
        transcript=transcript,
        memories=memories,
        output_mode=output_mode,
        preferred_language=preferred_language,
    )

    # 4. LLM generation
    llm = await generate_diary(prompt)

    # ── LLM OUTPUT LOG ────────────────────────────────────────────────────
    logger.info("LLM OUTPUT [user=%s]", user_id)
    logger.info("-" * 60)
    logger.info("title_original : %s", llm.get("title_original"))
    logger.info("content_original:\n%s", llm.get("content_original"))
    logger.info("title_english  : %s", llm.get("title_english"))
    logger.info("content_english:\n%s", llm.get("content_english"))
    logger.info("mood_summary   : %s", llm.get("mood_summary"))
    logger.info("=" * 60)
    # ─────────────────────────────────────────────────────────────────────

    # 5. Language detection
    if language_override and language_override not in ("auto", ""):
        lang_code = language_override
    else:
        lang_code = llm.get("detected_language_code",
                            preferred_language if preferred_language not in ("auto", "bilingual") else "en")

    # 6. Build created_at: use noon IST of the target date so it lands
    #    definitively within that day regardless of UTC offset.
    noon_ist = IST.localize(datetime(target_date.year, target_date.month, target_date.day, 12, 0, 0))
    created_at_utc = noon_ist.astimezone(timezone.utc)

    entry = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "transcript": encrypt_if_present(transcript),
        "detected_language": lang_code,
        "preferred_language": preferred_language,
        "output_mode": output_mode,
        "title_original": encrypt_if_present(llm.get("title_original")),
        "content_original": encrypt_if_present(llm.get("content_original")),
        "title_english": encrypt_if_present(llm.get("title_english")),
        "content_english": encrypt_if_present(llm.get("content_english")),
        "mood_summary": encrypt_if_present(llm.get("mood_summary", llm.get("dominant_emotion", "unknown"))),
        "embedding": embedding_list,
        "created_at": created_at_utc,
    }

    await db["entries"].insert_one(dict(entry))
    logger.info("Entry saved: %s for user %s (date=%s)", entry["id"], user_id, target_date)

    return {
        **entry,
        "transcript": transcript,
        "title_original": llm.get("title_original"),
        "content_original": llm.get("content_original"),
        "title_english": llm.get("title_english"),
        "content_english": llm.get("content_english"),
        "mood_summary": llm.get("mood_summary", llm.get("dominant_emotion", "unknown")),
    }
