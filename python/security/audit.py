"""Security event log — the data behind Settings -> Security.

Every gateway decision, approval, block and sandbox degradation lands here.
WHY in SQLite and not just the log file: users should be able to SEE what the
security layer did on their behalf ("what did Arthur block last Tuesday?") —
that visibility is part of the product's trust story, not a debug artifact.
"""

from __future__ import annotations

import json
import logging
import time

from core.db import Database

log = logging.getLogger("arthur.audit")


class AuditLog:
    def __init__(self, db: Database):
        self._db = db

    async def record(self, kind: str, severity: str, **detail) -> None:
        # Cap detail size so a huge injected page can't bloat the DB.
        raw = json.dumps({k: (v if not isinstance(v, str) else v[:500]) for k, v in detail.items()})
        await self._db.write(
            "INSERT INTO security_events(ts, kind, severity, detail) VALUES(?,?,?,?)",
            (time.time(), kind, severity, raw),
        )
        log.info("[%s] %s %s", severity, kind, raw[:200])

    async def recent(self, limit: int = 200, offset: int = 0) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM security_events ORDER BY ts DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return rows

    async def purge(self) -> None:
        await self._db.write("DELETE FROM security_events")
