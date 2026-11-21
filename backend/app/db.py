"""SQLite storage layer.

The schema is created automatically on startup. The database file location comes
from DATABASE_URL and, in Docker, lives on the /data named volume so history
survives container restarts. No external services are used.
"""
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional


def now_iso() -> str:
    """Server time as an ISO-8601 string (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def parse_sqlite_path(database_url: str) -> str:
    """Turn a sqlite:// URL into a filesystem path.

    Examples:
        sqlite:////data/app.db -> /data/app.db   (absolute)
        sqlite:///./app.db     -> ./app.db       (relative)
        sqlite:///app.db       -> app.db         (relative)
    """
    if database_url.startswith("sqlite:///"):
        return database_url[len("sqlite:///"):]
    if database_url.startswith("sqlite://"):
        return database_url[len("sqlite://"):]
    return database_url


class Database:
    """Thin, thread-safe SQLite wrapper.

    FastAPI runs sync handlers in a threadpool, so a single shared connection is
    guarded by a lock. Concurrency is low for this workload, so this is simple
    and correct.
    """

    def __init__(self, database_url: str) -> None:
        self.path = parse_sqlite_path(database_url)
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id    TEXT PRIMARY KEY,
                    system_prompt TEXT,
                    created_at    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role       TEXT NOT NULL,   -- 'user' or 'assistant'
                    content    TEXT NOT NULL,
                    metadata   TEXT,            -- JSON string, assistant messages only
                    created_at TEXT NOT NULL    -- server time, ISO-8601
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages (session_id, id);
                """
            )
            self._conn.commit()

    def ping(self) -> bool:
        """Return True if the database is reachable."""
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def get_session(self, session_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, system_prompt, created_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def ensure_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, system_prompt, created_at) "
                "VALUES (?, ?, ?)",
                (session_id, None, now_iso()),
            )
            self._conn.commit()

    def set_system_prompt(self, session_id: str, system_prompt: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET system_prompt = ? WHERE session_id = ?",
                (system_prompt, session_id),
            )
            self._conn.commit()

    def get_messages(self, session_id: str) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, metadata FROM messages "
                "WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[str],
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, metadata, created_at),
            )
            self._conn.commit()

    def reset(self) -> None:
        """Clear all data. Used by the test suite between cases."""
        with self._lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM sessions")
            self._conn.commit()
