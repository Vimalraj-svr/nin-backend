import asyncio
import logging
import os
import secrets
from datetime import timedelta, date, datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from bson import ObjectId

from app.db import get_mongo_db
from app.models.user import UserCreate, UserResponse, Token, UserDB, PersonalDetails
from app.services.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from app.services.email_service import send_welcome_email, send_password_reset_email
from app.services.llm_service import generate_birthday_wish
from app.services.transliteration_service import name_in_script

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=6)

class UserUpdate(BaseModel):
    name: Optional[str] = None
    preferred_language: Optional[str] = None
    gender: Optional[str] = None
    birthday: Optional[str] = None
    reminder_enabled: Optional[bool] = None
    reminder_time: Optional[str] = None
    personal_details: Optional[PersonalDetails] = None
    onboarding_complete: Optional[bool] = None


def _user_doc_to_response(doc: dict) -> dict:
    from app.models.user import PersonalDetails as PD
    pd_raw = doc.get("personal_details")
    pd = PD(**pd_raw) if pd_raw else None
    lang = doc.get("preferred_language", "auto")
    name = doc.get("name", "")
    return {
        "id": str(doc.get("_id", doc.get("id", ""))),
        "name": name,
        "name_native": name_in_script(name, lang),
        "email": doc.get("email", ""),
        "preferred_language": lang,
        "gender": doc.get("gender", "prefer_not_to_say"),
        "birthday": doc.get("birthday"),
        "reminder_enabled": doc.get("reminder_enabled", False),
        "reminder_time": doc.get("reminder_time", "08:00"),
        "personal_details": pd,
        "onboarding_complete": doc.get("onboarding_complete", False),
    }


@router.post("/register", response_model=UserResponse)
async def register(user: UserCreate):
    db = get_mongo_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    if await db["users"].find_one({"email": user.email}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That email is already registered. Try signing in instead."
        )

    user_dict = user.model_dump()
    user_dict["hashed_password"] = get_password_hash(user_dict.pop("password"))
    result = await db["users"].insert_one(user_dict)

    # Send welcome email without blocking the response
    asyncio.create_task(asyncio.to_thread(
        send_welcome_email, user.name, user.email, user.preferred_language
    ))

    return {**user.model_dump(exclude={"password"}), "id": str(result.inserted_id)}


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    db = get_mongo_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    user_doc = await db["users"].find_one({"email": form_data.username})
    if not user_doc or not verify_password(form_data.password, user_doc["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Hmm, that email or passphrase doesn't match. Want to try again?",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": user_doc["email"]},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserDB = Depends(get_current_user)):
    return _user_doc_to_response(current_user.model_dump(by_alias=True))


@router.get("/birthday-wish")
async def birthday_wish(current_user: UserDB = Depends(get_current_user)):
    """Returns a personalised birthday wish if today is the user's birthday (IST), else null."""
    import pytz
    from datetime import datetime

    if not current_user.birthday:
        return {"wish": None}

    ist = pytz.timezone("Asia/Kolkata")
    today = datetime.now(ist).date()
    try:
        bday = date.fromisoformat(current_user.birthday)
    except ValueError:
        return {"wish": None}

    if today.month != bday.month or today.day != bday.day:
        return {"wish": None}

    wish = await generate_birthday_wish(current_user.name, current_user.preferred_language, gender=current_user.gender)
    return {"wish": wish}


@router.patch("/me", response_model=UserResponse)
async def update_me(
    update: UserUpdate,
    current_user: UserDB = Depends(get_current_user),
):
    db = get_mongo_db()
    raw = update.model_dump(exclude_none=True)
    if "personal_details" in raw and raw["personal_details"] is not None:
        # Store personal_details as plain dict
        raw["personal_details"] = raw["personal_details"]

    if not raw:
        return _user_doc_to_response(current_user.model_dump(by_alias=True))

    await db["users"].update_one({"_id": ObjectId(current_user.id)}, {"$set": raw})
    updated = await db["users"].find_one({"_id": ObjectId(current_user.id)})
    return _user_doc_to_response(updated)


@router.delete("/me")
async def delete_me(current_user: UserDB = Depends(get_current_user)):
    db = get_mongo_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    await db["users"].delete_one({"_id": ObjectId(current_user.id)})
    await db["entries"].delete_many({"user_id": current_user.id})
    return {"message": "Your account and all entries have been permanently deleted."}


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    db = get_mongo_db()
    user_doc = await db["users"].find_one({"email": req.email})
    # Return the same message regardless to prevent email enumeration
    generic = {"message": "If that email is registered, a reset link is on its way."}
    if not user_doc:
        return generic

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await db["users"].update_one(
        {"_id": user_doc["_id"]},
        {"$set": {"reset_token": token, "reset_token_expires": expires_at}},
    )

    frontend_url = os.getenv("FRONTEND_URL", "https://nin-frontend.onrender.com").rstrip("/")
    reset_link = f"{frontend_url}/reset-password?token={token}"
    asyncio.create_task(asyncio.to_thread(
        send_password_reset_email, user_doc["name"], req.email, reset_link
    ))
    return generic


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    db = get_mongo_db()
    now = datetime.now(timezone.utc)
    user_doc = await db["users"].find_one({
        "reset_token": req.token,
        "reset_token_expires": {"$gt": now},
    })
    if not user_doc:
        raise HTTPException(
            status_code=400,
            detail="This link has expired or is invalid. Please request a new one.",
        )

    new_hash = get_password_hash(req.new_password)
    await db["users"].update_one(
        {"_id": user_doc["_id"]},
        {
            "$set": {"hashed_password": new_hash},
            "$unset": {"reset_token": "", "reset_token_expires": ""},
        },
    )
    return {"message": "Your passphrase has been reset. You can now sign in."}
