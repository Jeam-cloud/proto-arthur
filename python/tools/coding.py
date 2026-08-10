"""Coding tools: read, list, edit and write files inside ONE user-chosen
workspace folder, plus sandboxed Python execution.

RISK SPLIT, AND WHY IT CHANGED
------------------------------
Reads and listing are SAFE — workspace-scoped and non-destructive.

Writes and edits are ALSO SAFE now, which looks alarming until you see where
they go: nowhere. They stage into the conversation's ChangeSet
(coding/changeset.py) and never touch disk. Staged work is fully reversible,
and reversible actions do not need a human in the loop, so the agent can work
across many files uninterrupted. The approval gate MOVED rather than vanished:
it now sits at the diff (POST /conversations/{cid}/changes/apply), where the
user approves the whole batch with every change visible at once.

Execution stays CONFIRM. Running code is not reversible and not previewable —
a diff cannot tell you what `run_python` will do — so it keeps its dialog, plus
a CodeShield (or fallback regex) scan whose findings are shown IN that dialog.

EDIT VS WRITE
-------------
`edit_file` replaces an exact snippet; `write_file` replaces a whole file.
Editing is the one to reach for on existing code, and the uniqueness rule below
is what makes it trustworthy — see EditFileTool.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from coding.patches import PatchError, apply_hunks, parse_patch
from coding.paths import SKIP_DIRS, safe_path
from core.errors import ArthurError
from sandbox.runner import SandboxRunner
from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

CODE_IMAGE = "arthur-code:1"
MAX_READ_BYTES = 200_000

# Re-exported under its old private name: tests/test_paths.py and any existing
# imports still reach for tools.coding._safe_path.
_safe_path = safe_path

# Fallback patterns when CodeShield isn't installed. The sandbox (no network,
# read-only fs) is the real containment; this is a human-facing heads-up.
_SUSPICIOUS = [
    "os.system", "subprocess", "socket.", "shutil.rmtree", "eval(", "exec(",
    "__import__", "ctypes", "urllib.request", "requests.",
]

_NO_CHANGESET = (
    "Error: file editing is unavailable for this conversation. Tell the user to "
    "pick a workspace folder in the folder bar at the top of Code mode."
)


def _changes(ctx: ToolContext):
    """The conversation's staging buffer, or None.

    Every write path calls this and BAILS on None. It never falls back to
    writing directly to disk — a bug that disabled staging would otherwise turn
    silently into 'the agent edits your files with no review', which is the one
    outcome this whole design exists to prevent.
    """
    return ctx.changes


def _counts(adds: int, dels: int) -> str:
    """The activity feed's right-hand metric. Both halves are shown even when
    one is zero, so the numbers stay in the same two columns down a long run."""
    return f"+{adds} \u2212{dels}"


class ReadFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace folder")


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a text file from the workspace folder. Always read a file before "
        "editing it, so your edit matches the real text."
    )
    Args = ReadFileArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def approval_summary(self, args: ReadFileArgs) -> str:
        return f"Read {args.path}"

    async def execute(self, args: ReadFileArgs, ctx: ToolContext) -> ToolResult:
        cs = _changes(ctx)
        if cs is not None:
            # Read THROUGH the overlay: pending edits win over disk. Without
            # this an agent that writes a file and reads it back sees the old
            # text, decides the write failed, and rewrites it in a loop.
            text, state = cs.read(args.path)
        else:
            p = safe_path(ctx.workspace_root, args.path)
            state = "disk" if p.is_file() else "missing"
            text = None
            if state == "disk":
                try:
                    text = p.read_bytes()[:MAX_READ_BYTES].decode("utf-8")
                except UnicodeDecodeError:
                    state = "binary"

        if state == "missing":
            # THE RECOVERY INSTRUCTION LIVES ON THE ERROR, not only in the
            # system prompt. A miss is exactly the moment the model decides what
            # to do next, and a bare "No such file" reads as a dead end — the
            # observed failure was the model guessing a second path, missing
            # again, and then asking the USER where their CSS lived, with a
            # search tool sitting unused the whole time.
            return ToolResult(
                ok=False, summary="not found", detail=args.path.rsplit("/", 1)[-1],
                content=(f"No such file: {args.path}. Do NOT guess another path and do NOT ask "
                         "the user where it is — call find_files (e.g. pattern '*.css') or "
                         "search_files to locate it yourself, then read what you find."),
            )
        if state == "binary" or text is None:
            return ToolResult(ok=False, content=f"{args.path} is not a text file.", summary="binary file")

        truncated = ""
        if len(text) > MAX_READ_BYTES:
            text = text[:MAX_READ_BYTES]
            truncated = "\n(truncated)"
        note = " (including your pending edits)" if state == "staged" else ""
        return ToolResult(
            ok=True,
            content=f"Contents of {args.path}{note}:\n```\n{text}\n```{truncated}",
            summary=f"Read {args.path.rsplit('/', 1)[-1]}", detail=f"{len(text.splitlines())} lines",
        )


class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="Directory relative to the workspace folder")


class ListDirTool(Tool):
    name = "list_files"
    description = (
        "List what is inside a FOLDER of the workspace (use '.' for the top "
        "level). Takes a folder, never a file — to see what is in a file, use "
        "read_file."
    )
    Args = ListDirArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def approval_summary(self, args: ListDirArgs) -> str:
        return f"List files in {args.path}"

    async def execute(self, args: ListDirArgs, ctx: ToolContext) -> ToolResult:
        p = safe_path(ctx.workspace_root, args.path)
        if not p.is_dir():
            # POINTING A FILE AT THE WRONG TOOL IS ITS OWN ERROR.
            #
            # Observed: the model called list_files on 'app/static/login.css',
            # got "No such folder", and concluded the file did not exist — then
            # asked the user to confirm it was there. The path was right and the
            # file was right in front of it; only the tool was wrong. A generic
            # not-found sends the model looking for something it has already
            # found.
            if p.is_file():
                return ToolResult(
                    ok=False, summary="that's a file", detail=args.path.rsplit("/", 1)[-1],
                    content=(f"{args.path} is a file, not a folder — it exists. "
                             "Call read_file on it to see its contents."),
                )
            return ToolResult(
                ok=False, summary="not found", detail=args.path,
                content=(f"No such folder: {args.path}. Use find_files to locate what you are "
                         "after instead of guessing folder names."),
            )
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))[:200]
        lines = [f"{'[dir] ' if e.is_dir() else ''}{e.name}"
                 for e in entries if e.name not in SKIP_DIRS]

        # Files that exist only as pending creates are invisible to iterdir(),
        # so list them too — same reasoning as the read overlay.
        cs = _changes(ctx)
        if cs is not None:
            prefix = "" if args.path in (".", "") else args.path.strip("/") + "/"
            existing = {e.name for e in entries}
            for change in cs.summary(include_diff=False):
                rel = change["path"]
                if change["kind"] == "create" and rel.startswith(prefix):
                    tail = rel[len(prefix):]
                    if "/" not in tail and tail not in existing:
                        lines.append(f"{tail} (pending, not yet applied)")

        return ToolResult(ok=True, content="\n".join(lines) or "(empty)",
                          summary=f"Listed {args.path}", detail=f"{len(lines)} entries")


class WriteFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace folder")
    content: str = Field(max_length=400_000)


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new file, or replace an existing file's entire contents. "
        "The change is staged for the user to review as a diff — it does not "
        "touch their disk yet, so make the edit and keep going. For a small "
        "change to an existing file, prefer edit_file."
    )
    Args = WriteFileArgs
    risk = Risk.SAFE  # stages only; the gate is the diff review, not this call
    modes = {TaskMode.CODE}

    def approval_summary(self, args: WriteFileArgs) -> str:
        preview = args.content[:400] + ("…" if len(args.content) > 400 else "")
        return f"Stage {len(args.content)} chars to {args.path}\n---\n{preview}"

    async def execute(self, args: WriteFileArgs, ctx: ToolContext) -> ToolResult:
        cs = _changes(ctx)
        if cs is None:
            return ToolResult(ok=False, content=_NO_CHANGESET, summary="staging unavailable")
        change = cs.stage_write(args.path, args.content)
        adds, dels = change.stats()
        verb = {"create": "Created", "modify": "Rewrote", "delete": "Emptied"}[change.kind]
        return ToolResult(
            ok=True,
            content=f"{verb} {args.path} (+{adds}/-{dels}), staged for review.",
            summary=f"{verb} {args.path.rsplit('/', 1)[-1]}", detail=_counts(adds, dels),
        )


class EditFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace folder")
    old_text: str = Field(
        min_length=1, max_length=200_000,
        description="Exact text to find, copied verbatim from the file including indentation",
    )
    new_text: str = Field(
        max_length=200_000, description="Text to put in its place. Empty string deletes it.",
    )
    replace_all: bool = Field(
        default=False, description="Replace every occurrence instead of requiring a unique match",
    )


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Change part of an existing file by replacing an exact snippet. "
        "old_text must appear EXACTLY once (copy it verbatim from read_file, "
        "with the surrounding lines needed to make it unique) unless you set "
        "replace_all. Staged for review like write_file."
    )
    Args = EditFileArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def approval_summary(self, args: EditFileArgs) -> str:
        return f"Edit {args.path}"

    async def execute(self, args: EditFileArgs, ctx: ToolContext) -> ToolResult:
        cs = _changes(ctx)
        if cs is None:
            return ToolResult(ok=False, content=_NO_CHANGESET, summary="staging unavailable")

        text, state = cs.read(args.path)
        if state == "missing":
            return ToolResult(
                ok=False, summary="not found", detail=args.path.rsplit("/", 1)[-1],
                content=(f"No such file: {args.path}. If it should already exist, find it with "
                         "find_files or search_files rather than guessing. If it is genuinely "
                         "new, use write_file to create it."),
            )
        if text is None:
            return ToolResult(ok=False, content=f"{args.path} is not a text file.", summary="binary file")

        count = text.count(args.old_text)
        # THE UNIQUENESS RULE IS THE SAFETY PROPERTY.
        #
        # A model asked to fix "the second `return None`" cannot see line
        # numbers reliably and will happily edit the first one. Demanding a
        # unique match makes that class of mistake IMPOSSIBLE to express: the
        # edit either identifies exactly one place or it is refused. The error
        # goes back as a tool result, so the model simply retries with more
        # surrounding context — which is precisely the fix.
        if count == 0:
            return ToolResult(
                ok=False, summary="no match",
                content=(f"old_text was not found in {args.path}. Re-read the file and copy the "
                         "snippet exactly, including indentation and line breaks."),
            )
        if count > 1 and not args.replace_all:
            return ToolResult(
                ok=False, summary=f"{count} matches",
                content=(f"old_text appears {count} times in {args.path}. Include more surrounding "
                         "lines so it matches exactly one place, or set replace_all=true."),
            )

        updated = (text.replace(args.old_text, args.new_text) if args.replace_all
                   else text.replace(args.old_text, args.new_text, 1))
        change = cs.stage_write(args.path, updated)
        adds, dels = change.stats()
        where = f"{count} places" if args.replace_all else "1 place"
        return ToolResult(
            ok=True,
            content=f"Edited {args.path} in {where} (+{adds}/-{dels}), staged for review.",
            summary=f"Edited {args.path.rsplit('/', 1)[-1]}", detail=_counts(adds, dels),
        )


class ApplyPatchArgs(BaseModel):
    patch: str = Field(
        min_length=1, max_length=400_000,
        description="A patch in the *** Begin Patch / *** Update File: ... format",
    )


class ApplyPatchTool(Tool):
    name = "apply_patch"
    # The format example lives here rather than in the mode prompt because it is
    # only paid for when apply_patch is actually granted, and it cannot drift
    # away from the parser that reads it.
    description = (
        "Change parts of one or more long files without reprinting them. Format:\n"
        "*** Update File: path\n@@\n unchanged line\n-removed\n+added\n"
        "Also '*** Add File: path' with '+' lines, and '*** Delete File: path'. "
        "Context must match exactly, and match one place only."
    )
    Args = ApplyPatchArgs
    risk = Risk.SAFE          # stages only; the gate is the diff, as with every write
    modes = {TaskMode.CODE}

    def approval_summary(self, args: ApplyPatchArgs) -> str:
        return f"Apply a patch ({len(args.patch.splitlines())} lines)"

    async def execute(self, args: ApplyPatchArgs, ctx: ToolContext) -> ToolResult:
        cs = _changes(ctx)
        if cs is None:
            return ToolResult(ok=False, content=_NO_CHANGESET, summary="staging unavailable")

        try:
            ops = parse_patch(args.patch)
        except PatchError as e:
            return ToolResult(ok=False, content=f"apply_patch: {e}", summary="bad patch")

        # EVERYTHING IS COMPUTED BEFORE ANYTHING IS STAGED.
        #
        # A patch that half-applies is worse than one that fails: the file is
        # left in a state neither the user nor the model asked for, and the
        # model's next move is reasoning about a version that never existed.
        # So the whole patch is resolved against current content first, and one
        # failure anywhere abandons all of it.
        planned: list[tuple[str, str, str | None]] = []   # (kind, path, new text)
        for op in ops:
            try:
                text, state = cs.read(op.path)
            except (ArthurError, ValueError, OSError) as e:
                # Containment refused the path (traversal, absolute, no folder).
                # Reported as a normal tool result rather than left to raise: the
                # model can act on a sentence, and a crash here would read to the
                # user as Arthur breaking rather than Arthur refusing. Nothing is
                # staged until the whole patch resolves, so bailing here leaves
                # the changeset exactly as it was.
                return ToolResult(ok=False, summary="path refused",
                                  content=f"apply_patch: {e} Nothing was changed.")
            if op.kind == "add":
                if state != "missing":
                    return ToolResult(
                        ok=False, summary="already exists",
                        content=(f"apply_patch: {op.path} already exists. Use '*** Update File:' "
                                 "to change it, or pick a different name."),
                    )
                planned.append(("add", op.path, op.content))
                continue
            if state == "missing":
                return ToolResult(
                    ok=False, summary="not found",
                    content=(f"apply_patch: no such file: {op.path}. Nothing was changed. "
                             "Use find_files to locate it, or '*** Add File:' to create it."),
                )
            if op.kind == "delete":
                planned.append(("delete", op.path, None))
                continue
            if text is None:
                return ToolResult(ok=False, summary="binary file",
                                  content=f"apply_patch: {op.path} is not a text file.")
            try:
                planned.append(("update", op.path, apply_hunks(text, op.hunks, op.path)))
            except PatchError as e:
                return ToolResult(
                    ok=False, summary="patch did not match",
                    content=f"apply_patch: {e} Nothing was changed.",
                )

        adds = dels = 0
        for kind, path, new_text in planned:
            change = cs.stage_delete(path) if kind == "delete" else cs.stage_write(path, new_text)
            a, d = change.stats()
            adds += a
            dels += d

        names = ", ".join(p.rsplit("/", 1)[-1] for _k, p, _t in planned)
        return ToolResult(
            ok=True,
            content=(f"Patched {len(planned)} file(s): {names} (+{adds}/-{dels}), "
                     "staged for review."),
            summary=f"Patched {names}" if len(planned) <= 2 else f"Patched {len(planned)} files",
            detail=_counts(adds, dels),
        )


class DeleteFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace folder")


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Stage a file for deletion. Shown in the review diff before anything is removed."
    Args = DeleteFileArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def approval_summary(self, args: DeleteFileArgs) -> str:
        return f"Delete {args.path}"

    async def execute(self, args: DeleteFileArgs, ctx: ToolContext) -> ToolResult:
        cs = _changes(ctx)
        if cs is None:
            return ToolResult(ok=False, content=_NO_CHANGESET, summary="staging unavailable")
        _text, state = cs.read(args.path)
        if state == "missing":
            return ToolResult(ok=False, content=f"No such file: {args.path}", summary="not found")
        cs.stage_delete(args.path)
        return ToolResult(ok=True, content=f"Staged {args.path} for deletion.",
                          summary=f"Deleted {args.path.rsplit('/', 1)[-1]}", detail="removed")


class RunPythonArgs(BaseModel):
    code: str = Field(max_length=20_000, description="Python code to execute in the sandbox")


class RunPythonTool(Tool):
    name = "run_python"
    description = (
        "Execute Python code in an isolated sandbox (no network, no filesystem, "
        "45s limit) and return stdout/stderr. Print what you want to see."
    )
    Args = RunPythonArgs
    risk = Risk.CONFIRM  # not reversible and not previewable — keeps its dialog
    modes = {TaskMode.CODE}

    def __init__(self, sandbox: SandboxRunner):
        self._sandbox = sandbox

    def approval_summary(self, args: RunPythonArgs) -> str:
        findings = self._scan(args.code)
        head = args.code[:400] + ("…" if len(args.code) > 400 else "")
        warn = f"\n⚠ scanner findings: {', '.join(findings)}" if findings else ""
        return f"Run Python in sandbox:\n---\n{head}{warn}"

    def _scan(self, code: str) -> list[str]:
        """Cheap heuristic pass for the approval dialog (CodeShield's async
        scan runs in execute())."""
        return [pat for pat in _SUSPICIOUS if pat in code]

    async def execute(self, args: RunPythonArgs, ctx: ToolContext) -> ToolResult:
        # Checked HERE rather than by hiding the tool, so the model can say
        # something true to the user instead of quietly pretending it never had
        # the option. Code mode itself no longer requires Docker — only this.
        if not await self._sandbox.is_available():
            return ToolResult(
                ok=False, summary="sandbox unavailable",
                content=("Docker is not running, so code cannot be executed safely. "
                         "Everything else in Code mode still works — tell the user they "
                         "can start Docker Desktop if they want code run."),
            )

        try:
            from codeshield.cs import CodeShield

            result = await CodeShield.scan_code(args.code)
            if result.is_insecure and str(getattr(result, "recommended_treatment", "")) == "block":
                return ToolResult(ok=False, summary="blocked by CodeShield",
                                  content="CodeShield classified this code as insecure; it was not run.")
        except ImportError:
            pass  # sandbox still contains it; heuristics already shown at approval

        await self._sandbox.ensure_image(CODE_IMAGE, "code.Dockerfile")
        res = await self._sandbox.run(CODE_IMAGE, [], stdin_data=args.code,
                                      network="none", timeout_s=45)
        if res.timed_out:
            return ToolResult(ok=False, content="Execution timed out after 45s.", summary="timeout")
        out = f"exit code: {res.exit_code}\nstdout:\n{res.stdout or '(empty)'}"
        if res.stderr:
            out += f"\nstderr:\n{res.stderr}"
        return ToolResult(ok=res.exit_code == 0, content=out,
                          summary="Ran Python", detail=f"exit {res.exit_code}")
