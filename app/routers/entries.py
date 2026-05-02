import logging
from datetime import datetime, timedelta, timezone

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form

logger = logging.getLogger(__name__)
from pydantic import BaseModel

from app.db import get_mongo_db
from app.models.entry import GenerateRequest, EntryResponse, EMOTION_FLAGS
from app.models.user import UserDB
from app.services.auth import get_current_user
from app.services.encryption_service import decrypt_if_present
from app.services.groq_service import transcribe_audio
from app.services.llm_service import generate_weekly_letter, generate_memory_threads, answer_from_entries
from app.services.pipeline import run_pipeline, has_entry_for_date, get_available_dates
from datetime import date as date_type

IST = pytz.timezone("Asia/Kolkata")

router = APIRouter(prefix="/api/entries", tags=["entries"])


def _serialize(doc: dict) -> dict:
    created = doc.get("created_at")
    if hasattr(created, "isoformat"):
        created_str = created.isoformat()
    else:
        created_str = str(created)

    # Normalize comments: convert created_at to isoformat
    comments = []
    for c in (doc.get("comments") or []):
        c2 = dict(c)
        if hasattr(c2.get("created_at"), "isoformat"):
            c2["created_at"] = c2["created_at"].isoformat()
        comments.append(c2)

    shared_comments = []
    for c in (doc.get("shared_comments") or []):
        c2 = dict(c)
        if hasattr(c2.get("created_at"), "isoformat"):
            c2["created_at"] = c2["created_at"].isoformat()
        shared_comments.append(c2)

    shared_reactions = []
    for r in (doc.get("shared_reactions") or []):
        r2 = dict(r)
        if hasattr(r2.get("created_at"), "isoformat"):
            r2["created_at"] = r2["created_at"].isoformat()
        shared_reactions.append(r2)

    return {
        "id": doc.get("id", str(doc.get("_id", ""))),
        "transcript": decrypt_if_present(doc.get("transcript", "")),
        "detected_language": doc.get("detected_language"),
        "preferred_language": doc.get("preferred_language", "auto"),
        "output_mode": doc.get("output_mode", "SAME_LANGUAGE"),
        "title_original": decrypt_if_present(doc.get("title_original")),
        "content_original": decrypt_if_present(doc.get("content_original")),
        "title_english": decrypt_if_present(doc.get("title_english")),
        "content_english": decrypt_if_present(doc.get("content_english")),
        "mood_summary": decrypt_if_present(doc.get("mood_summary")),
        "created_at": created_str,
        "content_edit": decrypt_if_present(doc.get("content_edit")),
        "title_edit": decrypt_if_present(doc.get("title_edit")),
        "emojis": doc.get("emojis") or [],
        "images": doc.get("images") or [],
        "comments": comments,
        "is_hidden": doc.get("is_hidden", False),
        "emotion_flag": doc.get("emotion_flag"),
        "shared_with": doc.get("shared_with") or [],
        "viewer_is_owner": True,
        "shared_comments": shared_comments,
        "shared_reactions": shared_reactions,
    }


def _already_written_error(d: str):
    raise HTTPException(
        status_code=429,
        detail=f"You've already written an entry for {d}. Choose a different day."
    )


@router.get("/available-dates")
async def available_dates(current_user: UserDB = Depends(get_current_user)):
    """Returns ISO date strings for the last 3 days that have no entry yet."""
    db = get_mongo_db()
    dates = await get_available_dates(db, current_user.id, days=3)
    return {"dates": dates}


@router.post("/generate", response_model=EntryResponse)
async def generate_entry(
    req: GenerateRequest,
    current_user: UserDB = Depends(get_current_user),
):
    db = get_mongo_db()
    target = date_type.fromisoformat(req.entry_date) if req.entry_date else None
    if target is None:
        from datetime import datetime as _dt
        from app.services.pipeline import IST as _IST
        target = _dt.now(_IST).date()

    if await has_entry_for_date(db, current_user.id, target):
        _already_written_error(target.isoformat())

    # Minimum transcript length guard
    transcript_clean = (req.transcript or "").strip()
    word_count = len(transcript_clean.split())
    if len(transcript_clean) < 30 or word_count < 5:
        raise HTTPException(
            status_code=422,
            detail="Your entry is a little short for me to work with. Write at least a few sentences so your day comes through clearly."
        )

    preferred = req.language_override or current_user.preferred_language or "auto"
    entry = await run_pipeline(
        transcript=req.transcript,
        preferred_language=preferred,
        user_id=current_user.id,
        db=db,
        language_override=req.language_override,
        entry_date=req.entry_date,
    )
    return _serialize(entry)


