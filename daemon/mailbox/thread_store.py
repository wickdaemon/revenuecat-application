import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path("runs/threads.db")

# Valid states in order of progression
STATES = [
    "applied",
    "confirmation_received",
    "screening",
    "interview",
    "offer",
    "closed",
    "rejected",
]

class ThreadStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS applications (
                    company         TEXT PRIMARY KEY,
                    state           TEXT NOT NULL DEFAULT 'applied',
                    updated_at      TEXT NOT NULL,
                    created_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id       TEXT NOT NULL,
                    company         TEXT NOT NULL,
                    message_id      TEXT NOT NULL UNIQUE,
                    from_addr       TEXT,
                    subject         TEXT,
                    body            TEXT,
                    timestamp       TEXT NOT NULL,
                    logged_at       TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS seen_message_ids (
                    message_id      TEXT PRIMARY KEY,
                    seen_at         TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_thread
                    ON messages(thread_id);
                CREATE INDEX IF NOT EXISTS idx_messages_company
                    ON messages(company);
            """)

    def get_state(self, company: str) -> Optional[str]:
        """Return current state for company, or None if not tracked."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM applications WHERE company = ?",
                (company,)
            ).fetchone()
            return row["state"] if row else None

    def set_state(self, company: str, state: str) -> None:
        """Create or update application state for company."""
        if state not in STATES:
            raise ValueError(f"Invalid state '{state}'. Must be one of: {STATES}")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO applications (company, state, updated_at, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(company) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at
            """, (company, state, now, now))

    def log_message(
        self,
        thread_id: str,
        message: "EmailMessage",
        company: str,
    ) -> None:
        """Persist an email message to the messages table."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO messages
                    (thread_id, company, message_id, from_addr, subject,
                     body, timestamp, logged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                thread_id,
                company,
                message.id,
                message.from_addr,
                message.subject,
                message.body,
                message.timestamp.isoformat(),
                now,
            ))

    def mark_seen(self, message_id: str) -> None:
        """Record a message ID as processed."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO seen_message_ids (message_id, seen_at)
                VALUES (?, ?)
            """, (message_id, now))

    def is_seen(self, message_id: str) -> bool:
        """Return True if this message ID has been processed before."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_message_ids WHERE message_id = ?",
                (message_id,)
            ).fetchone()
            return row is not None

    def get_messages(self, company: str) -> list[dict]:
        """Return all logged messages for a company, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM messages WHERE company = ?
                   ORDER BY timestamp ASC""",
                (company,)
            ).fetchall()
            return [dict(r) for r in rows]

    def list_applications(self) -> list[dict]:
        """Return all tracked applications with current state."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM applications ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
