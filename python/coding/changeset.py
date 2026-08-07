"""The changeset: every file the agent writes lands HERE first, never on disk.

WHY THIS EXISTS
---------------
Research mode only READS the world, so a bad run wastes time. Code mode WRITES
the user's files, so a bad run destroys work. Research's trust device is the
citation; Code mode's equivalent is the diff. The rule this module enforces is
"nothing touches your disk until you have seen exactly what changes".

WHAT IT BUYS US, CONCRETELY
---------------------------
Staging is fully reversible, and reversible actions do not need a human in the
loop. That is what lets `write_file` and `edit_file` drop from Risk.CONFIRM to
Risk.SAFE: the agent can edit eight files across twelve turns without a single
dialog, because none of it is real yet. The approval gate does not disappear —
it MOVES, from "once per tool call" to "once per batch, at the diff". One
decision with full context beats twelve decisions with none.

THE OVERLAY IS THE SUBTLE PART
------------------------------
`read()` returns pending content in preference to disk content. Without that,
an agent that writes a file and then reads it back sees the OLD text, concludes
its edit failed, and rewrites it — a loop that burns iterations and usually
ends with mangled output. The staged view has to be the view the agent lives
in, or the whole layer is a lie it keeps tripping over.

WHY IN-MEMORY AND NOT A DB TABLE
--------------------------------
Pending changes die with the process. That is the safe direction to fail: on
restart the user's disk is exactly as they left it, and the worst case is
re-asking for work that was never applied. Persisting them would mean a crashed
session can leave apply-able edits sitting around that nobody remembers
approving.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from coding.paths import rel_key, safe_path

MAX_PENDING_FILES = 200  # a runaway loop stages garbage; cap it rather than OOM


@dataclass
class PendingChange:
    """One file's staged future.

    `before` is captured at STAGE time, not apply time, on purpose — see
    ChangeSet.apply() for the conflict check that depends on it.
    """

    path: str                  # canonical relative posix path
    before: str | None         # None = the file did not exist -> this is a create
    after: str | None          # None = delete
    binary: bool = False       # existed but could not be decoded as text

    @property
    def kind(self) -> str:
        if self.after is None:
            return "delete"
        if self.before is None:
            return "create"
        return "modify"

    def diff(self) -> str:
        before_lines = (self.before or "").splitlines(keepends=True)
        after_lines = (self.after or "").splitlines(keepends=True)
        return "".join(difflib.unified_diff(
            before_lines, after_lines,
            fromfile=f"a/{self.path}", tofile=f"b/{self.path}",
            # 3 lines of context is git's default and the number people's eyes
            # are trained on; enough to locate a hunk, not enough to bury it.
            n=3,
        ))

    def stats(self) -> tuple[int, int]:
        """(additions, deletions) counted from the diff body, ignoring the
        +++/--- file headers so a 1-line change never reads as '2 additions'."""
        adds = dels = 0
        for line in self.diff().splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                adds += 1
            elif line.startswith("-") and not line.startswith("---"):
                dels += 1
        return adds, dels

    # Set when an apply was refused because the file changed on disk. Lives on
    # the change rather than in a one-shot API response so the REVIEW PANEL can
    # show it on the file's own card. A conflict reported once, in a toast that
    # fades, leaves the offending file sitting in the list looking exactly like
    # the ones that succeeded — which is how it gets applied by mistake later.
    conflict: bool = False

    def to_dict(self, include_diff: bool = True) -> dict:
        adds, dels = self.stats()
        out = {"path": self.path, "kind": self.kind, "additions": adds,
               "deletions": dels, "conflict": self.conflict}
        if include_diff:
            out["diff"] = self.diff()
        return out


class ConflictError(Exception):
    """The file changed on disk after the agent staged its edit."""


@dataclass
class ChangeSet:
    """Pending edits for ONE conversation, scoped to ONE workspace root.

    Scoped per conversation because that is the unit the user reviews and
    applies. Two chats editing two projects must never share a review queue.
    """

    root: str | None
    _pending: dict[str, PendingChange] = field(default_factory=dict)

    # ---------- reading (overlay) ----------

    def read(self, relative: str) -> tuple[str | None, str]:
        """Current text for a path AS THE AGENT SHOULD SEE IT.

        Returns (text, state) where state is one of "staged" (from this
        changeset), "disk", "missing", "binary".
        """
        p = safe_path(self.root, relative)
        key = self._key(p)
        if key in self._pending:
            change = self._pending[key]
            if change.after is None:
                return None, "missing"   # staged for deletion: treat as gone
            return change.after, "staged"
        if not p.is_file():
            return None, "missing"
        try:
            return p.read_text(encoding="utf-8"), "disk"
        except (UnicodeDecodeError, ValueError):
            return None, "binary"

    def exists(self, relative: str) -> bool:
        text, state = self.read(relative)
        return state in ("staged", "disk", "binary") or text is not None

    # ---------- staging ----------

    def stage_write(self, relative: str, content: str) -> PendingChange:
        p = safe_path(self.root, relative)
        key = self._key(p)
        # `before` is re-read from disk here, which is what clears a conflict:
        # the agent re-reading and redoing its edit picks up the user's version
        # as the new baseline, so the next apply has nothing to collide with.
        change = PendingChange(path=key, before=self._original(key, p), after=content)
        self._store(key, change)
        return change

    def stage_delete(self, relative: str) -> PendingChange:
        p = safe_path(self.root, relative)
        key = self._key(p)
        change = PendingChange(path=key, before=self._original(key, p), after=None)
        self._store(key, change)
        return change

    def _original(self, key: str, p: Path) -> str | None:
        """The on-disk text this file had BEFORE the agent touched it.

        Re-staging a file that is already pending must keep the ORIGINAL
        `before`, not the previous staged content. Otherwise a file edited
        three times produces a diff against edit #2 — the user reviews the last
        hop instead of the whole journey, which is exactly the thing they need
        to see.

        The ONE exception is a conflicted file. Its remembered `before` is the
        version the user has since replaced, so keeping it would guarantee the
        same conflict forever. Re-reading disk is exactly what "re-read and
        retry" means, and it adopts the user's edit as the new baseline.
        """
        if key in self._pending and not self._pending[key].conflict:
            return self._pending[key].before
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            return None

    def _store(self, key: str, change: PendingChange) -> None:
        if key not in self._pending and len(self._pending) >= MAX_PENDING_FILES:
            raise ConflictError(
                f"Too many pending files ({MAX_PENDING_FILES}). Apply or discard the "
                "current changes before making more."
            )
        # A change that ends up identical to the original is not a change. Drop
        # it so the review panel never shows an empty diff the user has to
        # squint at to confirm says nothing.
        if change.before == change.after:
            self._pending.pop(key, None)
            return
        change.conflict = False  # a freshly staged edit has not collided with anything
        self._pending[key] = change

    def _key(self, p: Path) -> str:
        assert self.root  # safe_path already raised if root was None
        return rel_key(self.root, p)

    # ---------- review ----------

    def is_empty(self) -> bool:
        return not self._pending

    def paths(self) -> list[str]:
        return sorted(self._pending)

    def staged_contents(self) -> dict[str, str | None]:
        """{relative path -> staged text}, with None meaning staged-for-delete.

        Exists so SEARCH can apply the same overlay reads do. An agent that
        edits a file and then greps for what it just wrote must find it, and
        must NOT find text it has already removed — otherwise it draws
        conclusions about a version of the project that no longer exists even
        in its own head.
        """
        return {k: c.after for k, c in self._pending.items()}

    def summary(self, include_diff: bool = True) -> list[dict]:
        return [self._pending[k].to_dict(include_diff) for k in self.paths()]

    def totals(self) -> dict:
        adds = dels = 0
        for change in self._pending.values():
            a, d = change.stats()
            adds += a
            dels += d
        return {"files": len(self._pending), "additions": adds, "deletions": dels}

    # ---------- resolution ----------

    def discard(self, paths: list[str] | None = None) -> list[str]:
        targets = self.paths() if paths is None else [p for p in paths if p in self._pending]
        for key in targets:
            self._pending.pop(key, None)
        return targets

    def apply(self, paths: list[str] | None = None) -> dict:
        """Write staged changes to disk. The ONLY method in Code mode that
        mutates the user's files.

        Conflict check: `before` was captured when the edit was staged. If the
        file on disk no longer matches it, someone else — usually the user in
        their own editor — changed it in the meantime. Applying anyway would
        silently destroy their edit, so those files are SKIPPED and reported.
        Everything clean still applies; a conflict in one file should not block
        the other nine.
        """
        targets = self.paths() if paths is None else [p for p in paths if p in self._pending]
        applied: list[str] = []
        conflicts: list[str] = []
        failed: list[dict] = []

        for key in targets:
            change = self._pending[key]
            try:
                p = safe_path(self.root, key)  # re-validated: never trust a stored path
                if self._on_disk(p) != change.before:
                    change.conflict = True   # sticks to the card until the edit is redone
                    conflicts.append(key)
                    continue
                if change.after is None:
                    if p.is_file():
                        p.unlink()
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(change.after, encoding="utf-8")
                applied.append(key)
                self._pending.pop(key, None)
            except OSError as e:
                failed.append({"path": key, "error": str(e)})

        return {"applied": applied, "conflicts": conflicts, "failed": failed,
                "remaining": len(self._pending)}

    @staticmethod
    def _on_disk(p: Path) -> str | None:
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            return None


class ChangeSetStore:
    """One ChangeSet per conversation, held in memory for the life of the app.

    Rebinding a conversation to a different folder throws its pending changes
    away rather than carrying them over — a diff computed against project A is
    meaningless against project B, and applying it there would be destructive.
    """

    def __init__(self):
        self._sets: dict[str, ChangeSet] = {}

    def get(self, conversation_id: str, root: str | None) -> ChangeSet:
        cs = self._sets.get(conversation_id)
        if cs is None or cs.root != root:
            cs = ChangeSet(root=root)
            self._sets[conversation_id] = cs
        return cs

    def peek(self, conversation_id: str) -> ChangeSet | None:
        return self._sets.get(conversation_id)

    def drop(self, conversation_id: str) -> None:
        self._sets.pop(conversation_id, None)
