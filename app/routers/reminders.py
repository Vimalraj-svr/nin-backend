import os
import logging
from datetime import datetime, timedelta, timezone

import pytz
from fastapi import APIRouter, Header, HTTPException

from app.db import get_mongo_db
from app.services.email_service import send_reminder_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reminders", tags=["reminders"])

CRON_SECRET = os.getenv("CRON_SECRET", "")
IST = pytz.timezone("Asia/Kolkata")


def _verify_secret(secret: str):
    if CRON_SECRET and secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


async def _users_without_entry_today() -> list[dict]:
    """
    Returns all reminder-enabled users who have NOT written an entry today (IST).
    """
    db = get_mongo_db()

    # IST day boundaries → UTC for MongoDB comparison
    today_ist = datetime.now(IST).date()
    start_ist = IST.localize(datetime(today_ist.year, today_ist.month, today_ist.day))
    end_ist = start_ist + timedelta(days=1)
    start_utc = start_ist.astimezone(timezone.utc)
    end_utc = end_ist.astimezone(timezone.utc)

    # All reminder-enabled users
    cursor = db["users"].find(
        {"reminder_enabled": True},
        {"_id": 1, "name": 1, "email": 1},
    )
    all_users = await cursor.to_list(length=None)

    # Users who already wrote today
    wrote_today = await db["entries"].distinct(
        "user_id",
        {"created_at": {"$gte": start_utc, "$lt": end_utc}},
    )
    wrote_set = set(wrote_today)

    # Return only those who haven't written
    return [u for u in all_users if str(u["_id"]) not in wrote_set]


@router.post("/send")
async def send_reminders(x_cron_secret: str = Header(default="")):
    """
    Call this daily at 7:00 PM IST from your cron job.
    Sends a reminder email to every user who has reminders on but hasn't
    written an entry today. Protect with CRON_SECRET + X-Cron-Secret header.

    Example cron (crontab, UTC): 30 13 * * *   curl -X POST https://your-api/api/reminders/send -H "X-Cron-Secret: ..."
    (13:30 UTC = 19:00 IST)
    """
    _verify_secret(x_cron_secret)

    pending = await _users_without_entry_today()
    sent = failed = 0

    for user in pending:
        ok = send_reminder_email(user.get("name", "there"), user["email"])
        if ok:
            sent += 1
        else:
            failed += 1

    logger.info("Reminders: sent=%d failed=%d skipped=%d", sent, failed,
                (await _count_enabled()) - len(pending))
    return {
        "sent": sent,
        "failed": failed,
        "skipped_already_wrote": (await _count_enabled()) - len(pending),
    }


async def _count_enabled() -> int:
    db = get_mongo_db()
    return await db["users"].count_documents({"reminder_enabled": True})


@router.get("/preview")
async def preview_pending(x_cron_secret: str = Header(default="")):
    """
    Dry-run: returns names of users who would receive a reminder right now.
    Useful for testing before wiring up the cron.
    """
    _verify_secret(x_cron_secret)
    pending = await _users_without_entry_today()
    return {
        "would_send_to": [{"name": u.get("name"), "email": u["email"]} for u in pending],
        "count": len(pending),
    }
