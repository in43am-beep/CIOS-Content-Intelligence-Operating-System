"""Request/response schemas for the CIOS API."""
from pydantic import BaseModel, Field


class IdeasRequest(BaseModel):
    niche: str = Field(..., min_length=2, max_length=200)
    audience: str = Field(default="general", max_length=200)
    count: int = Field(default=10, ge=3, le=20)


class ResearchRequest(BaseModel):
    niche: str = Field(..., min_length=2, max_length=200)
    examples: str = Field(default="", max_length=4000)


class ScriptRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=300)
    duration_sec: int = Field(default=60, ge=15, le=1800)
    audience: str = Field(default="general", max_length=200)


class PackagingRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=300)
    audience: str = Field(default="general", max_length=200)


class SeoRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    topic: str = Field(..., min_length=3, max_length=300)


class NicheRequest(BaseModel):
    niche: str = Field(..., min_length=2, max_length=200)


class ApiResult(BaseModel):
    ok: bool = True
    model: str | None = None
    cached: bool = False
    data: dict
