"""Central settings. Everything runs at $0: only OpenRouter :free models are used."""
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    openrouter_api_key: str = ""
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    db_path: str = str(BASE_DIR / "cios.db")

    # Auth (B-1). AUTH_REQUIRED=true (default) = JWT on all AI endpoints.
    # Explicit opt-out for single-user local dev only: AUTH_REQUIRED=false.
    auth_required: bool = True
    jwt_secret: str = ""
    jwt_expire_days: int = 7

    # OpenRouter free tier: 20 req/min always; 50 req/day on unfunded accounts.
    # We keep a safety buffer so the app never hard-hits the daily wall.
    daily_request_cap: int = 48
    min_seconds_between_calls: float = 3.5
    request_timeout: int = 120
    cache_ttl_days: int = 30

    # Primary + fallbacks. If one free model is rate-limited/down, the next is tried.
    # Pool rotates over time; refresh via: python scripts/check_models.py
    # 2026-09-18: nemotron-3-ultra verified working end-to-end (real Roman Urdu gen).
    # thinkingmachines/inkling-small REMOVED — 403 "only available on agentic
    # harnesses", can never work via plain chat/completions.
    model_chain: list[str] = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "google/gemma-4-31b-it:free",
        "deepseek/deepseek-v4-flash-0731:free",
        "openrouter/free",  # OpenRouter's own auto-router over free models
    ]

    # Sent as HTTP-Referer on OpenRouter calls. Empty = not sent (was a hardcoded
    # third-party GitHub URL before — audit L-1). Set APP_PUBLIC_URL to your own URL.
    app_public_url: str = ""

    # ---- v2 studios (shared contract V2-DESIGN.md Appendix E) ----
    # MEDIA_* Builder B (ai33.pro) ke liye, AVATAR_* Builder D (HeyGen) ke liye.
    MEDIA_HTTP_TIMEOUT: int = 60          # outbound third-party calls
    MEDIA_DAILY_CREATE_CAP: int = 20      # per user/day, create endpoints par
    MEDIA_TTS_DAILY_CHARS: int = 100000   # per user/day TTS characters
    AVATAR_DAILY_CREATE_CAP: int = 3      # per user/day, video/agent creates par
    AI33_BASE_URL: str = "https://api.ai33.pro"
    HEYGEN_BASE_URL: str = "https://api.heygen.com"

    @property
    def http_referer(self) -> str:
        return self.app_public_url

    app_title: str = "CIOS-YouTube"

    def ensure_auth_config(self) -> None:
        """Fail loud at startup when auth is required but no JWT secret is set.

        A fixed default secret is never used in production. With AUTH_REQUIRED=false
        (explicit local-dev opt-out) an ephemeral per-process secret is generated.
        """
        if self.auth_required and not self.jwt_secret:
            raise RuntimeError(
                "AUTH_REQUIRED=true hai lekin JWT_SECRET set nahi — .env (ya Render dashboard) "
                "me lambi random value dalo, phir restart karo."
            )
        if not self.jwt_secret:
            self.jwt_secret = secrets.token_urlsafe(32)


settings = Settings()
