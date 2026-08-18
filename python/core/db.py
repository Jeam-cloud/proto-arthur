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

    # 6 — whether a receipt's edits were actually reviewed before they landed.
    #
    # The two apply paths (auto-apply at the end of a turn vs. the review
    # panel's Apply button) share one receipt renderer, and until now that
    # renderer's "you reviewed every line" note was hardcoded true no matter
    # which path produced it. Correct when review-before-apply is on; false —
    # and told to the user anyway — when auto-apply wrote the files straight
    # through. A receipt exists so the transcript cannot claim something that
    # didn't happen; this column is what lets it stop doing that to itself.
    #
    # NULL for every role except 'receipt': the fact only exists at the moment
    # of an apply, and NULL reads honestly as "not applicable" rather than
    # false reading as "no one reviewed this either" for an ordinary message.
    """
    ALTER TABLE messages ADD COLUMN reviewed INTEGER;
    """,

    # 7 — the chosen model belongs to the conversation too.
    #
    # It was `modelOverride: {}` in a Zustand store: renderer memory, never
    # persisted. So picking qwen2.5-coder for a Code chat held until the next
    # launch and then silently reverted to Auto — and "silently" is the problem,
    # because the chip still looked authoritative while the request went to a
    # different model than the one the last twenty turns had used.
    #
    # This is the same fix migrations 2 and 5 made for the folder and the mode,
    # for the same reason: a conversation's identity should survive a restart.
    #
    # EMPTY STRING IS NOT NULL HERE. '' means "Auto — follow Settings", which is
    # a real choice a user can make (and can switch BACK to); NULL means the
    # conversation predates this column and has never expressed one. They
    # resolve the same way today, but only one of them is a decision.
    """
    ALTER TABLE conversations ADD COLUMN model TEXT;
    """,

    # 8 — portfolio holdings.
    #
    # HAND-ENTERED AND LOCAL. This is the most sensitive data in the app: what
    # someone is curious about is a watchlist, what they OWN is different. It
    # lives in this file and goes nowhere — no account, no sync, no server —
    # which is the one claim a local-first app can make here that a hosted one
    # cannot.
    #
    # QUANTITY AND COST ARE REAL, NOT INTEGER. Fractional shares are normal now,
    # and a cost basis is a price. Storing either as an integer would silently
    # round someone's position.
    #
    # `purchase_date` is nullable ON PURPOSE. Every required field is a person
    # deciding not to bother, and the date is not needed to value a holding —
    # only to do the return maths we are deliberately NOT doing (see the brief:
    # no IRR, no time-weighted return, because that needs a full transaction
    # history we are not asking for).
    """
    CREATE TABLE holdings (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        quantity REAL NOT NULL,
        cost_basis REAL NOT NULL,
        purchase_date TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX idx_holdings_symbol ON holdings(symbol);
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
