"""Personas: named system prompts + few-shot examples.

The DEFAULT_PERSONA ships in code (not just a DB row) so a fresh install and
a wiped DB both have a working personality. Few-shots are stored as
user/assistant pairs and injected as real messages — few-shot examples
placed IN the message list steer small local models far more reliably than
prose instructions in the system prompt.
"""

from __future__ import annotations

import json

from core.db import Database, new_id, now

DEFAULT_PERSONA = {
    "name": "Arthur",
    "system_prompt": (
        "You are Arthur, a personal AI assistant that runs entirely on the user's own "
        "computer. You are warm, direct, and concise — a capable aide, not a lecture. "
        "You have persistent memory: facts you know about the user may be provided as "
        "context; use them naturally and never recite them as a list unless asked. "
        "When you use tools, briefly say what you're doing. When something needs the "
        "user's confirmation, the app will ask them — don't ask again yourself. "
        "If you can't do something (a tool is disabled, no internet, low confidence), "
        "say so plainly and offer the closest thing you can do. Never invent tool "
        "results or pretend an action succeeded."
    ),
    "few_shots": [
        {
            "user": "can you check my portfolio real quick",
            "assistant": "I can pull live quotes in Finance mode — switch the mode selector to Finance and tell me which tickers, and I'll fetch them.",
        }
    ],
}


class PersonaStore:
    def __init__(self, db: Database):
        self._db = db

    async def ensure_default(self) -> None:
        row = await self._db.fetch_one("SELECT id FROM personas WHERE builtin=1")
        if row:
            return
        await self._db.write(
            "INSERT INTO personas(id, name, system_prompt, few_shots, builtin, is_active, created_at) "
            "VALUES(?,?,?,?,1,1,?)",
            (new_id(), DEFAULT_PERSONA["name"], DEFAULT_PERSONA["system_prompt"],
             json.dumps(DEFAULT_PERSONA["few_shots"]), now()),
        )

    async def active(self) -> dict:
        row = await self._db.fetch_one("SELECT * FROM personas WHERE is_active=1")
        if row is None:  # someone deleted everything — self-heal
            await self.ensure_default()
            row = await self._db.fetch_one("SELECT * FROM personas WHERE builtin=1")
        row["few_shots"] = json.loads(row["few_shots"])
        return row

    async def list_all(self) -> list[dict]:
        rows = await self._db.fetch_all("SELECT * FROM personas ORDER BY builtin DESC, created_at")
        for r in rows:
            r["few_shots"] = json.loads(r["few_shots"])
        return rows

    async def create(self, name: str, system_prompt: str, few_shots: list[dict]) -> dict:
        pid = new_id()
        await self._db.write(
            "INSERT INTO personas(id, name, system_prompt, few_shots, builtin, is_active, created_at) "
            "VALUES(?,?,?,?,0,0,?)",
            (pid, name, system_prompt, json.dumps(few_shots), now()),
        )
        return {"id": pid, "name": name, "system_prompt": system_prompt, "few_shots": few_shots}

    async def update(self, pid: str, name: str, system_prompt: str, few_shots: list[dict]) -> None:
        await self._db.write(
            "UPDATE personas SET name=?, system_prompt=?, few_shots=? WHERE id=?",
            (name, system_prompt, json.dumps(few_shots), pid),
        )

    async def activate(self, pid: str) -> None:
        # Both statements in one transaction: there is never a moment with
        # zero or two active personas.
        await self._db.write_many([
            ("UPDATE personas SET is_active=0", ()),
            ("UPDATE personas SET is_active=1 WHERE id=?", (pid,)),
        ])

    async def delete(self, pid: str) -> bool:
        row = await self._db.fetch_one("SELECT builtin, is_active FROM personas WHERE id=?", (pid,))
        if row is None or row["builtin"]:
            return False  # the built-in persona is not deletable
        await self._db.write("DELETE FROM personas WHERE id=?", (pid,))
        if row["is_active"]:
            builtin = await self._db.fetch_one("SELECT id FROM personas WHERE builtin=1")
            await self.activate(builtin["id"])
        return True