@router.post("/voice-generate", response_model=EntryResponse)
async def voice_generate(
    audio: UploadFile = File(...),
    language_override: str = Form(default=""),
    entry_date: str = Form(default=""),
    current_user: UserDB = Depends(get_current_user),
):
    db = get_mongo_db()
    entry_date_clean = entry_date.strip() or None

    target = date_type.fromisoformat(entry_date_clean) if entry_date_clean else None
    if target is None:
        from datetime import datetime as _dt
        from app.services.pipeline import IST as _IST
        target = _dt.now(_IST).date()

    if await has_entry_for_date(db, current_user.id, target):
        _already_written_error(target.isoformat())

    transcript = await transcribe_audio(audio)
    logger.info("RAW TRANSCRIPT from voice [user=%s]: %r", current_user.id, transcript)

    if not transcript or not transcript.strip():
        raise HTTPException(
            status_code=422,
            detail="I couldn't make out any words in that recording. Try speaking a little closer to the mic, or type your entry instead."
        )

    # Minimum transcript length guard
    word_count = len(transcript.strip().split())
    if len(transcript.strip()) < 30 or word_count < 5:
        raise HTTPException(
            status_code=422,
            detail="That recording was a little too brief. Speak for a bit longer — a few sentences help capture your day more clearly."
        )

    preferred = language_override.strip() or current_user.preferred_language or "auto"
    entry = await run_pipeline(
        transcript=transcript,
        preferred_language=preferred,
        user_id=current_user.id,
        db=db,
        language_override=language_override.strip() or None,
        entry_date=entry_date_clean,
    )
    return _serialize(entry)


