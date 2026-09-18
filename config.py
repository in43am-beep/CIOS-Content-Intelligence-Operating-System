"""Central settings. Everything runs at $0: only OpenRouter :free models are used."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    openrouter_api_key: str = ""
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    db_path: str = str(BASE_DIR / "cios.db")

    # OpenRouter free tier: 20 req/min always; 50 req/day on unfunded accounts.
    # We keep a safety buffer so the app never hard-hits the daily wall.
    daily_request_cap: int = 48
    min_seconds_between_calls: float = 3.5
    request_timeout: int = 120
    cache_ttl_days: int = 30

    # Primary + fallbacks. If one free model is rate-limited/down, the next is tried.
    # Pool rotates over time; refresh from https://openrouter.ai/models?free=true
    model_chain: list[str] = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "google/gemma-4-31b-it:free",
        "thinkingmachines/inkling-small:free",
        "openrouter/free",  # OpenRouter's own auto-router over free models
    ]

    http_referer: str = "https://github.com/in43am-beep/CIOS-Content-Intelligence-Operating-System"
    app_title: str = "CIOS-YouTube"


settings = Settings()
