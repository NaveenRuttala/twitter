from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


@dataclass
class Tweet:
    """Normalised tweet shape every provider returns."""
    id: str                       # tweet id as string (these are huge ints)
    text: str
    url: str
    created_at: Optional[str] = None
    is_retweet: bool = False
    is_reply: bool = False


class AddAccountRequest(BaseModel):
    username: str
    # optional per-account overrides; fall back to global settings if None
    min_qty: Optional[int] = None
    max_qty: Optional[int] = None
    service_id: Optional[int] = None
    enabled: bool = True

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        return v.strip().lstrip("@").lower()


class UpdateAccountRequest(BaseModel):
    enabled: Optional[bool] = None
    min_qty: Optional[int] = None
    max_qty: Optional[int] = None
    service_id: Optional[int] = None