@router.get("/", response_model=list[EntryResponse])
async def list_entries(
    skip: int = 0,
    limit: int = 30,
    current_user: UserDB = Depends(get_current_user),
):
    db = get_mongo_db()
    cursor = db["entries"].find(
        {"user_id": current_user.id},
        {"embedding": 0},
    ).sort("created_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [_serialize(d) for d in docs]


@router.get("/streak")
async def get_streak(current_user: UserDB = Depends(get_current_user)):
    """Returns current writing streak, total entries, and milestone info."""
    db = get_mongo_db()
    total = await db["entries"].count_documents({"user_id": current_user.id})

    cursor = db["entries"].find(
        {"user_id": current_user.id},
        {"created_at": 1},
    ).sort("created_at", -1)
    docs = await cursor.to_list(length=None)

    written_dates: set[str] = set()
    for doc in docs:
        created = doc.get("created_at")
        if created:
            ist_date = created.astimezone(IST).strftime("%Y-%m-%d")
            written_dates.add(ist_date)

    streak = 0
    check = datetime.now(IST).date()
    while check.isoformat() in written_dates:
        streak += 1
        check = check - timedelta(days=1)

    MILESTONES = [1, 7, 14, 30, 50, 100, 200, 365]
    next_milestone = next((m for m in MILESTONES if m > total), None)
    last_milestone = max((m for m in MILESTONES if m <= total), default=None)

    return {
        "streak": streak,
        "total_entries": total,
        "last_milestone": last_milestone,
        "next_milestone": next_milestone,
        "entries_to_next": (next_milestone - total) if next_milestone else None,
    }


@router.get("/memory-threads")
async def memory_threads(current_user: UserDB = Depends(get_current_user)):
    db = get_mongo_db()
    since = datetime.now(timezone.utc) - timedelta(days=30)
    cursor = db["entries"].find(
        {"user_id": current_user.id, "created_at": {"$gte": since}},
        {"embedding": 0},
    ).sort("created_at", 1)
    docs = await cursor.to_list(length=None)

    if len(docs) < 3:
        return {"threads": [], "entry_count": len(docs), "message": "Write a few more entries and I'll start noticing patterns."}

    from app.services.encryption_service import decrypt_if_present as dec
    lines = []
    for doc in docs:
        content = dec(doc.get("content_english") or doc.get("content_original")) or ""
        lines.append(content[:300])

    threads = await generate_memory_threads("\n\n---\n\n".join(lines), current_user.name, gender=current_user.gender)
    return {"threads": threads, "entry_count": len(docs)}


@router.get("/weekly-letter")
async def weekly_letter(current_user: UserDB = Depends(get_current_user)):
    db = get_mongo_db()
    since = datetime.now(timezone.utc) - timedelta(days=7)
    cursor = db["entries"].find(
        {"user_id": current_user.id, "created_at": {"$gte": since}},
        {"embedding": 0},
    ).sort("created_at", 1)
    docs = await cursor.to_list(length=None)

    if len(docs) < 3:
        return {"letter": None, "entry_count": len(docs), "message": f"Write at least 3 entries this week — you've written {len(docs)} so far. Come back when you have a few more days to reflect on."}

    from app.services.encryption_service import decrypt_if_present as dec
    lines = []
    for doc in docs:
        date_str = doc["created_at"].strftime("%A, %d %b") if hasattr(doc["created_at"], "strftime") else str(doc["created_at"])[:10]
        title = dec(doc.get("title_english") or doc.get("title_original")) or "Untitled"
        mood = dec(doc.get("mood_summary")) or ""
        snippet = dec(doc.get("content_english") or doc.get("content_original")) or ""
        snippet = snippet[:200].strip()
        lines.append(f"[{date_str}] {title} (mood: {mood}) — {snippet}…")

    letter = await generate_weekly_letter(
        current_user.name, "\n".join(lines), current_user.preferred_language, gender=current_user.gender
    )
    return {"letter": letter, "entry_count": len(docs)}


@router.get("/shared-with-me")
async def shared_with_me(
    current_user: UserDB = Depends(get_current_user),
    from_user_id: str = Query(None),
):
    """Entries that other users have shared with the current user."""
    db = get_mongo_db()
    query: dict = {"shared_with": current_user.id}
    if from_user_id:
        query["user_id"] = from_user_id
    cursor = db["entries"].find(
        query,
        {"embedding": 0},
    ).sort("created_at", -1).limit(50)
    docs = await cursor.to_list(length=50)
    if not docs:
        return []

    from bson import ObjectId
    owner_ids = list({d["user_id"] for d in docs})
    try:
        owner_docs = await db["users"].find(
            {"_id": {"$in": [ObjectId(oid) for oid in owner_ids]}},
            {"_id": 1, "name": 1},
        ).to_list(length=None)
        owner_names = {str(u["_id"]): u["name"] for u in owner_docs}
    except Exception:
        owner_names = {}

    result = []
    for doc in docs:
        s = _serialize(doc)
        s["viewer_is_owner"] = False
        s["shared_by_name"] = owner_names.get(doc["user_id"], "Someone")
        result.append(s)
    return result


@router.get("/shared-by-me-with/{user_id}")
async def shared_by_me_with(
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Entries the current user has shared with a specific user."""
    db = get_mongo_db()
    cursor = db["entries"].find(
        {"user_id": current_user.id, "shared_with": user_id},
        {"embedding": 0},
    ).sort("created_at", -1).limit(50)
    docs = await cursor.to_list(length=50)
    result = []
    for doc in docs:
        s = _serialize(doc)
        s["viewer_is_owner"] = True
        result.append(s)
    return result


@router.get("/{entry_id}", response_model=EntryResponse)
async def get_entry(
    entry_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    from bson import ObjectId
    db = get_mongo_db()
    doc = await db["entries"].find_one(
        {
            "id": entry_id,
            "$or": [
                {"user_id": current_user.id},
                {"shared_with": current_user.id},
            ],
        },
        {"embedding": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")
    s = _serialize(doc)
    is_owner = (doc["user_id"] == current_user.id)
    s["viewer_is_owner"] = is_owner

    if not is_owner:
        viewer_id = current_user.id
        # Shared viewers see only their own shared items
        s["shared_comments"] = [c for c in s["shared_comments"] if c.get("user_id") == viewer_id]
        s["shared_reactions"] = [r for r in s["shared_reactions"] if r.get("user_id") == viewer_id]
        # Include owner name for attribution in UI
        try:
            owner_doc = await db["users"].find_one(
                {"_id": ObjectId(doc["user_id"])}, {"name": 1}
            )
            s["shared_by_name"] = owner_doc.get("name", "Someone") if owner_doc else "Someone"
        except Exception:
            s["shared_by_name"] = "Someone"

    return s


class ChatRequest(BaseModel):
    question: str

@router.post("/ask")
async def ask_past_self(
    req: ChatRequest,
    current_user: UserDB = Depends(get_current_user),
):
    """
    Answers a question using the user's actual diary entries as context.
    Uses recent entries (by date) + semantically similar entries, with dates shown.
    """
    db = get_mongo_db()
    from app.services.embedding_service import generate_embedding
    from app.services.memory_service import retrieve_similar, get_recent_entries_for_chat

    # Require at least 3 entries before answering
    total = await db["entries"].count_documents({"user_id": current_user.id})
    if total < 3:
        remaining = 3 - total
        return {"answer": f"Your diary needs a little more to work with. Write {remaining} more {'entry' if remaining == 1 else 'entries'} and I'll be able to reflect your past back to you."}

    # 1. Fetch recent entries (primary source — answers time-based questions correctly)
    recent = await get_recent_entries_for_chat(db, current_user.id, limit=15)

    if not recent:
        return {"answer": "You haven't written any entries yet. Start writing and I'll be able to answer from your diary."}

    # 2. Build context block with date labels so LLM can answer "this week" etc.
    context_parts = []
    for e in recent:
        body = e["content"][:600].replace("\n", " ").strip()
        context_parts.append(
            f"[{e['date']}] {e['title']}\n{body}"
        )
    context = "\n\n---\n\n".join(context_parts)

    answer = await answer_from_entries(
        req.question, context, current_user.name, current_user.preferred_language, gender=current_user.gender
    )
    return {"answer": answer}


@router.get("/on-this-day/all", response_model=list[EntryResponse])
async def on_this_day(current_user: UserDB = Depends(get_current_user)):
    """
    Returns one entry per past year for today's month+day (IST).
    E.g. if today is June 5 2025, returns entries from June 5 2024, 2023, 2022…
    """
    db = get_mongo_db()
    today_ist = datetime.now(IST).date()
    month, day = today_ist.month, today_ist.day

    # Search up to 5 years back
    year_ranges = []
    for years_back in range(1, 6):
        try:
            past = today_ist.replace(year=today_ist.year - years_back)
        except ValueError:
            continue  # Feb 29 on non-leap year
        start_ist = IST.localize(datetime(past.year, past.month, past.day, 0, 0, 0))
        end_ist = start_ist + timedelta(days=1)
        year_ranges.append((start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)))

    docs = []
    for start_utc, end_utc in year_ranges:
        doc = await db["entries"].find_one(
            {
                "user_id": current_user.id,
                "created_at": {"$gte": start_utc, "$lt": end_utc},
            },
            {"embedding": 0},
            sort=[("created_at", -1)],
        )
        if doc:
            docs.append(_serialize(doc))

    return docs


@router.get("/export/pdf")
async def export_pdf(current_user: UserDB = Depends(get_current_user)):
    """Exports all entries as a formatted PDF journal."""
    import os
    from io import BytesIO
    from datetime import date as _date
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Prefer fonts with good Indic script support (Noto > Arial Unicode > fallback)
    FONT_CANDIDATES = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",      # Ubuntu/Debian
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",                # some Linux distros
        "/usr/share/fonts/truetype/noto/NotoSerifTamil-Regular.ttf", # Tamil-specific Noto
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",      # macOS 14+
        "/Library/Fonts/Arial Unicode.ttf",                          # macOS older
        "/Library/Fonts/Noto/NotoSans-Regular.ttf",
    ]
    body_font = "Helvetica"
    for _fp in FONT_CANDIDATES:
        if os.path.exists(_fp):
            try:
                pdfmetrics.registerFont(TTFont("UniFont", _fp))
                body_font = "UniFont"
                break
            except Exception:
                continue
    has_unicode_font = body_font == "UniFont"

    db = get_mongo_db()
    cursor = db["entries"].find(
        {"user_id": current_user.id},
        {"embedding": 0},
    ).sort("created_at", 1)
    docs = await cursor.to_list(length=None)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=3*cm, rightMargin=3*cm,
        topMargin=3*cm, bottomMargin=3*cm,
    )

    styles = getSampleStyleSheet()
    ink = colors.HexColor("#1a1512")
    gold = colors.HexColor("#b4854a")
    muted = colors.HexColor("#8a7e72")
    cream = colors.HexColor("#f4ede0")
    page_w, page_h = A4

    def draw_background(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(cream)
        canvas.rect(0, 0, page_w, page_h, fill=1, stroke=0)
        canvas.restoreState()

    title_style = ParagraphStyle("BookTitle", parent=styles["Title"],
        fontName=body_font,
        fontSize=28, textColor=ink, spaceAfter=6, alignment=TA_CENTER, leading=34)
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"],
        fontName=body_font,
        fontSize=11, textColor=muted, alignment=TA_CENTER, spaceAfter=4)
    date_style = ParagraphStyle("Date", parent=styles["Normal"],
        fontName=body_font,
        fontSize=9, textColor=gold, spaceBefore=24, spaceAfter=6)
    entry_title_style = ParagraphStyle("EntryTitle", parent=styles["Heading2"],
        fontName=body_font,
        fontSize=16, textColor=ink, spaceAfter=8, leading=22)
    body_style = ParagraphStyle("Body", parent=styles["Normal"],
        fontName=body_font,
        fontSize=11, textColor=ink, leading=18, spaceAfter=6)
    mood_style = ParagraphStyle("Mood", parent=styles["Normal"],
        fontName=body_font,
        fontSize=9, textColor=muted, spaceAfter=10)

    LANG_NATIVE = {
        "ta": "நினைவுகள்",
        "hi": "यादें",
        "te": "జ్ఞాపకాలు",
        "kn": "ನೆನಪುಗಳು",
        "ml": "ഓർമ്മകൾ",
        "en": "Memories",
    }
    lang_code = (current_user.preferred_language or "en").lower()
    lang_subtitle = LANG_NATIVE.get(lang_code, "Memories")

    notice_style = ParagraphStyle("Notice", parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8, textColor=muted, spaceAfter=8, leading=12,
        borderPad=6, borderColor=gold, borderWidth=0.3,
        backColor=colors.HexColor("#faf5ea"))

    story = []

    # Cover
    story.append(Spacer(1, 2*cm))
    story.append(Paragraph("Ninaivugal", title_style))
    story.append(Paragraph("Memories" if not has_unicode_font else lang_subtitle, sub_style))
    story.append(Paragraph(f"{current_user.name}'s diary", sub_style))
    story.append(Paragraph(f"Exported {_date.today().strftime('%d %B %Y')}", sub_style))
    story.append(Spacer(1, 1*cm))
    story.append(HRFlowable(width="100%", color=gold, thickness=0.5))
    if not has_unicode_font:
        story.append(Spacer(1, 0.5*cm))
        story.append(Paragraph(
            "Note: Entries written in regional scripts (Tamil, Hindi, etc.) are shown in English where available. "
            "Full regional language PDF export is coming soon.",
            notice_style
        ))
    story.append(Spacer(1, 2*cm))

    for doc_entry in docs:
        created = doc_entry.get("created_at")
        date_str = created.strftime("%A, %d %B %Y · %H:%M") if hasattr(created, "strftime") else str(created)[:16]

        dec = decrypt_if_present
        title_en = dec(doc_entry.get("title_english"))
        title_orig = dec(doc_entry.get("title_original"))
        title_edit = doc_entry.get("title_edit")
        content_en = dec(doc_entry.get("content_english"))
        content_orig = dec(doc_entry.get("content_original"))
        content_edit = dec(doc_entry.get("content_edit"))
        mood = dec(doc_entry.get("mood_summary")) or ""

        # When no proper unicode font, prefer English to avoid broken glyphs
        if has_unicode_font:
            title = title_edit or title_en or title_orig or "Untitled"
            content = content_edit or content_en or content_orig or ""
            regional_only = False
        else:
            title = title_edit or title_en or "Untitled"
            content = content_edit or content_en or ""
            regional_only = not content and bool(content_orig)
            if regional_only:
                title = title_en or title_orig or "Untitled"

        story.append(Paragraph(date_str, date_style))
        story.append(Paragraph(title, entry_title_style))
        if mood:
            story.append(Paragraph(mood, mood_style))
        if regional_only:
            story.append(Paragraph(
                "This entry was written in a regional language. "
                "English rendering is coming soon.",
                notice_style
            ))
        else:
            for para in content.split("\n\n"):
                para = para.strip()
                if para:
                    story.append(Paragraph(para.replace("\n", " "), body_style))
        story.append(HRFlowable(width="60%", color=gold, thickness=0.5, spaceAfter=6))

    doc.build(story, onFirstPage=draw_background, onLaterPages=draw_background)
    buf.seek(0)
    filename = f"ninaivugal_{current_user.name.lower().replace(' ', '_')}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/{entry_id}/share")
async def create_share_link(
    entry_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Creates a time-limited (48h) read-only share token for a single entry."""
    import secrets as _secrets
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "user_id": current_user.id})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")

    token = _secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(hours=48)
    await db["shares"].insert_one({
        "token": token,
        "entry_id": entry_id,
        "user_id": current_user.id,
        "expires_at": expires,
    })
    return {"token": token, "expires_at": expires.isoformat()}


