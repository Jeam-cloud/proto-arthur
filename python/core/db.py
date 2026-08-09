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
    # 2 — a folder per conversation.
    #
    # The workspace used to be ONE app-wide setting, so every chat pointed at
    # the same folder and switching projects meant a round trip through
    # Settings. Binding it to the conversation makes "which files can this chat
    # touch" a property of the chat itself, which is also the honest answer for
    # a security boundary: the containment check in tools/coding.py is only
    # meaningful if the root it checks against cannot change under a running
    # conversation.
    #
    # NULL means "not chosen yet", not "no access" -- resolution falls back to
    # the last-used folder so a new chat inherits rather than prompting again.
    # See _conversation_workspace() in core/api/routes.py.
    """
    ALTER TABLE conversations ADD COLUMN workspace_root TEXT;
    """,
    # 3 — file attachments.
    #
    # A separate table rather than columns on `messages`: one message can carry
    # several files, and an attachment exists BEFORE the message does (you drop
    # files in, then type). `message_id` is therefore nullable -- NULL means
    # "staged in the composer, not sent yet".
    #
    # `extracted_text` is stored rather than re-derived on every send. Parsing a
    # 300-page PDF once at attach time is the difference between a chat that
    # feels instant and one that stalls for seconds per message, and it also
    # means the conversation stays readable years later even if the original
    # file is deleted or moved.
    #
    # ON DELETE SET NULL for messages, CASCADE for conversations: deleting a
    # message should not silently destroy a file the user attached, but deleting
    # a whole conversation should take its attachments with it.
    """
    CREATE TABLE attachments (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
        filename TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        source_path TEXT,
        mime TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'other',
        size_bytes INTEGER NOT NULL DEFAULT 0,
        extracted_text TEXT,
        extract_error TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX idx_attach_conv ON attachments(conversation_id, created_at);
    CREATE INDEX idx_attach_msg ON attachments(message_id);
    """,

    # 4 — the 'receipt' message role.
    #
    # Code mode writes one of these when the user applies a changeset: "Wrote 4
    # files to ~/dev/atlas". It has to live in the messages table so it appears
    # in the transcript in the right place and survives a restart -- a toast
    # cannot do either -- but it must NEVER be replayed to the model.
    # history_for_model already selects only 'user' and 'assistant', so a new
    # role is excluded from the prompt for free, with no filter to remember.
    #
    # WHY a table rebuild: SQLite cannot ALTER a CHECK constraint. The
    # create-copy-drop-rename dance is the documented way, and it runs inside
    # the migration's implicit transaction, so a failure part-way leaves the old
    # table intact rather than a half-migrated database.
    #
    # Foreign keys are disabled for the swap: attachments.message_id references
    # messages(id), and dropping the old table with them on would either fail or
    # cascade the references away. `legacy_alter_table` keeps the rename from
    # rewriting those references to point at the temporary name.
    """
    PRAGMA foreign_keys=OFF;
    PRAGMA legacy_alter_table=ON;

    CREATE TABLE messages_new (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system','receipt')),
        content TEXT NOT NULL,
        tool_calls TEXT,
        tool_name TEXT,
        model TEXT,
        provider TEXT NOT NULL DEFAULT 'local',
        created_at REAL NOT NULL
    );
    INSERT INTO messages_new
        SELECT id, conversation_id, role, content, tool_calls, tool_name, model, provider, created_at
        FROM messages;
    DROP TABLE messages;
    ALTER TABLE messages_new RENAME TO messages;
    -- Same name as migration 1's index: DROP TABLE took the original with it,
    -- and the rebuilt schema should differ from the old one in exactly one way.
    CREATE INDEX idx_messages_conv ON messages(conversation_id, created_at);

    PRAGMA legacy_alter_table=OFF;
    PRAGMA foreign_keys=ON;
    """,

    # 5 — mode belongs to the conversation.
    #
    # It used to be `useState("general")` in App.jsx: app-level React state,
    # never persisted and never attached to anything. So a "Code chat" was not a
    # thing that existed — a conversation was whatever mode the rail happened to
    # point at while you were looking at it, and a reload turned every chat back
    # into General, including ones holding staged edits.
    #
    # Storing it makes a conversation mean something: this chat is a Code chat,
    # bound to this folder, with these tools, forever. Which in turn makes the
    # folder binding beside it (migration 2) actually usable for more than one
    # project at a time.
    #
    # DEFAULT 'general' so every existing conversation keeps behaving exactly as
    # it did. No CHECK constraint on the value: TaskMode is the authority and it
    # will gain modes, and a schema-level enum would mean a table rebuild every
    # time one is added.
    """
    ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'general';
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
