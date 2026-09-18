"""SQLite storage: response cache (saves free-tier quota), run history, daily usage."""
import sqlite3
import time

from config import settings


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cache (
            prompt_hash TEXT PRIMARY KEY,
            response TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feature TEXT NOT NULL,
            input_json TEXT NOT NULL,
            output_text TEXT NOT NULL,
            model TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage (
            day TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS rate_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_call_at REAL NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO rate_state (id, last_call_at) VALUES (1, 0);
        """
    )
    conn.commit()
    conn.close()


def save_history(feature: str, input_json: str, output_text: str, model: str | None) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO history (feature, input_json, output_text, model, created_at) VALUES (?,?,?,?,?)",
            (feature, input_json, output_text, model, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(limit: int = 20) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, feature, input_json, output_text, model, created_at"
            " FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
