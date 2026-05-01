import os
import logging
import tempfile
import httpx
from fastapi import UploadFile, HTTPException
from groq import AsyncGroq

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CF_AUDIO_WORKER_URL = os.getenv("CF_AUDIO_WORKER_URL", "").rstrip("/")
CF_WORKER_SECRET = os.getenv("CF_WORKER_SECRET", "")

_groq_client: AsyncGroq | None = None

def _get_groq() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set")
        _groq_client = AsyncGroq(api_key=GROQ_API_KEY)
    return _groq_client


async def _transcribe_via_cf(audio_bytes: bytes, filename: str) -> str:
    """Try Cloudflare Whisper worker. Raises on any failure."""
    headers = {}
    if CF_WORKER_SECRET:
        headers["X-Worker-Secret"] = CF_WORKER_SECRET

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            CF_AUDIO_WORKER_URL,
            files={"audio": (filename, audio_bytes)},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("text", "")
        if not isinstance(text, str):
            raise ValueError(f"Unexpected CF response: {data}")
        return text


async def _transcribe_via_groq(audio_bytes: bytes, suffix: str) -> str:
    """Transcribe via Groq Whisper."""
    client = _get_groq()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        with open(tmp_path, "rb") as f:
            result = await client.audio.transcriptions.create(
                file=(os.path.basename(tmp_path), f.read()),
                model="whisper-large-v3",
                prompt="This is a personal diary entry. The language might be English, Tamil, Hindi, Malayalam, Telugu, Kannada, or a code-switched mix like Tanglish or Hinglish. Please transcribe carefully without hallucinations.",
                temperature=0.0,
                response_format="json",
            )
        return result.text
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def transcribe_audio(audio_file: UploadFile) -> str:
    """
    Transcribe audio. Uses Cloudflare Whisper worker first (if configured),
    then falls back to Groq Whisper.
    """
    suffix = os.path.splitext(audio_file.filename or "")[1] or ".m4a"
    audio_bytes = await audio_file.read()

    if CF_AUDIO_WORKER_URL:
        try:
            logger.info("Transcribing via Cloudflare Whisper worker…")
            text = await _transcribe_via_cf(audio_bytes, f"audio{suffix}")
            logger.info("CF transcription OK (%d chars)", len(text))
            return text
        except Exception as e:
            logger.warning("CF audio worker failed (%s) — falling back to Groq", e)

    # Groq fallback
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="No transcription service configured.")
    try:
        logger.info("Transcribing via Groq Whisper…")
        text = await _transcribe_via_groq(audio_bytes, suffix)
        logger.info("Groq transcription OK (%d chars)", len(text))
        return text
    except Exception as e:
        logger.error("Groq transcription failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Audio transcription failed: {e}")
