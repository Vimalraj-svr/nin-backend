import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytz
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import get_mongo_db
from app.models.user import UserDB
from app.services.auth import get_current_user

IST = pytz.timezone("Asia/Kolkata")

MOOD_HUES: dict[str, int] = {
    "joyful": 18, "happy": 18, "joy": 18,
    "tender": 42, "nostalgic": 42, "love": 340,
    "content": 56, "calm": 56, "peace": 56, "peaceful": 56,
    "uncertain": 228, "heavy": 260, "sad": 260, "grief": 260,
    "anxious": 284, "worry": 284, "stress": 284, "anxious": 284,
    "anger": 28, "angry": 28,
    "hopeful": 130, "hope": 130,
}

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/social", tags=["social"])


def _to_str_id(doc: dict) -> str:
    return str(doc.get("_id", ""))


def _profile(doc: dict, following_ids: set, restricted_ids: set) -> dict:
    uid = _to_str_id(doc)
    return {
        "id": uid,
        "name": doc.get("name", ""),
        "is_following": uid in following_ids,
        "is_restricted": uid in restricted_ids,
    }


async def _load_user(db, user_id: str) -> dict:
    try:
        doc = await db["users"].find_one({"_id": ObjectId(user_id)}, {"_id": 1, "name": 1})
    except Exception:
        raise HTTPException(status_code=404, detail="User not found.")
    if not doc:
        raise HTTPException(status_code=404, detail="User not found.")
    return doc


@router.get("/users-info")
async def get_users_info(
    ids: str = Query(..., description="Comma-separated user IDs"),
    current_user: UserDB = Depends(get_current_user),
):
    """Batch lookup of user names by ID list."""
    id_list = [i.strip() for i in ids.split(",") if i.strip()][:20]
    if not id_list:
        return []
    try:
        oid_list = [ObjectId(uid) for uid in id_list]
    except Exception:
        return []
    docs = await get_mongo_db()["users"].find(
        {"_id": {"$in": oid_list}}, {"_id": 1, "name": 1}
    ).to_list(length=20)
    return [{"id": str(d["_id"]), "name": d.get("name", "")} for d in docs]


async def _get_excluded(db, user_id: str) -> tuple[set, set]:
    my_restrictions = await db["restrictions"].find(
        {"user_id": user_id}, {"target_id": 1}
    ).to_list(length=None)
    restricted_ids = {r["target_id"] for r in my_restrictions}
    blocked_by = await db["restrictions"].find(
        {"target_id": user_id}, {"user_id": 1}
    ).to_list(length=None)
    blocked_by_ids = {r["user_id"] for r in blocked_by}
    return restricted_ids, blocked_by_ids


async def _enrich_with_follow(db, users: list, current_user_id: str, restricted_ids: set) -> list:
    user_ids = [_to_str_id(u) for u in users]
    following_docs = await db["follows"].find(
        {"follower_id": current_user_id, "following_id": {"$in": user_ids}},
        {"following_id": 1},
    ).to_list(length=None)
    following_ids = {f["following_id"] for f in following_docs}
    return [_profile(u, following_ids, restricted_ids) for u in users]


@router.get("/discover")
async def discover_users(current_user: UserDB = Depends(get_current_user)):
    """Return all users (up to 50) excluding self and restricted, for the Discover tab."""
    db = get_mongo_db()
    restricted_ids, blocked_by_ids = await _get_excluded(db, current_user.id)
    excluded = restricted_ids | blocked_by_ids
    cursor = db["users"].find({}, {"_id": 1, "name": 1}).limit(50)
    users = await cursor.to_list(length=50)
    users = [u for u in users if _to_str_id(u) != current_user.id and _to_str_id(u) not in excluded]
    return await _enrich_with_follow(db, users, current_user.id, restricted_ids)


