"""Undo for applied changes: the safety net that replaces the approval gate.

WHY THIS EXISTS
---------------
Code mode used to protect the user by asking BEFORE writing: stage everything,
show a diff, wait for Apply. That works, but it puts the cost on every good
edit to catch the rare bad one, and a gate clicked forty times a day stops
being read. Worse, it made "Arthur says it edited the file" and "Arthur edited
the file" two separate facts — which is exactly the failure a small local model
produces (a confident summary of work it never did).

So the protection moves to the other side of the write. Arthur edits the file,
shows a receipt of what actually changed, and keeps the previous version so the
whole thing can be put back with one click. The user's guarantee is no longer
"nothing happens without your say-so" but "nothing that happens is permanent",
which is the guarantee people actually want while working.

WHY NOT GIT
-----------
Git would be the obvious undo, and it is the wrong dependency here. It needs a
binary that may not be installed, it only helps inside a repository (Code mode's
whole premise is someone editing a plain folder), and using it would mean Arthur
writing to the user's own history — the one place in their project where a bug
costs more than a file. Arthur reads git state elsewhere to inform the user; it
never mutates a repo. Copying the old bytes ourselves has none of those problems
and works everywhere.

WHY ON DISK, WHEN THE CHANGESET IS IN MEMORY
--------------------------------------------
Deliberately the opposite choice from ChangeSet, for the opposite reason.
PENDING edits die with the process because the safe direction to fail is "your
disk is as you left it". APPLIED edits already touched the disk, so the safe
direction is the reverse: the record of how to reverse them has to outlive the
process, or closing Arthur silently throws away the only way back.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from coding.paths import safe_path

log = logging.getLogger(__name__)

# How many applies stay undoable. Undo is for "that was wrong, put it back",
# which people realise within seconds — not an archive. Twenty is far past the
# point of usefulness and still trivially small on disk.
KEEP_APPLIES = 20

# A single file bigger than this is not snapshotted. write_file caps content at
# 400k, so this only excludes files the user already had; skipping one is
# reported rather than silently dropped, because an undo that quietly restores
# 4 of 5 files is worse than one that says it cannot.
MAX_SNAPSHOT_BYTES = 2_000_000


@dataclass
class UndoEntry:
    """One file inside one apply.

    `before` is what to restore. `after` is what Arthur wrote, kept ONLY so undo
    can tell "still as Arthur left it" from "the user has since edited this" —
    the same conflict test ChangeSet.apply does, pointed the other way.
    """

    path: str
    before: str | None    # None = the file did not exist -> undo deletes it
    after: str | None     # None = Arthur deleted it -> undo recreates it

    def to_dict(self) -> dict:
        return {"path": self.path, "before": self.before, "after": self.after}

    @classmethod
    def from_dict(cls, d: dict) -> UndoEntry:
        return cls(path=d["path"], before=d.get("before"), after=d.get("after"))


class UndoStore:
    """Snapshots of what files looked like before each apply.

    One JSON file per apply, named with a sortable timestamp so "most recent"
    is a directory listing rather than an index that could disagree with what
    is on disk.
    """

    def __init__(self, directory: Path, keep: int = KEEP_APPLIES):
        self._dir = Path(directory)
        self._keep = keep

    # ---------- writing ----------

    def record(self, conversation_id: str, root: str | None,
               entries: list[UndoEntry]) -> str | None:
        """Save the pre-apply state of these files. Returns an apply id.

        Failure here must never break an apply that already succeeded: the
        files are written, and refusing to report that because a snapshot could
        not be saved would be the worst of both worlds. So this logs and
        returns None instead of raising — the user loses undo for one apply and
        is told so, rather than losing the apply.
        """
        entries = [e for e in entries if self._snapshottable(e)]
        if not entries:
            return None
        apply_id = f"{time.time_ns():024d}"
        payload = {
            "id": apply_id,
            "conversation_id": conversation_id,
            "root": root,
            "at": time.time(),
            "entries": [e.to_dict() for e in entries],
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{apply_id}.json"
            # Written to a temp name and moved, so a crash mid-write can never
            # leave a half-parsed snapshot that looks like a usable undo.
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            log.warning("could not save undo snapshot: %s", e)
            return None
        self._prune()
        return apply_id

    @staticmethod
    def _snapshottable(entry: UndoEntry) -> bool:
        for text in (entry.before, entry.after):
            if text is not None and len(text.encode("utf-8", "ignore")) > MAX_SNAPSHOT_BYTES:
                log.info("skipping undo snapshot for %s: too large", entry.path)
                return False
        return True

    def _prune(self) -> None:
        try:
            files = sorted(self._dir.glob("*.json"))
        except OSError:
            return
        for stale in files[:-self._keep] if self._keep > 0 else files:
            try:
                stale.unlink()
            except OSError:
                pass

    # ---------- reading ----------

    def list(self, conversation_id: str | None = None) -> list[dict]:
        """Undoable applies, most recent first, without file contents.

        Contents are deliberately left out: this feeds a button label ("Undo —
        3 files"), and shipping every snapshotted file to render it would make
        a cheap poll expensive.
        """
        out = []
        for record in self._records():
            if conversation_id and record.get("conversation_id") != conversation_id:
                continue
            entries = record.get("entries", [])
            out.append({
                "id": record["id"],
                "conversation_id": record.get("conversation_id"),
                "at": record.get("at"),
                "root": record.get("root"),
                "files": [e["path"] for e in entries],
            })
        return out

    def latest(self, conversation_id: str) -> dict | None:
        found = self.list(conversation_id)
        return found[0] if found else None

    def _records(self) -> list[dict]:
        try:
            paths = sorted(self._dir.glob("*.json"), reverse=True)
        except OSError:
            return []
        records = []
        for p in paths:
            try:
                records.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError):
                # A corrupt snapshot is skipped, not fatal. Undo is a safety
                # net; a hole in it must not take the app down with it.
                log.warning("ignoring unreadable undo snapshot %s", p.name)
        return [r for r in records if isinstance(r, dict) and r.get("id")]

    # ---------- undoing ----------

    def undo(self, apply_id: str) -> dict:
        """Put the files back the way they were before `apply_id`.

        Skips any file the user has edited since. That mirrors the conflict
        check on apply, and for the same reason: Arthur's job is to reverse its
        OWN write, and a file that no longer matches what Arthur wrote contains
        someone else's work. Restoring over it would make undo destructive,
        which is a contradiction in terms.
        """
        record = next((r for r in self._records() if r["id"] == apply_id), None)
        if record is None:
            return {"restored": [], "skipped": [], "failed": [],
                    "error": "That change is no longer undoable."}

        root = record.get("root")
        restored: list[str] = []
        skipped: list[str] = []
        failed: list[dict] = []

        for raw in record.get("entries", []):
            entry = UndoEntry.from_dict(raw)
            try:
                # Re-validated against the root, never trusted as a stored
                # string: a snapshot file is on disk and could be edited.
                p = safe_path(root, entry.path)
                if self._on_disk(p) != entry.after:
                    skipped.append(entry.path)
                    continue
                if entry.before is None:
                    if p.is_file():
                        p.unlink()
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(entry.before, encoding="utf-8")
                restored.append(entry.path)
            except (OSError, ValueError) as e:
                failed.append({"path": entry.path, "error": str(e)})

        # A fully reversed apply is spent — keeping it would offer the user an
        # Undo button that silently does nothing the second time. If anything
        # was skipped or failed, the record stays so they can retry after
        # sorting out the file that got in the way.
        if not skipped and not failed:
            try:
                (self._dir / f"{apply_id}.json").unlink()
            except OSError:
                pass

        return {"restored": restored, "skipped": skipped, "failed": failed}

    @staticmethod
    def _on_disk(p: Path) -> str | None:
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
