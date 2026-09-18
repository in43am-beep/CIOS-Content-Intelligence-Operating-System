"""Shared encrypted key-store for third-party provider keys (Builder B owns, D consumes).

Providers: "ai33" (ai33.pro Media Studio), "heygen" (HeyGen Avatar Studio).

Encryption (Gate 4): Fernet (cryptography lib). Key derived once via
HKDF-SHA256(salt=b"cios-provider-keys-v1", info=b"cios-fernet",
ikm=settings.jwt_secret.encode()) -> base64.urlsafe_b64encode -> Fernet key.

Rules:
- never plaintext at rest (DB column holds the Fernet token string only)
- never logged (log only has_key bools / user ids)
- never returned in any API response, never sent to the browser
- raw_key validated (10-500 chars, stripped) before storing

DB tables are created HERE (CREATE TABLE IF NOT EXISTS at import) — database.py
is owned by Builder A, so this module owns its own tables:

  provider_keys: (user_id, provider, enc_key, created_at, updated_at)
  media_usage:   (user_id, day, provider, creates, tts_chars) — B enforces ai33
                 caps, D enforces HeyGen caps with provider='heygen'
"""
import base64
import logging
import time

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from config import settings
from database import get_conn

log = logging.getLogger("cios.provider_keys")

_KDF_SALT = b"cios-provider-keys-v1"
_KDF_INFO = b"cios-fernet"

_fernet: Fernet | None = None


def ensure_tables() -> None:
    """Create provider_keys + media_usage if missing. Safe to call repeatedly."""
    conn = get_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS provider_keys (
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                enc_key TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, provider)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS media_usage (
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                provider TEXT NOT NULL,
                creates INTEGER NOT NULL DEFAULT 0,
                tts_chars INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day, provider)
            )"""
        )
        conn.commit()
    finally:
        conn.close()


def _get_fernet() -> Fernet:
    """Module-level lazy singleton. Derive-once; never log the key material."""
    global _fernet
    if _fernet is None:
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_KDF_SALT,
            info=_KDF_INFO,
        )
        raw = hkdf.derive(settings.jwt_secret.encode("utf-8"))
        _fernet = Fernet(base64.urlsafe_b64encode(raw))
    return _fernet


def _reset_fernet() -> None:
    """Test-only: drop the cached Fernet so a new jwt_secret takes effect."""
    global _fernet
    _fernet = None


def _validate_key(raw_key: str) -> str:
    key = (raw_key or "").strip()
    if not (10 <= len(key) <= 500):
        raise ValueError("API key 10 se 500 characters ki honi chahiye.")
    return key


# --------------------------------------------------------- exact contract ---
# (signatures fixed by V2-DESIGN.md §0.2 — do not change)


def _ensure() -> None:
    """Lazy table creation: import-time side effect nahi (C-5), har public
    call se pehle idempotent ensure — lifespan, tests, ya direct import,
    har context me tables maujood hongi."""
    ensure_tables()


def set_provider_key(user_id: int, provider: str, raw_key: str) -> None:
    _ensure()
    key = _validate_key(raw_key)  # ValueError on bad input; caller shows message
    token = _get_fernet().encrypt(key.encode("utf-8")).decode("ascii")
    now = time.time()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO provider_keys "
            "(user_id, provider, enc_key, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, provider, token, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    # Caller ki raw_key variable ko scope se bahar jana chahiye; yahan sirf bool log.
    log.info("provider key stored user=%s provider=%s has_key=true", user_id, provider)


def get_provider_key(user_id: int, provider: str) -> str | None:
    """Decrypted key, memory-only. None when missing OR undecryptable
    (e.g. jwt_secret rotated) — caller treats None as 're-enter key'."""
    _ensure()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT enc_key FROM provider_keys WHERE user_id=? AND provider=?",
            (user_id, provider),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return _get_fernet().decrypt(row["enc_key"].encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.warning("provider key undecryptable user=%s provider=%s "
                    "(jwt_secret badal gaya?)", user_id, provider)
        return None


def delete_provider_key(user_id: int, provider: str) -> None:
    _ensure()
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM provider_keys WHERE user_id=? AND provider=?",
            (user_id, provider),
        )
        conn.commit()
    finally:
        conn.close()
    log.info("provider key deleted user=%s provider=%s has_key=false", user_id, provider)


def has_provider_key(user_id: int, provider: str) -> bool:
    _ensure()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM provider_keys WHERE user_id=? AND provider=?",
            (user_id, provider),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


# NOTE: import-time ensure_tables() nahi — har public function lazily ensure
# karti hai (C-5: import pe real DB ko chhuna mana hai; lifespan bhi ensure karta hai).