@router.get("/shared/{token}")
async def get_shared_entry(token: str):
    """Public endpoint — returns a shared entry if the token is valid and not expired."""
    db = get_mongo_db()
    share = await db["shares"].find_one({"token": token})
    if not share:
        raise HTTPException(status_code=404, detail="This link doesn't exist or has expired.")

    if datetime.now(timezone.utc) > share["expires_at"].replace(tzinfo=timezone.utc):
        await db["shares"].delete_one({"token": token})
        raise HTTPException(status_code=410, detail="This link has expired.")

    doc = await db["entries"].find_one(
        {"id": share["entry_id"]},
        {"embedding": 0, "user_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return _serialize(doc)


class ShareWithRequest(BaseModel):
    user_id: str


@router.post("/{entry_id}/share-with")
async def add_share_with(
    entry_id: str,
    req: ShareWithRequest,
    current_user: UserDB = Depends(get_current_user),
):
    """Share an entry with a specific user by user_id."""
    from bson import ObjectId
    db = get_mongo_db()

    doc = await db["entries"].find_one({"id": entry_id, "user_id": current_user.id})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")

    try:
        target = await db["users"].find_one(
            {"_id": ObjectId(req.user_id)}, {"_id": 1, "name": 1}
        )
    except Exception:
        raise HTTPException(status_code=404, detail="User not found.")
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    # Respect the target's restriction of the current user
    blocked = await db["restrictions"].find_one(
        {"user_id": req.user_id, "target_id": current_user.id}
    )
    if blocked:
        raise HTTPException(status_code=403, detail="You cannot share with this user.")

    already_shared = req.user_id in (doc.get("shared_with") or [])
    if not already_shared:
        import uuid as _uuid
        now = datetime.now(timezone.utc)
        await db["entries"].update_one(
            {"id": entry_id},
            {"$push": {"shared_with": req.user_id}},
        )
        notif = {
            "id": str(_uuid.uuid4()),
            "user_id": req.user_id,
            "type": "shared_memory",
            "actor_id": current_user.id,
            "actor_name": current_user.name,
            "entry_id": entry_id,
            "read": False,
            "created_at": now,
        }
        from app.services.firebase_service import write_notification
        write_notification(req.user_id, notif)
    return {"message": "Shared.", "shared_with_name": target.get("name", "")}


@router.delete("/{entry_id}/share-with/{user_id}")
async def remove_share_with(
    entry_id: str,
    user_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Remove a user from an entry's shared_with list."""
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "user_id": current_user.id})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")
    await db["entries"].update_one(
        {"id": entry_id},
        {"$pull": {"shared_with": user_id}},
    )
    return {"message": "Removed from shared."}


class PatchEntryRequest(BaseModel):
    content_edit: str | None = None
    title_edit: str | None = None
    emojis: list[str] | None = None
    emotion_flag: str | None = None
    is_hidden: bool | None = None


@router.patch("/{entry_id}", response_model=EntryResponse)
async def patch_entry(
    entry_id: str,
    req: PatchEntryRequest,
    current_user: UserDB = Depends(get_current_user),
):
    """Edit content, emojis, emotion flag, or hide status of an entry."""
    from app.services.encryption_service import encrypt_if_present
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "user_id": current_user.id})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")

    updates: dict = {}
    if req.content_edit is not None:
        updates["content_edit"] = encrypt_if_present(req.content_edit)
    if req.title_edit is not None:
        updates["title_edit"] = encrypt_if_present(req.title_edit)
    if req.emojis is not None:
        updates["emojis"] = req.emojis
    if req.emotion_flag is not None:
        if req.emotion_flag not in EMOTION_FLAGS:
            raise HTTPException(status_code=422, detail=f"emotion_flag must be one of: {', '.join(EMOTION_FLAGS)}")
        updates["emotion_flag"] = req.emotion_flag
    if req.is_hidden is not None:
        updates["is_hidden"] = req.is_hidden

    if updates:
        await db["entries"].update_one({"id": entry_id}, {"$set": updates})

    updated = await db["entries"].find_one({"id": entry_id}, {"embedding": 0})
    return _serialize(updated)


