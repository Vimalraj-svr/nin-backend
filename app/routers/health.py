import os
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "gemini": "configured" if os.getenv("GEMINI_API_KEY") else "missing GEMINI_API_KEY",
        "groq": "configured" if os.getenv("GROQ_API_KEY") else "missing GROQ_API_KEY",
        "storage": "mongodb",
    }
