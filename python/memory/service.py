"""Memory service: the one API the rest of the app uses.

Write path: fact -> embed -> dedupe check -> SQLite row (canonical, with the
embedding as JSON) + vector index entry.
Read path: query -> embed -> top-k similar -> relevance floor -> context block.

WHY a similarity dedupe (>= 0.92) instead of exact-match: users repeat
themselves with different words ("I like tea more than coffee" / "prefer tea
over coffee"). Near-duplicates rot recall quality — the same fact crowds out
different relevant ones in top-k. On near-match we UPDATE the existing memory
(fresher wording, bumped timestamp) rather than inserting a twin.

WHY a relevance floor (default 0.45): vector search always returns the k
nearest items even when nothing is related. Injecting irrelevant "memories"
confuses small models more than no memories at all.
"""

from __future__ import annotations

import json
import logging

from core.db import Database, new_id, now
from memory.embedder import Embedder
from memory.vector_store import VectorStore

log = logging.getLogger(__name__)

DEDUPE_SIMILARITY = 0.92
DEFAULT_RELEVANCE_FLOOR = 0.45


class MemoryService:
    def __init__(self, db: Database, embedder: Embedder, store: VectorStore):
        self._db = db
        self._embedder = embedder
        self._store = store
        self.available = True  # flips false if embedding fails (Ollama down / model missing)

    async def rebuild_index(self) -> int:
        """Boot-time: repopulate the vector index from SQLite (covers a wiped
        or corrupted Chroma dir, and the in-memory fallback)."""
        rows = await self._db.fetch_all(
            "SELECT id, text, embedding FROM memories WHERE enabled=1 AND embedding IS NOT NULL"
        )
        restored = 0
        for r in rows:
            try:
                self._store.update(r["id"], json.loads(r["embedding"]), r["text"])
                restored += 1
            except Exception as e:
                log.warning("could not restore memory %s: %s", r["id"], e)
        return restored

    async def add(self, text: str, category: str = "other", source_conversation_id: str | None = None) -> dict | None:
        try:
            [embedding] = await self._embedder.embed([text])
            self.available = True
        except Exception as e:
            self.available = False
            log.warning("embedding unavailable, memory not saved: %s", e)
            return None

        # Dedupe: update the near-duplicate instead of inserting a twin.
        for existing_id, sim in self._store.query(embedding, k=1):
            if sim >= DEDUPE_SIMILARITY:
                await self._db.write(
                    "UPDATE memories SET text=?, embedding=?, updated_at=? WHERE id=?",
                    (text, json.dumps(embedding), now(), existing_id),
                )
                self._store.update(existing_id, embedding, text)
                return await self._db.fetch_one("SELECT * FROM memories WHERE id=?", (existing_id,))

        mem_id = new_id()
        await self._db.write(
            "INSERT INTO memories(id, text, category, embedding, source_conversation_id, enabled, created_at, updated_at) "
            "VALUES(?,?,?,?,?,1,?,?)",
            (mem_id, text, category, json.dumps(embedding), source_conversation_id, now(), now()),
        )
        self._store.add(mem_id, embedding, text)
        return await self._db.fetch_one("SELECT * FROM memories WHERE id=?", (mem_id,))

    async def recall(self, query: str, k: int = 5, floor: float = DEFAULT_RELEVANCE_FLOOR) -> list[dict]:
        try:
            [embedding] = await self._embedder.embed([query])
            self.available = True
        except Exception as e:
            self.available = False
            log.warning("embedding unavailable, recall skipped: %s", e)
            return []

        hits = self._store.query(embedding, k=k * 2)  # overfetch, then filter
        results = []
        for mem_id, sim in hits:
            if sim < floor or len(results) >= k:
                continue
            row = await self._db.fetch_one(
                "SELECT * FROM memories WHERE id=? AND enabled=1", (mem_id,)
            )
            if row:
                row["similarity"] = round(sim, 3)
                row.pop("embedding", None)  # never ship 768 floats to the UI
                results.append(row)
        return results

    @staticmethod
    def format_context_block(memories: list[dict]) -> str:
        if not memories:
            return ""
        lines = "\n".join(f"- {m['text']}" for m in memories)
        return (
            "Relevant things you know about the user from past conversations "
            "(use naturally when helpful; don't recite):\n" + lines
        )

    # ---- Settings-page CRUD ----
    async def list_all(self) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT id, text, category, enabled, created_at, updated_at FROM memories ORDER BY updated_at DESC"
        )
        return rows

    async def update_text(self, mem_id: str, text: str) -> dict | None:
        try:
            [embedding] = await self._embedder.embed([text])
        except Exception:
            embedding = None  # keep the old vector; text still edits
        if embedding is not None:
            await self._db.write(
                "UPDATE memories SET text=?, embedding=?, updated_at=? WHERE id=?",
                (text, json.dumps(embedding), now(), mem_id),
            )
            self._store.update(mem_id, embedding, text)
        else:
            await self._db.write(
                "UPDATE memories SET text=?, updated_at=? WHERE id=?", (text, now(), mem_id)
            )
        return await self._db.fetch_one(
            "SELECT id, text, category, enabled FROM memories WHERE id=?", (mem_id,)
        )

    async def set_enabled(self, mem_id: str, enabled: bool) -> None:
        await self._db.write("UPDATE memories SET enabled=?, updated_at=? WHERE id=?", (int(enabled), now(), mem_id))
        if enabled:
            row = await self._db.fetch_one("SELECT text, embedding FROM memories WHERE id=?", (mem_id,))
            if row and row["embedding"]:
                self._store.update(mem_id, json.loads(row["embedding"]), row["text"])
        else:
            self._store.delete(mem_id)

    async def delete(self, mem_id: str) -> None:
        await self._db.write("DELETE FROM memories WHERE id=?", (mem_id,))
        self._store.delete(mem_id)
