"""Coding assistant tools: file read/write inside ONE user-chosen workspace
folder, plus sandboxed Python execution.

PATH TRAVERSAL DEFENSE (`_safe_path`) — the classic attack is `../../../
Users/you/.ssh/id_rsa` or an absolute path. Defense in two layers: reject
absolute-SHAPED paths under both OS conventions up front, then join to the
workspace root, `resolve()` to collapse `..` and symlinks into a real
absolute path, and require the result to still be inside the root. Checking
AFTER resolve() is the whole trick — string prefix checks before resolution
are bypassable.

RISK SPLIT — reads/listing are SAFE (workspace only, reversible); writes are
CONFIRM with a content preview; execution is CONFIRM plus a CodeShield (or
fallback regex) scan whose findings are shown IN the approval dialog, so the
human decides with the warnings in front of them.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, Field

from core.errors import PathTraversalError
from sandbox.runner import SandboxRunner
from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

CODE_IMAGE = "arthur-code:1"
MAX_READ_BYTES = 200_000

# Fallback patterns when CodeShield isn't installed. The sandbox (no network,
# read-only fs) is the real containment; this is a human-facing heads-up.
_SUSPICIOUS = [
    "os.system", "subprocess", "socket.", "shutil.rmtree", "eval(", "exec(",
    "__import__", "ctypes", "urllib.request", "requests.",
]


def _safe_path(workspace_root: str | None, relative: str) -> Path:
    if not workspace_root:
        # The signpost has to be RIGHT. This used to say "Settings → Workspace",
        # and there is no Workspace tab -- so the one piece of guidance a user
        # got when Code mode refused to work pointed at a screen that does not
        # exist. The folder is now chosen in Code mode itself.
        raise PathTraversalError(
            "No folder is set for this chat. Choose one from the folder bar at the top of Code mode."
        )
    # Reject absolute-shaped paths under BOTH OS conventions before touching
    # the filesystem. Relying only on join+resolve is platform-dependent: on
    # Linux "C:\..." is just a weird filename; on Windows it would replace the
    # root. Explicit rejection fails the same way everywhere.
    win = PureWindowsPath(relative)
    if PurePosixPath(relative).is_absolute() or win.is_absolute() or win.drive:
        raise PathTraversalError(f"Absolute paths are not allowed: {relative!r}")
    root = Path(workspace_root).resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise PathTraversalError(f"Path escapes the workspace folder: {relative!r}")
    if ".git" in candidate.parts:
        raise PathTraversalError("Direct writes into .git are not allowed.")
    return candidate


class ReadFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace folder")


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a text file from the user's workspace folder."
    Args = ReadFileArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def approval_summary(self, args: ReadFileArgs) -> str:
        return f"Read {args.path}"

    async def execute(self, args: ReadFileArgs, ctx: ToolContext) -> ToolResult:
        p = _safe_path(ctx.workspace_root, args.path)
        if not p.is_file():
            return ToolResult(ok=False, content=f"No such file: {args.path}", summary="not found")
        data = p.read_bytes()[:MAX_READ_BYTES]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"{args.path} is not a text file.", summary="binary file")
        return ToolResult(ok=True, content=f"Contents of {args.path}:\n```\n{text}\n```", summary=f"read {args.path}")


class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="Directory relative to the workspace folder")


class ListDirTool(Tool):
    name = "list_files"
    description = "List files and folders in the workspace."
    Args = ListDirArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def approval_summary(self, args: ListDirArgs) -> str:
        return f"List files in {args.path}"

    async def execute(self, args: ListDirArgs, ctx: ToolContext) -> ToolResult:
        p = _safe_path(ctx.workspace_root, args.path)
        if not p.is_dir():
            return ToolResult(ok=False, content=f"No such folder: {args.path}", summary="not found")
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))[:200]
        lines = [f"{'[dir] ' if e.is_dir() else ''}{e.name}" for e in entries if e.name != ".git"]
        return ToolResult(ok=True, content="\n".join(lines) or "(empty)", summary=f"{len(lines)} entries")


class WriteFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace folder")
    content: str = Field(max_length=400_000)


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a text file in the workspace folder."
    Args = WriteFileArgs
    risk = Risk.CONFIRM  # destructive: can overwrite the user's work
    modes = {TaskMode.CODE}

    def approval_summary(self, args: WriteFileArgs) -> str:
        preview = args.content[:400] + ("…" if len(args.content) > 400 else "")
        return f"Write {len(args.content)} chars to {args.path}\n---\n{preview}"

    async def execute(self, args: WriteFileArgs, ctx: ToolContext) -> ToolResult:
        p = _safe_path(ctx.workspace_root, args.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(args.content, encoding="utf-8")
        return ToolResult(ok=True, content=f"{'Overwrote' if existed else 'Created'} {args.path}.",
                          summary=f"wrote {args.path}")


class RunPythonArgs(BaseModel):
    code: str = Field(max_length=20_000, description="Python code to execute in the sandbox")


class RunPythonTool(Tool):
    name = "run_python"
    description = (
        "Execute Python code in an isolated sandbox (no network, no filesystem, "
        "45s limit) and return stdout/stderr. Print what you want to see."
    )
    Args = RunPythonArgs
    risk = Risk.CONFIRM
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
                          summary=f"exit {res.exit_code}")
