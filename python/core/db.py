"""SQLite persistence: chat history, memories (canonical copy), personas,
settings, security events.

WHY raw SQL + aiosqlite instead of an ORM: this app has seven tables and one
process. Raw SQL keeps every query visible and greppable — when a consumer
app corrupts data you want to read exactly what ran, not decode ORM output.

WHY the write lock: SQLite allows one writer at a time. FastAPI handles
requests concurrently, so two writes could interleave and raise
`database is locked`. A single asyncio.Lock around writes + WAL journal mode
(readers never block) is the standard embedded-SQLite recipe.

WHY user_version migrations: schema changes ship as append-only SQL steps.
The DB records which step it is on, so v0.2 -> v0.5 upgrades replay only the
missing steps. Never edit an existing migration — add a new one.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT 'New chat',
        persona_id TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
        content TEXT NOT NULL,
        tool_calls TEXT,
        tool_name TEXT,
        model TEXT,
        provider TEXT NOT NULL DEFAULT 'local',
        created_at REAL NOT NULL
    );
    CREATE INDEX idx_messages_conv ON messages(conversation_id, created_at);
    CREATE TABLE memories (
        id TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'other',
        embedding TEXT,              -- JSON floats; source of truth for vector rebuilds
        source_conversation_id TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE personas (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        system_prompt TEXT NOT NULL,
        few_shots TEXT NOT NULL DEFAULT '[]',
        builtin INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE security_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (severity IN ('info','warning','blocked')),
        detail TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_security_ts ON security_events(ts DESC);
    """,
]


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    def __init__(self, path: Path | str):
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._migrate()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database.connect() was not called"
        return self._conn

    async def _migrate(self) -> None:
        cur = await self.conn.execute("PRAGMA user_version")
        (version,) = await cur.fetchone()
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            await self.conn.executescript(sql)
            await self.conn.execute(f"PRAGMA user_version={i}")
            await self.conn.commit()

    # ---- generic helpers ----
    async def write(self, sql: str, params: tuple = ()) -> None:
        async with self._write_lock:
            await self.conn.execute(sql, params)
            await self.conn.commit()

    async def write_many(self, statements: list[tuple[str, tuple]]) -> None:
        """Multiple statements in ONE transaction — all or nothing."""
        async with self._write_lock:
            try:
                for sql, params in statements:
                    await self.conn.execute(sql, params)
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    # ---- settings (typed JSON key/value) ----
    async def get_setting(self, key: str, default: Any = None) -> Any:
        row = await self.fetch_one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    async def set_setting(self, key: str, value: Any) -> None:
        await self.write(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
