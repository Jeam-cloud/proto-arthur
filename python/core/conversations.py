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

    async def list_all(self, *, archived: bool = False) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count "
            "FROM conversations c WHERE archived=? ORDER BY updated_at DESC",
            (1 if archived else 0,),
        )

    async def get(self, cid: str) -> dict:
        row = await self._db.fetch_one("SELECT * FROM conversations WHERE id=?", (cid,))
        if row is None:
            raise NotFoundError(f"Conversation {cid} not found")
        return row

    async def set_workspace(self, cid: str, root: str | None) -> None:
        """Bind this conversation to a folder (or clear it with None).

        Deliberately NOT validated against the filesystem here. A folder can be
        on a drive that is currently unplugged, and refusing to remember it
        would lose the binding for a project the user still has. The only place
        that must be strict is the moment a tool actually touches a path, which
        is `_safe_path` in tools/coding.py -- containment is enforced at use,
        not at configuration.
        """
        await self.get(cid)  # 404 for unknown ids
        await self._db.write(
            "UPDATE conversations SET workspace_root=?, updated_at=? WHERE id=?",
            ((root or None), now(), cid),
        )

    async def rename(self, cid: str, title: str) -> None:
        await self.get(cid)
        await self._db.write(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title[:120], now(), cid)
        )

    async def delete(self, cid: str) -> None:
        await self._db.write("DELETE FROM conversations WHERE id=?", (cid,))  # messages CASCADE

    async def set_archived(self, cid: str, archived: bool) -> None:
        await self.get(cid)  # 404 for unknown ids
        await self._db.write(
            "UPDATE conversations SET archived=?, updated_at=? WHERE id=?",
            (1 if archived else 0, now(), cid),
        )

    async def clone(self, cid: str) -> dict:
        """Duplicate a conversation and its messages as a new, independent one.
        WHY a real copy instead of a pointer: conversations are meant to
        branch from here (edit the clone, keep the original untouched), so
        sharing message rows would let an edit in one leak into the other."""
        src = await self.get(cid)
        new_cid = new_id()
        ts = now()
        title = src["title"] if src["title"].endswith(" (copy)") else f"{src['title']} (copy)"
        await self._db.write(
            "INSERT INTO conversations(id, title, persona_id, created_at, updated_at) VALUES(?,?,?,?,?)",
            (new_cid, title[:120], src["persona_id"], ts, ts),
        )
        rows = await self._db.fetch_all(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,)
        )
        if rows:
            await self._db.write_many([
                (
                    "INSERT INTO messages(id, conversation_id, role, content, tool_calls, tool_name, model, provider, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (new_id(), new_cid, r["role"], r["content"], r["tool_calls"], r["tool_name"], r["model"], r["provider"], r["created_at"]),
                )
                for r in rows
            ])
        return {"id": new_cid, "title": title[:120], "created_at": ts, "updated_at": ts, "message_count": len(rows)}

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

        # Attachments come back WITH their message.
        #
        # They were stored against the message from the start but never read
        # back, so scrolling up showed a question with no sign of the file it was
        # about -- and an attachment-only message (a dropped screenshot with no
        # words) rendered as a completely empty bubble.
        #
        # One query for the whole conversation, grouped in Python, rather than
        # one per message: a 200-turn chat would otherwise issue 200 queries to
        # populate a detail almost every message does not have.
        attach_rows = await self._db.fetch_all(
            "SELECT id, message_id, filename, kind, mime, size_bytes, extract_error "
            "FROM attachments WHERE conversation_id=? AND message_id IS NOT NULL "
            "ORDER BY created_at",
            (cid,),
        )
        by_message: dict[str, list[dict]] = {}
        for a in attach_rows:
            by_message.setdefault(a["message_id"], []).append({
                "id": a["id"],
                "filename": a["filename"],
                "kind": a["kind"],
                "mime": a.get("mime") or "",
                "size_bytes": a.get("size_bytes") or 0,
                "error": a.get("extract_error"),
            })
        for r in rows:
            r["attachments"] = by_message.get(r["id"], [])
        return rows

    async def history_for_model(
        self, cid: str, char_budget: int = 24_000, exclude_id: str | None = None,
    ) -> list[dict]:
        """Most recent turns that fit a rough character budget (~6k tokens).
        WHY chars not tokens: an exact tokenizer per local model isn't worth
        the dependency — the budget only guards against blowing the context
        window, and 4 chars/token is a safe planning number. Tool messages are
        excluded: they were transient working data for a past answer.

        `exclude_id` leaves out ONE message — always the turn being sent right
        now. The user message is persisted before the prompt is assembled (so a
        crash cannot lose it), which meant history already ended with the very
        message the caller then appended: the model received every question
        TWICE, back to back.

        That was wasteful for text and actively broken for attachments. Only the
        appended copy carries images, and two consecutive user messages is the
        shape a chat template is most likely to collapse or skip — taking the
        images with it, so the model answers "no image was attached" about a
        request that had one.
        """
        rows = await self._db.fetch_all(
            "SELECT id, role, content FROM messages "
            "WHERE conversation_id=? AND role IN ('user','assistant') AND id IS NOT ? "
            "ORDER BY created_at DESC LIMIT 60",
            (cid, exclude_id),
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
