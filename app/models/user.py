from typing import Optional, Any
from pydantic import BaseModel, Field


class PersonalDetails(BaseModel):
    favourites: Optional[str] = None
    hobbies: Optional[str] = None
    close_friends: Optional[str] = None
    music: Optional[str] = None
    sports: Optional[str] = None
    destinations: Optional[str] = None
    extra: Optional[str] = None


class UserBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    email: str
    preferred_language: str = Field(default="auto")
    gender: str = Field(default="prefer_not_to_say")
    birthday: Optional[str] = None          # YYYY-MM-DD
    reminder_enabled: bool = False
    reminder_time: str = "08:00"            # HH:MM, IST
    personal_details: Optional[PersonalDetails] = None
    onboarding_complete: bool = False


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)


class UserDB(UserBase):
    id: str = Field(alias="_id")
    hashed_password: str


class UserResponse(UserBase):
    id: str
    name_native: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[str] = None
