"""Request/response schemas for the CIOS API."""
from pydantic import BaseModel, Field, field_validator


def _stripped(v):
    return v.strip() if isinstance(v, str) else v


class StripModel(BaseModel):
    """Har string field pehle strip hoti hai, taake whitespace-only input
    (audit B-4) min_length bounds se bach na sake."""

    @field_validator("*", mode="before")
    @classmethod
    def strip_strings(cls, v):
        return _stripped(v)


class IdeasRequest(StripModel):
    niche: str = Field(..., min_length=2, max_length=200)
    audience: str = Field(default="general", max_length=200)
    count: int = Field(default=10, ge=3, le=20)


class ResearchRequest(StripModel):
    niche: str = Field(..., min_length=2, max_length=200)
    examples: str = Field(default="", max_length=4000)


class ScriptRequest(StripModel):
    topic: str = Field(..., min_length=3, max_length=300)
    duration_sec: int = Field(default=60, ge=15, le=1800)
    audience: str = Field(default="general", max_length=200)


class PackagingRequest(StripModel):
    topic: str = Field(..., min_length=3, max_length=300)
    audience: str = Field(default="general", max_length=200)


class SeoRequest(StripModel):
    title: str = Field(..., min_length=3, max_length=200)
    topic: str = Field(..., min_length=3, max_length=300)


class NicheRequest(StripModel):
    niche: str = Field(..., min_length=2, max_length=200)


class ApiResult(BaseModel):
    ok: bool = True
    model: str | None = None
    cached: bool = False
    data: dict


class SignupRequest(StripModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=200)  # <8 -> 400 in endpoint


class LoginRequest(StripModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=200)


class QuotaOverrideRequest(BaseModel):
    user_id: int = Field(..., ge=1)
    cap: int = Field(..., ge=1, le=1000)