@router.get("/search")
async def search_users(
    q: str = Query(..., min_length=1),
    current_user: UserDB = Depends(get_current_user),
):
    """Search users by name. Excludes self and mutually restricted users."""
    db = get_mongo_db()
    restricted_ids, blocked_by_ids = await _get_excluded(db, current_user.id)
    excluded = restricted_ids | blocked_by_ids
    cursor = db["users"].find(
        {"name": {"$regex": q, "$options": "i"}},
        {"_id": 1, "name": 1},
    ).limit(15)
    users = await cursor.to_list(length=15)
    users = [u for u in users if _to_str_id(u) != current_user.id and _to_str_id(u) not in excluded]
    if not users:
        return []
    return await _enrich_with_follow(db, users, current_user.id, restricted_ids)


@router.get("/profile/{user_id}")
async def get_profile(
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Get a user's public profile with follow stats, streak, and vibe."""
    db = get_mongo_db()
    doc = await _load_user(db, user_id)
    uid = _to_str_id(doc)

    is_following, is_restricted, follower_count, following_count, streak, entry_count, vibe_hue = (
        await _parallel_profile_data(db, uid, current_user.id)
    )

    return {
        "id": uid,
        "name": doc["name"],
        "is_following": is_following,
        "is_restricted": is_restricted,
        "follower_count": follower_count,
        "following_count": following_count,
        "streak": streak,
        "entry_count": entry_count,
        "vibe_hue": vibe_hue,
    }


async def _parallel_profile_data(db, uid: str, viewer_id: str):
    from app.services.encryption_service import decrypt_if_present
    import asyncio

    async def _streak():
        cursor = db["entries"].find(
            {"user_id": uid}, {"created_at": 1}
        ).sort("created_at", -1)
        docs = await cursor.to_list(length=None)
        written = set()
        for d in docs:
            if d.get("created_at"):
                written.add(d["created_at"].astimezone(IST).strftime("%Y-%m-%d"))
        count = 0
        check = datetime.now(IST).date()
        while check.isoformat() in written:
            count += 1
            check = check - timedelta(days=1)
        return count

    async def _vibe():
        since = datetime.now(timezone.utc) - timedelta(days=30)
        docs = await db["entries"].find(
            {"user_id": uid, "created_at": {"$gte": since}},
            {"mood_summary": 1},
        ).to_list(length=10)
        hues = []
        for d in docs:
            mood = (decrypt_if_present(d.get("mood_summary")) or "").lower()
            for kw, hue in MOOD_HUES.items():
                if kw in mood:
                    hues.append(hue)
                    break
        return round(sum(hues) / len(hues)) if hues else 42

    is_following, is_restricted, follower_count, following_count, total, streak_val, vibe_val = (
        await asyncio.gather(
            db["follows"].find_one({"follower_id": viewer_id, "following_id": uid}),
            db["restrictions"].find_one({"user_id": viewer_id, "target_id": uid}),
            db["follows"].count_documents({"following_id": uid}),
            db["follows"].count_documents({"follower_id": uid}),
            db["entries"].count_documents({"user_id": uid}),
            _streak(),
            _vibe(),
        )
    )
    return bool(is_following), bool(is_restricted), follower_count, following_count, streak_val, total, vibe_val


@router.post("/follow/{user_id}", status_code=201)
async def follow_user(
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot follow yourself.")

    db = get_mongo_db()
    await _load_user(db, user_id)

    existing = await db["follows"].find_one(
        {"follower_id": current_user.id, "following_id": user_id}
    )
    if existing:
        return {"message": "Already following."}

    now = datetime.now(timezone.utc)
    await db["follows"].insert_one({
        "follower_id": current_user.id,
        "following_id": user_id,
        "created_at": now,
    })
    notif = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "type": "new_follower",
        "actor_id": current_user.id,
        "actor_name": current_user.name,
        "read": False,
        "created_at": now,
    }
    from app.services.firebase_service import write_notification
    write_notification(user_id, notif)
    return {"message": "Followed."}


@router.delete("/follow/{user_id}")
async def unfollow_user(
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    db = get_mongo_db()
    result = await db["follows"].delete_one(
        {"follower_id": current_user.id, "following_id": user_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not following this user.")
    return {"message": "Unfollowed."}


@router.post("/restrict/{user_id}", status_code=201)
async def restrict_user(
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot restrict yourself.")

    db = get_mongo_db()
    await _load_user(db, user_id)

    existing = await db["restrictions"].find_one(
        {"user_id": current_user.id, "target_id": user_id}
    )
    if existing:
        return {"message": "Already restricted."}

    # Remove any follow relationship in both directions
    await db["follows"].delete_one({"follower_id": current_user.id, "following_id": user_id})
    await db["follows"].delete_one({"follower_id": user_id, "following_id": current_user.id})

    await db["restrictions"].insert_one({
        "user_id": current_user.id,
        "target_id": user_id,
        "created_at": datetime.now(timezone.utc),
    })
    return {"message": "Restricted."}


@router.delete("/restrict/{user_id}")
async def unrestrict_user(
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    db = get_mongo_db()
    result = await db["restrictions"].delete_one(
        {"user_id": current_user.id, "target_id": user_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not restricted.")
    return {"message": "Unrestricted."}


async def _enrich_users(db, ids: list[str], current_user_id: str) -> list[dict]:
    """Fetch user docs and enrich with follow/restrict status."""
    if not ids:
        return []
    try:
        oid_list = [ObjectId(uid) for uid in ids]
    except Exception:
        return []
    users = await db["users"].find(
        {"_id": {"$in": oid_list}}, {"_id": 1, "name": 1}
    ).to_list(length=None)

    following_docs = await db["follows"].find(
        {"follower_id": current_user_id, "following_id": {"$in": ids}},
        {"following_id": 1},
    ).to_list(length=None)
    following_ids = {f["following_id"] for f in following_docs}

    restricted_docs = await db["restrictions"].find(
        {"user_id": current_user_id}, {"target_id": 1}
    ).to_list(length=None)
    restricted_ids = {r["target_id"] for r in restricted_docs}

    return [_profile(u, following_ids, restricted_ids) for u in users]


@router.get("/followers")
async def get_followers(current_user: UserDB = Depends(get_current_user)):
    """List users following me."""
    db = get_mongo_db()
    docs = await db["follows"].find(
        {"following_id": current_user.id}, {"follower_id": 1}
    ).to_list(length=None)
    ids = [d["follower_id"] for d in docs]
    return await _enrich_users(db, ids, current_user.id)


@router.get("/following")
async def get_following(current_user: UserDB = Depends(get_current_user)):
    """List users I follow."""
    db = get_mongo_db()
    docs = await db["follows"].find(
        {"follower_id": current_user.id}, {"following_id": 1}
    ).to_list(length=None)
    ids = [d["following_id"] for d in docs]
    return await _enrich_users(db, ids, current_user.id)


@router.get("/restricted")
async def get_restricted(current_user: UserDB = Depends(get_current_user)):
    """List users I've restricted (blocked)."""
    db = get_mongo_db()
    docs = await db["restrictions"].find(
        {"user_id": current_user.id}, {"target_id": 1}
    ).to_list(length=None)
    ids = [d["target_id"] for d in docs]
    if not ids:
        return []
    try:
        oid_list = [ObjectId(uid) for uid in ids]
    except Exception:
        return []
    users = await db["users"].find(
        {"_id": {"$in": oid_list}}, {"_id": 1, "name": 1}
    ).to_list(length=None)
    restricted_ids = set(ids)
    return [_profile(u, set(), restricted_ids) for u in users]


# ── Notifications ─────────────────────────────────────────────────────────────

@router.post("/notifications/read-all")
async def mark_all_read(current_user: UserDB = Depends(get_current_user)):
    """Mark all notifications read in Firestore."""
    from app.services.firebase_service import mark_all_read_firestore
    mark_all_read_firestore(current_user.id)
    return {"message": "All marked read."}


# ── Vibe Check ───────────────────────────────────────────────────────────────

MIN_ENTRIES_FOR_VIBE = 5
DEFAULT_VIBE_CHECKS = 3


def _compute_vibe_score(my_entries: list, their_entries: list) -> int:
    """Compute 0-100 emotional compatibility score."""
    my_flags = set(e.get("emotion_flag") for e in my_entries if e.get("emotion_flag"))
    their_flags = set(e.get("emotion_flag") for e in their_entries if e.get("emotion_flag"))

    if my_flags and their_flags:
        jaccard = len(my_flags & their_flags) / len(my_flags | their_flags)
    else:
        jaccard = 0.5

    my_hues = [MOOD_HUES.get(e.get("emotion_flag", ""), 56) for e in my_entries if e.get("emotion_flag")]
    their_hues = [MOOD_HUES.get(e.get("emotion_flag", ""), 56) for e in their_entries if e.get("emotion_flag")]

    if my_hues and their_hues:
        avg_mine = sum(my_hues) / len(my_hues)
        avg_theirs = sum(their_hues) / len(their_hues)
        diff = abs(avg_mine - avg_theirs)
        if diff > 180:
            diff = 360 - diff
        hue_sim = 1 - (diff / 180)
    else:
        hue_sim = 0.5

    score = int(jaccard * 0.55 * 100 + hue_sim * 0.45 * 100)
    return max(12, min(98, score))


def _vibe_label(score: int) -> tuple[str, str]:
    """Returns (label, description) for a vibe score."""
    if score >= 88:
        return "Twin flames", "Your emotional worlds mirror each other almost perfectly."
    if score >= 72:
        return "Deep resonance", "You share a remarkably similar inner landscape."
    if score >= 56:
        return "Gentle harmony", "Different but beautifully complementary energies."
    if score >= 40:
        return "Intriguing contrast", "Your vibes create a fascinating, creative tension."
    return "Opposite orbits", "Your emotional worlds are wonderfully distinct."


async def _entry_vibe_hue(db, user_id: str) -> int:
    from app.services.encryption_service import decrypt_if_present
    since = datetime.now(timezone.utc) - timedelta(days=60)
    docs = await db["entries"].find(
        {"user_id": user_id, "created_at": {"$gte": since}},
        {"emotion_flag": 1, "mood_summary": 1},
    ).to_list(length=30)
    hues = []
    for d in docs:
        if d.get("emotion_flag") and d["emotion_flag"] in MOOD_HUES:
            hues.append(MOOD_HUES[d["emotion_flag"]])
            continue
        mood = (decrypt_if_present(d.get("mood_summary")) or "").lower()
        for kw, hue in MOOD_HUES.items():
            if kw in mood:
                hues.append(hue)
                break
    return round(sum(hues) / len(hues)) if hues else 42


@router.get("/vibe-check/{user_id}")
async def get_vibe_check_status(
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Return existing vibe check result (if any) and remaining checks."""
    db = get_mongo_db()
    me = current_user.id

    me_doc = await db["users"].find_one({"_id": ObjectId(me)}, {"vibe_checks_remaining": 1})
    remaining = me_doc.get("vibe_checks_remaining", DEFAULT_VIBE_CHECKS) if me_doc else DEFAULT_VIBE_CHECKS

    existing = await db["vibe_checks"].find_one({
        "$or": [{"from_id": me, "to_id": user_id}, {"from_id": user_id, "to_id": me}]
    })

    result = None
    if existing:
        result = {
            "score": existing["score"],
            "label": existing["label"],
            "description": existing["description"],
            "traits_a": existing.get("traits_a", []),
            "traits_b": existing.get("traits_b", []),
            "my_vibe_hue": existing.get("my_vibe_hue", 42),
            "their_vibe_hue": existing.get("their_vibe_hue", 42),
        }

    return {"remaining": remaining, "result": result}


@router.post("/vibe-check/{user_id}")
async def run_vibe_check(
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Run an emotional vibe check between two users. Costs 1 check."""
    db = get_mongo_db()
    me = current_user.id

    if me == user_id:
        raise HTTPException(400, "You cannot vibe-check yourself.")

    me_doc = await db["users"].find_one({"_id": ObjectId(me)}, {"vibe_checks_remaining": 1})
    remaining = me_doc.get("vibe_checks_remaining", DEFAULT_VIBE_CHECKS) if me_doc else DEFAULT_VIBE_CHECKS

    # Return cached result without deducting
    existing = await db["vibe_checks"].find_one({
        "$or": [{"from_id": me, "to_id": user_id}, {"from_id": user_id, "to_id": me}]
    })
    if existing:
        return {
            "score": existing["score"],
            "label": existing["label"],
            "description": existing["description"],
            "traits_a": existing.get("traits_a", []),
            "traits_b": existing.get("traits_b", []),
            "my_vibe_hue": existing.get("my_vibe_hue", 42),
            "their_vibe_hue": existing.get("their_vibe_hue", 42),
            "remaining": remaining,
            "cached": True,
        }

    if remaining <= 0:
        raise HTTPException(403, "You have no vibe checks remaining.")

    import asyncio
    my_count, their_count = await asyncio.gather(
        db["entries"].count_documents({"user_id": me}),
        db["entries"].count_documents({"user_id": user_id}),
    )
    if my_count < MIN_ENTRIES_FOR_VIBE:
        raise HTTPException(400, f"You need at least {MIN_ENTRIES_FOR_VIBE} memories to unlock vibe checks.")
    if their_count < MIN_ENTRIES_FOR_VIBE:
        raise HTTPException(400, f"This person needs at least {MIN_ENTRIES_FOR_VIBE} memories for a vibe check.")

    my_entries, their_entries, my_hue, their_hue = await asyncio.gather(
        db["entries"].find({"user_id": me}, {"emotion_flag": 1, "mood_summary": 1}).to_list(100),
        db["entries"].find({"user_id": user_id}, {"emotion_flag": 1, "mood_summary": 1}).to_list(100),
        _entry_vibe_hue(db, me),
        _entry_vibe_hue(db, user_id),
    )

    # Count emotion flags per user for the AI prompt
    def _flag_counts(entries: list) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in entries:
            flag = e.get("emotion_flag")
            if flag:
                counts[flag] = counts.get(flag, 0) + 1
        return counts

    score = _compute_vibe_score(my_entries, their_entries)
    flag_counts_a = _flag_counts(my_entries)
    flag_counts_b = _flag_counts(their_entries)

    from app.services.llm_service import generate_vibe_reading
    ai_reading = await generate_vibe_reading(flag_counts_a, flag_counts_b, score)

    label = ai_reading.get("label", "Two Quiet Worlds")
    description = ai_reading.get("description", "")
    traits_a = ai_reading.get("traits_a", [])
    traits_b = ai_reading.get("traits_b", [])

    now = datetime.now(timezone.utc)
    await db["vibe_checks"].insert_one({
        "from_id": me,
        "to_id": user_id,
        "score": score,
        "label": label,
        "description": description,
        "traits_a": traits_a,
        "traits_b": traits_b,
        "my_vibe_hue": my_hue,
        "their_vibe_hue": their_hue,
        "created_at": now,
    })
    await db["users"].update_one(
        {"_id": ObjectId(me)},
        {"$inc": {"vibe_checks_remaining": -1}},
        upsert=False,
    )

    return {
        "score": score,
        "label": label,
        "description": description,
        "traits_a": traits_a,
        "traits_b": traits_b,
        "my_vibe_hue": my_hue,
        "their_vibe_hue": their_hue,
        "remaining": remaining - 1,
        "cached": False,
    }


# ── Invite ────────────────────────────────────────────────────────────────────

class InviteRequest(BaseModel):
    email: str


@router.post("/invite")
async def invite_friend(
    req: InviteRequest,
    current_user: UserDB = Depends(get_current_user),
):
    """Check if email is already registered; if not, send invite email."""
    db = get_mongo_db()
    email = req.email.strip().lower()
    existing = await db["users"].find_one({"email": email}, {"_id": 1, "name": 1})
    if existing:
        return {
            "status": "already_registered",
            "name": existing.get("name", ""),
        }
    from app.services.email_service import send_invite_email
    sent = send_invite_email(email, current_user.name)
    return {"status": "invited", "sent": sent}
