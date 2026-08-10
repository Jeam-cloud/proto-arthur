"""Patches: changing part of a big file without reprinting the whole thing.

WHY THIS EXISTS
---------------
File blocks (coding/fileblocks.py) are the primary write path because printing
a file is the one thing a small local model does reliably. They have one honest
weakness, stated when they were built: the whole file goes through the context
every time. An 80-line stylesheet is free; a 600-line module round-tripped twice
is most of an 8k window, and the model starts forgetting the request it was
answering.

`edit_file` already covers a single snippet. What was missing is the middle
ground — several small changes, possibly across several files, in one turn —
without either reprinting everything or making six separate tool calls that a
7B will not reliably emit.

THE FORMAT is the one Codex uses, chosen because models have seen it:

    *** Begin Patch
    *** Update File: app/static/login.css
    @@
     .label {
    -    background-color: #FE5654;
    +    background-color: #1E88E5;
     }
    *** Add File: app/new.py
    +print("hi")
    *** Delete File: app/old.py
    *** End Patch

STRICTNESS IS THE POINT
-----------------------
Deliberately stricter than `git apply`: no fuzz, no offsets, no partial
application. A hunk's context must match exactly once, and if ANY hunk in the
patch fails, nothing is staged at all. Fuzzy patching exists for humans applying
old patches to moved code; here the "old code" is text the model may simply have
misremembered, and a fuzzy match against misremembered context is how a file
gets quietly corrupted in the middle. All-or-nothing also means the model gets
one clear error instead of a half-applied file it then has to reason about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

BEGIN = "*** Begin Patch"
END = "*** End Patch"

_UPDATE = re.compile(r"^\*\*\*\s*Update File:\s*(.+?)\s*$")
_ADD = re.compile(r"^\*\*\*\s*Add File:\s*(.+?)\s*$")
_DELETE = re.compile(r"^\*\*\*\s*Delete File:\s*(.+?)\s*$")
_HUNK = re.compile(r"^@@")


class PatchError(Exception):
    """The patch could not be parsed or could not be applied cleanly.

    One exception type for both, because the model needs the same thing in
    either case: a plain sentence saying what to fix, and the knowledge that
    NOTHING was written.
    """


@dataclass
class FileOp:
    kind: str                      # "update" | "add" | "delete"
    path: str
    hunks: list[list[str]] = field(default_factory=list)   # update: raw ' '/'+'/'-' lines
    content: str = ""              # add: the new file


def parse_patch(text: str) -> list[FileOp]:
    """Patch text -> file operations, or raise PatchError.

    The Begin/End markers are optional. Models drop them constantly, and
    refusing an otherwise well-formed patch over a missing banner would be
    exactly the kind of punctuation-strictness that made tool calls unusable in
    the first place.
    """
    if not text or not text.strip():
        raise PatchError("The patch was empty.")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Trim to the banners when present, so surrounding prose is harmless.
    start = next((i + 1 for i, ln in enumerate(lines) if ln.strip() == BEGIN), 0)
    stop = next((i for i, ln in enumerate(lines) if ln.strip() == END), len(lines))
    lines = lines[start:stop]

    ops: list[FileOp] = []
    current: FileOp | None = None
    for raw in lines:
        line = raw.rstrip("\n")
        for pattern, kind in ((_UPDATE, "update"), (_ADD, "add"), (_DELETE, "delete")):
            m = pattern.match(line.strip())
            if m:
                current = FileOp(kind=kind, path=m.group(1).strip().strip("`\"'"))
                ops.append(current)
                break
        else:
            if current is None:
                continue          # prose before the first file header
            if current.kind == "update":
                if _HUNK.match(line):
                    current.hunks.append([])
                elif current.hunks:
                    current.hunks[-1].append(line)
                elif line.startswith(("+", "-", " ")):
                    # A hunk body with no @@ header. Common, and harmless to
                    # accept: the header carries no information we use.
                    current.hunks.append([line])
            elif current.kind == "add":
                # '+' prefixes are conventional but frequently dropped.
                current.content += (line[1:] if line.startswith("+") else line) + "\n"

    if not ops:
        raise PatchError(
            "No file sections found. Each file needs a line like "
            "'*** Update File: path/to/file.css'."
        )
    for op in ops:
        if op.kind == "update" and not any(any(h) for h in op.hunks):
            raise PatchError(f"{op.path}: the Update section has no changes in it.")
    return ops


def apply_hunks(original: str, hunks: list[list[str]], path: str) -> str:
    """Apply every hunk to `original`, or raise. Never partially applied.

    Each hunk becomes a straight find-and-replace: the ' ' and '-' lines are
    what must be there now, the ' ' and '+' lines are what replaces them. That
    reduces patching to the same contract `edit_file` already has — exact text,
    matched exactly once — which is the contract that has proven safe.
    """
    text = original
    for index, hunk in enumerate(hunks, start=1):
        body = [ln for ln in hunk if ln.strip() or ln.startswith((" ", "+", "-"))]
        if not body:
            continue
        before, after = [], []
        for line in body:
            tag, content = (line[0], line[1:]) if line[:1] in (" ", "+", "-") else (" ", line)
            if tag in (" ", "-"):
                before.append(content)
            if tag in (" ", "+"):
                after.append(content)

        old = "\n".join(before)
        new = "\n".join(after)
        if not old.strip():
            raise PatchError(
                f"{path}: hunk {index} has nothing to match against — include the "
                "surrounding unchanged lines so the location is unambiguous."
            )
        count = text.count(old)
        if count == 0:
            raise PatchError(
                f"{path}: hunk {index} does not match the file. Re-read {path} and copy "
                "the context lines exactly as they appear, including indentation."
            )
        if count > 1:
            raise PatchError(
                f"{path}: hunk {index} matches {count} places. Include more surrounding "
                "lines so it can only mean one of them."
            )
        text = text.replace(old, new, 1)
    return text
