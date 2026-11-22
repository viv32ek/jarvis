"""Local SQLite + Markdown persistent memory for Jarvis."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Fact:
    id: int
    content: str
    category: str
    created_at: str


@dataclass(frozen=True)
class Reminder:
    id: int
    text: str
    due_at: str | None
    completed: bool
    created_at: str


@dataclass(frozen=True)
class Message:
    id: int
    channel: str
    mode: str
    role: str
    content: str
    created_at: str


class MemoryStore:
    """Zero-cost local memory backed by SQLite + Markdown profile."""

    def __init__(self, db_path: Path, profile_path: Path) -> None:
        self.db_path = db_path
        self.profile_path = profile_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    due_at TEXT,
                    completed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'assistant',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Migrate existing databases created before the `mode` column existed.
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
            if "mode" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN mode TEXT NOT NULL DEFAULT 'assistant'")

    # --- Key-value state (e.g. active agy conversation_id) ---

    def get_state(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, _utc_now()),
            )

    def delete_state(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM state WHERE key = ?", (key,))

    # --- Profile Markdown ---

    def read_profile(self) -> str:
        if not self.profile_path.exists():
            return ""
        return self.profile_path.read_text(encoding="utf-8").strip()

    # --- Long-term facts ---

    def add_fact(self, content: str, category: str = "general") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO facts (content, category, created_at) VALUES (?, ?, ?)",
                (content.strip(), category.strip() or "general", _utc_now()),
            )
            return int(cur.lastrowid)

    def list_facts(self, limit: int = 20) -> list[Fact]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, content, category, created_at FROM facts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Fact(**dict(r)) for r in rows]

    def search_facts(self, query: str, limit: int = 10) -> list[Fact]:
        terms = re.findall(r"[a-z0-9_-]{3,}", query.lower())
        if not terms:
            return self.list_facts(limit=limit)
        clauses = " OR ".join("LOWER(content) LIKE ?" for _ in terms)
        params: list[object] = [f"%{t}%" for t in terms]
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, content, category, created_at FROM facts WHERE {clauses} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        if not rows:
            return self.list_facts(limit=limit)
        return [Fact(**dict(r)) for r in rows]

    def delete_fact(self, fact_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            return cur.rowcount > 0

    # --- Reminders ---

    def add_reminder(self, text: str, due_at: str | None = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO reminders (text, due_at, completed, created_at) VALUES (?, ?, 0, ?)",
                (text.strip(), due_at, _utc_now()),
            )
            return int(cur.lastrowid)

    def list_reminders(self, include_completed: bool = False) -> list[Reminder]:
        where = "" if include_completed else "WHERE completed = 0"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, text, due_at, completed, created_at FROM reminders {where} ORDER BY id ASC"
            ).fetchall()
        return [
            Reminder(
                id=r["id"],
                text=r["text"],
                due_at=r["due_at"],
                completed=bool(r["completed"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def complete_reminder(self, reminder_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE reminders SET completed = 1 WHERE id = ?", (reminder_id,))
            return cur.rowcount > 0

    # --- Cross-channel message history ---

    def log_message(self, channel: str, role: str, content: str, mode: str = "assistant") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages (channel, mode, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (channel, mode, role, content, _utc_now()),
            )
            return int(cur.lastrowid)

    def recent_messages(self, limit: int = 20, mode: str | None = None) -> list[Message]:
        with self._connect() as conn:
            if mode:
                rows = conn.execute(
                    "SELECT id, channel, mode, role, content, created_at FROM messages "
                    "WHERE mode = ? ORDER BY id DESC LIMIT ?",
                    (mode, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, channel, mode, role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [Message(**dict(r)) for r in reversed(rows)]

    # --- Prompt context builder ---

    def build_context_block(self, user_query: str = "") -> str:
        sections: list[str] = []

        profile = self.read_profile()
        if profile:
            sections.append(f"## User Profile\n{profile}")

        facts: Iterable[Fact] = self.search_facts(user_query, limit=8) if user_query else self.list_facts(limit=8)
        facts_list = list(facts)
        if facts_list:
            lines = [f"- [{f.id}] ({f.category}) {f.content}" for f in facts_list]
            sections.append("## Remembered Facts\n" + "\n".join(lines))

        reminders = self.list_reminders(include_completed=False)
        if reminders:
            lines = [
                f"- [{r.id}] {r.text}" + (f" (due: {r.due_at})" if r.due_at else "")
                for r in reminders
            ]
            sections.append("## Pending Reminders\n" + "\n".join(lines))

        return "\n\n".join(sections)
