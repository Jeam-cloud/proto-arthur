"""Conversation + message persistence."""

from __future__ import annotations

import json

from core.db import Database, new_id, now
from core.errors import NotFoundError


class ConversationStore:
    def __init__(self, db: Database):
        self._db = db

    async def create(self, persona_id: str | None = None) -> dict:
        cid = new_id()
        ts = now()
        await self._db.write(
            "INSERT INTO conversations(id, title, persona_id, created_at, updated_at) VALUES(?,?,?,?,?)",
            (cid, "New chat", persona_id, ts, ts),
        )
        return {"id": cid, "title": "New chat", "created_at": ts, "updated_at": ts}

    async def list_all(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count "
            "FROM conversations c WHERE archived=0 ORDER BY updated_at DESC"
        )

    async def get(self, cid: str) -> dict:
        row = await self._db.fetch_one("SELECT * FROM conversations WHERE id=?", (cid,))
        if row is None:
            raise NotFoundError(f"Conversation {cid} not found")
        return row

    async def rename(self, cid: str, title: str) -> None:
        await self.get(cid)
        await self._db.write(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title[:120], now(), cid)
        )

    async def delete(self, cid: str) -> None:
        await self._db.write("DELETE FROM conversations WHERE id=?", (cid,))  # messages CASCADE

    async def add_message(
        self,
        cid: str,
        role: str,
        content: str,
        *,
        tool_calls: list | None = None,
        tool_name: str | None = None,
        model: str | None = None,
        provider: str = "local",
    ) -> str:
        mid = new_id()
        await self._db.write_many([
            (
                "INSERT INTO messages(id, conversation_id, role, content, tool_calls, tool_name, model, provider, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (mid, cid, role, content, json.dumps(tool_calls) if tool_calls else None,
                 tool_name, model, provider, now()),
            ),
            ("UPDATE conversations SET updated_at=? WHERE id=?", (now(), cid)),
        ])
        return mid

    async def messages(self, cid: str, limit: int = 500) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at LIMIT ?",
            (cid, limit),
        )
        for r in rows:
            if r["tool_calls"]:
                r["tool_calls"] = json.loads(r["tool_calls"])
        return rows

    async def history_for_model(self, cid: str, char_budget: int = 24_000) -> list[dict]:
        """Most recent turns that fit a rough character budget (~6k tokens).
        WHY chars not tokens: an exact tokenizer per local model isn't worth
        the dependency — the budget only guards against blowing the context
        window, and 4 chars/token is a safe planning number. Tool messages are
        excluded: they were transient working data for a past answer."""
        rows = await self._db.fetch_all(
            "SELECT role, content FROM messages "
            "WHERE conversation_id=? AND role IN ('user','assistant') "
            "ORDER BY created_at DESC LIMIT 60",
            (cid,),
        )
        picked: list[dict] = []
        used = 0
        for r in rows:  # newest first; stop when budget is spent
            cost = len(r["content"])
            if used + cost > char_budget and picked:
                break
            picked.append({"role": r["role"], "content": r["content"]})
            used += cost
        picked.reverse()  # chronological for the model
        return picked
