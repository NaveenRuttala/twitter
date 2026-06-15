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


class OrderStep(BaseModel):
    """One order in an account's per-tweet sequence.
    A value of 0 for service_id/min_qty/max_qty means 'use the account or global default'."""
    service_id: int = 0
    min_qty: int = 0
    max_qty: int = 0
    delay_minutes: int = 0

    @field_validator("service_id", "min_qty", "max_qty", "delay_minutes")
    @classmethod
    def non_negative(cls, v: int) -> int:
        return max(0, int(v))


class PlanUpdateRequest(BaseModel):
    steps: list[OrderStep]