@router.post("/{entry_id}/images")
async def upload_entry_image(
    entry_id: str,
    image: UploadFile = File(...),
    current_user: UserDB = Depends(get_current_user),
):
    """Upload an image to Cloudinary and attach it to an entry."""
    from app.services.cloudinary_service import upload_image
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "user_id": current_user.id})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")

    file_bytes = await image.read()
    asset = await upload_image(file_bytes, folder=f"ninaivugal/{current_user.id}")

    await db["entries"].update_one(
        {"id": entry_id},
        {"$push": {"images": asset}},
    )
    return asset


@router.delete("/{entry_id}/images/{public_id:path}")
async def remove_entry_image(
    entry_id: str,
    public_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Remove an image from Cloudinary and detach from entry."""
    from app.services.cloudinary_service import delete_image
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "user_id": current_user.id})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")

    await delete_image(public_id)
    await db["entries"].update_one(
        {"id": entry_id},
        {"$pull": {"images": {"public_id": public_id}}},
    )
    return {"message": "Image removed.", "public_id": public_id}


class CommentRequest(BaseModel):
    text: str


@router.post("/{entry_id}/comments")
async def add_comment(
    entry_id: str,
    req: CommentRequest,
    current_user: UserDB = Depends(get_current_user),
):
    """Add a comment to an entry."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "user_id": current_user.id})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")

    comment = {
        "id": str(_uuid.uuid4()),
        "text": req.text,
        "created_at": _dt.now(_tz.utc),
    }
    await db["entries"].update_one(
        {"id": entry_id},
        {"$push": {"comments": comment}},
    )
    comment["created_at"] = comment["created_at"].isoformat()
    return comment


