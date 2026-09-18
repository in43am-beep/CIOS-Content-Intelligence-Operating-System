"""SQLite storage: users, per-user quota, response cache, run history.

Single uvicorn worker only (SQLite + in-process state). WAL mode + busy timeout
for the small concurrent-write surface (threadpool workers).
"""
import logging
import sqlite3
import time
from datetime import date

from config import settings

log = logging.getLogger("cios.db")

# Legacy single-user id used for rows created before multi-user auth landed.
LEGACY_USER_ID = 0
HISTORY_RETENTION_DAYS = 90
HISTORY_MAX_ROWS_PER_USER = 500


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return set()


def init_db() -> None:
    """Idempotent: safe to run on every startup and in tests."""
    conn = get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                pw_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                daily_cap INTEGER NOT NULL DEFAULT 48,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rate_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_call_at REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO rate_state (id, last_call_at) VALUES (1, 0);
            """
        )

        # --- history: add user_id to pre-auth databases (audit C-10 prep) ---
        if "history" not in {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            conn.execute(
                """CREATE TABLE history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    feature TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    output_text TEXT NOT NULL,
                    model TEXT,
                    created_at REAL NOT NULL
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id DESC)")
        elif "user_id" not in _table_columns(conn, "history"):
            conn.execute("ALTER TABLE history ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id DESC)")

        # --- usage: migrate (day) -> (user_id, day) composite key ---
        if "user_id" not in _table_columns(conn, "usage"):
            conn.execute(
                """CREATE TABLE IF NOT EXISTS usage_new (
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, day)
                )"""
            )
            cols = _table_columns(conn, "usage")
            if "day" in cols:
                conn.execute(
                    "INSERT OR IGNORE INTO usage_new (user_id, day, count) "
                    f"SELECT {LEGACY_USER_ID}, day, count FROM usage"
                )
                conn.execute("DROP TABLE usage")
            conn.execute("ALTER TABLE usage_new RENAME TO usage")

        # --- cache: migrate to per-user namespaced composite key ---
        if "user_id" not in _table_columns(conn, "cache"):
            conn.execute(
                """CREATE TABLE IF NOT EXISTS cache_new (
                    prompt_hash TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    response TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (prompt_hash, user_id)
                )"""
            )
            cols = _table_columns(conn, "cache")
            if "prompt_hash" in cols:
                conn.execute(
                    "INSERT OR IGNORE INTO cache_new (prompt_hash, user_id, response, created_at) "
                    f"SELECT prompt_hash, {LEGACY_USER_ID}, response, created_at FROM cache"
                )
                conn.execute("DROP TABLE cache")
            conn.execute("ALTER TABLE cache_new RENAME TO cache")

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- users ---

def create_user(email: str, pw_hash: str, is_admin: bool, daily_cap: int) -> dict:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (email, pw_hash, is_admin, daily_cap, created_at)"
            " VALUES (?,?,?,?,?)",
            (email, pw_hash, 1 if is_admin else 0, daily_cap, time.time()),
        )
        conn.commit()
        return get_user_by_id(cur.lastrowid)
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_users() -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    finally:
        conn.close()


def get_users() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, email, is_admin, daily_cap, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_user_cap(user_id: int, cap: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE users SET daily_cap=? WHERE id=?", (cap, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def user_daily_cap(user_id: int) -> int:
    user = get_user_by_id(user_id)
    if user and user.get("daily_cap"):
        return int(user["daily_cap"])
    return settings.daily_request_cap


# --------------------------------------------------------------- history ---

def save_history(feature: str, input_json: str, output_text: str,
                 model: str | None, user_id: int = LEGACY_USER_ID) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO history (user_id, feature, input_json, output_text, model, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, feature, input_json, output_text, model, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(limit: int = 20, user_id: int | None = None) -> list[dict]:
    conn = get_conn()
    try:
        if user_id is None:
            rows = conn.execute(
                "SELECT id, user_id, feature, input_json, output_text, model, created_at"
                " FROM history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, user_id, feature, input_json, output_text, model, created_at"
                " FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ----------------------------------------------------------------- usage ---

def usage_for_user(user_id: int) -> dict:
    conn = get_conn()
    try:
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT count FROM usage WHERE user_id=? AND day=?", (user_id, today)
        ).fetchone()
        return {
            "today": today,
            "used": row["count"] if row else 0,
            "cap": user_daily_cap(user_id),
        }
    finally:
        conn.close()


def usage_by_day(day: str) -> list[dict]:
    """Admin view: per-user counters for one day."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT u.user_id, COALESCE(us.email, '(dev)') AS email, u.count"
            " FROM usage u LEFT JOIN users us ON us.id = u.user_id"
            " WHERE u.day=? ORDER BY u.count DESC",
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ------------------------------------------------------- startup hygiene ---

def startup_maintenance() -> None:
    """Prune history + purge stale cache. Cache purge runs at most once per day
    (tracked in meta), not on every request (audit C-9)."""
    conn = get_conn()
    try:
        now = time.time()
        today = date.today().isoformat()

        # History retention: older than 90 days (audit C-10).
        conn.execute(
            "DELETE FROM history WHERE created_at < ?", (now - HISTORY_RETENTION_DAYS * 86400,)
        )

        # Per-user cap: keep newest 500 rows per user.
        user_ids = [r["user_id"] for r in
                    conn.execute("SELECT DISTINCT user_id FROM history").fetchall()]
        for uid in user_ids:
            conn.execute(
                "DELETE FROM history WHERE user_id=? AND id NOT IN ("
                " SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?)",
                (uid, uid, HISTORY_MAX_ROWS_PER_USER),
            )

        # Cache TTL purge: once per day.
        last = conn.execute("SELECT value FROM meta WHERE key='last_cache_purge'").fetchone()
        if not last or last["value"] != today:
            cutoff = now - settings.cache_ttl_days * 86400
            conn.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_cache_purge', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (today,),
            )
            log.info("cache TTL purge done (cutoff %d days)", settings.cache_ttl_days)

        conn.commit()
    finally:
        conn.close()
