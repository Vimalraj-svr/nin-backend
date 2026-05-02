import os
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "ai": "configured" if os.getenv("GEMINI_API_KEY") else "unavailable",
        "voice": "configured" if os.getenv("GROQ_API_KEY") else "unavailable",
        "storage": "mongodb",
    }