@router.delete("/{entry_id}/comments/{comment_id}")
async def delete_comment(
    entry_id: str,
    comment_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Remove a comment from an entry."""
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "user_id": current_user.id})
    if not doc:
        raise HTTPException(status_code=404, detail="Entry not found.")

    await db["entries"].update_one(
        {"id": entry_id},
        {"$pull": {"comments": {"id": comment_id}}},
    )
    return {"message": "Comment removed.", "id": comment_id}


class SharedCommentRequest(BaseModel):
    text: str


@router.post("/{entry_id}/shared-comments")
async def add_shared_comment(
    entry_id: str,
    req: SharedCommentRequest,
    current_user: UserDB = Depends(get_current_user),
):
    """A shared viewer adds a comment on an entry shared with them."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "shared_with": current_user.id})
    if not doc:
        raise HTTPException(status_code=403, detail="Not authorised.")
    comment = {
        "id": str(_uuid.uuid4()),
        "user_id": current_user.id,
        "user_name": current_user.name,
        "text": req.text,
        "created_at": _dt.now(_tz.utc),
    }
    await db["entries"].update_one({"id": entry_id}, {"$push": {"shared_comments": comment}})
    comment["created_at"] = comment["created_at"].isoformat()
    return comment


@router.delete("/{entry_id}/shared-comments/{comment_id}")
async def delete_shared_comment(
    entry_id: str,
    comment_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Remove a shared comment (own comment, or owner can remove any)."""
    db = get_mongo_db()
    doc = await db["entries"].find_one({
        "id": entry_id,
        "$or": [{"user_id": current_user.id}, {"shared_with": current_user.id}],
    })
    if not doc:
        raise HTTPException(status_code=403, detail="Not authorised.")
    is_owner = doc["user_id"] == current_user.id
    pull_filter = {"id": comment_id} if is_owner else {"id": comment_id, "user_id": current_user.id}
    await db["entries"].update_one({"id": entry_id}, {"$pull": {"shared_comments": pull_filter}})
    return {"message": "Comment removed.", "id": comment_id}


class SharedReactionRequest(BaseModel):
    emoji: str


@router.post("/{entry_id}/shared-reactions")
async def toggle_shared_reaction(
    entry_id: str,
    req: SharedReactionRequest,
    current_user: UserDB = Depends(get_current_user),
):
    """Toggle a shared reaction emoji (adds if not present, removes if already added)."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    db = get_mongo_db()
    doc = await db["entries"].find_one({"id": entry_id, "shared_with": current_user.id})
    if not doc:
        raise HTTPException(status_code=403, detail="Not authorised.")
    existing = next(
        (r for r in (doc.get("shared_reactions") or [])
         if r.get("user_id") == current_user.id and r.get("emoji") == req.emoji),
        None,
    )
    if existing:
        await db["entries"].update_one(
            {"id": entry_id}, {"$pull": {"shared_reactions": {"id": existing["id"]}}}
        )
        return {"toggled": "off", "id": existing["id"]}
    reaction = {
        "id": str(_uuid.uuid4()),
        "user_id": current_user.id,
        "user_name": current_user.name,
        "emoji": req.emoji,
        "created_at": _dt.now(_tz.utc),
    }
    await db["entries"].update_one({"id": entry_id}, {"$push": {"shared_reactions": reaction}})
    reaction["created_at"] = reaction["created_at"].isoformat()
    return {"toggled": "on", **reaction}


@router.delete("/{entry_id}/shared-reactions/{reaction_id}")
async def delete_shared_reaction(
    entry_id: str,
    reaction_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    """Remove a shared reaction (own, or owner can remove any)."""
    db = get_mongo_db()
    doc = await db["entries"].find_one({
        "id": entry_id,
        "$or": [{"user_id": current_user.id}, {"shared_with": current_user.id}],
    })
    if not doc:
        raise HTTPException(status_code=403, detail="Not authorised.")
    is_owner = doc["user_id"] == current_user.id
    pull_filter = {"id": reaction_id} if is_owner else {"id": reaction_id, "user_id": current_user.id}
    await db["entries"].update_one({"id": entry_id}, {"$pull": {"shared_reactions": pull_filter}})
    return {"message": "Reaction removed.", "id": reaction_id}


@router.delete("/{entry_id}")
async def delete_entry(
    entry_id: str,
    current_user: UserDB = Depends(get_current_user),
):
    db = get_mongo_db()
    result = await db["entries"].delete_one(
        {"id": entry_id, "user_id": current_user.id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Entry not found.")
    return {"message": "Entry deleted.", "id": entry_id}
