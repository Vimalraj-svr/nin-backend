import uuid
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field


SUPPORTED_LANGUAGES = {
    'en': 'English',
    'ta': 'Tamil',
    'hi': 'Hindi',
    'ml': 'Malayalam',
    'te': 'Telugu',
    'kn': 'Kannada',
    'bilingual': 'Bilingual',
    'auto': 'Auto-detect',
}

OUTPUT_MODE_FOR_LANG = {
    'en':        'ENGLISH_REFINED',
    'bilingual': 'BILINGUAL',
    'auto':      'SAME_LANGUAGE',
    # all other ISO codes → SAME_LANGUAGE (prompt forces the target language)
}

def output_mode_for(preferred_language: str) -> str:
    return OUTPUT_MODE_FOR_LANG.get(preferred_language, 'SAME_LANGUAGE')


class GenerateRequest(BaseModel):
    transcript: str
    language_override: Optional[str] = None  # one-time override, ignored if None
    entry_date: Optional[str] = None         # ISO date "YYYY-MM-DD"; defaults to today IST


EMOTION_FLAGS = [
    "love", "happy", "sad", "anxious", "grateful",
    "angry", "nostalgic", "hopeful", "confused", "peaceful",
]


class ImageAsset(BaseModel):
    public_id: str
    url: str
    width: Optional[int] = None
    height: Optional[int] = None


class EntryComment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EntryDocument(BaseModel):
    """Full MongoDB document shape."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    transcript: str
    detected_language: Optional[str] = None
    preferred_language: str = 'auto'
    output_mode: str = 'SAME_LANGUAGE'
    title_original: Optional[str] = None
    content_original: Optional[str] = None
    title_english: Optional[str] = None
    content_english: Optional[str] = None
    mood_summary: Optional[str] = None
    embedding: Optional[list] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # rich editor fields
    content_edit: Optional[str] = None       # user-edited override of content_original
    emojis: List[str] = Field(default_factory=list)
    images: List[ImageAsset] = Field(default_factory=list)
    comments: List[EntryComment] = Field(default_factory=list)
    is_hidden: bool = False
    emotion_flag: Optional[str] = None       # one of EMOTION_FLAGS
    shared_with: List[str] = Field(default_factory=list)  # user IDs this entry is shared with


class EntryResponse(BaseModel):
    id: str
    transcript: str
    detected_language: Optional[str]
    preferred_language: str
    output_mode: str
    title_original: Optional[str]
    content_original: Optional[str]
    title_english: Optional[str]
    content_english: Optional[str]
    mood_summary: Optional[str]
    created_at: str
    content_edit: Optional[str] = None
    title_edit: Optional[str] = None
    emojis: List[str] = Field(default_factory=list)
    images: List[dict] = Field(default_factory=list)
    comments: List[dict] = Field(default_factory=list)
    is_hidden: bool = False
    emotion_flag: Optional[str] = None
    shared_with: List[str] = Field(default_factory=list)
    viewer_is_owner: bool = True
