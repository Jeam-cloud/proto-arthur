"""Workspace search: find files by name pattern, and find text inside them.

WHY THIS EXISTS
---------------
Without search, an agent navigates a project by listing one directory at a time
and guessing filenames. On anything bigger than a toy that is hopeless — and
worse, it is hopeless EXPENSIVELY: every guess is a tool call, and tool calls
are the budget the whole turn runs on.

Search is what turns "read the tree until you find it" into one call. It is the
difference between the agent asking *where is the login handler* and the agent
asking *is it maybe in auth.py? no. api.py? no. routes.py?*

WHY PURE PYTHON AND NOT ripgrep
-------------------------------
Shelling out to `rg` would be faster and is what a developer would reach for.
But it means a binary that must exist on the user's machine, a subprocess
boundary the security model would have to reason about, and a Windows-first app
inheriting a dependency it cannot guarantee. Walking the tree in Python is
fast enough for the sizes involved once the skip list does its job, and it keeps
Code mode's file access flowing through exactly one containment check.

BOTH TOOLS ARE Risk.SAFE — read-only, workspace-scoped, no side effects.

THE REAL DESIGN PROBLEM HERE IS OUTPUT SIZE
-------------------------------------------
A naive grep for `def ` in a Python project returns thousands of lines and
blows the context window, which does not fail loudly — it silently truncates
the conversation and the model starts forgetting what it was doing. So every
limit below (files scanned, matches returned, matches per file, line length) is
load-bearing, and hitting one is always REPORTED rather than silently applied.
An agent that knows it saw a partial answer can narrow the search; an agent that
thinks it saw everything draws a wrong conclusion with confidence.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from coding.paths import SKIP_DIRS, safe_path
from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

MAX_FILES_SCANNED = 4_000     # ceiling on the walk itself
MAX_MATCHES = 100             # total match lines returned
MAX_MATCHES_PER_FILE = 5      # so one generated file can't crowd out the rest
MAX_LINE_CHARS = 240          # a minified bundle is one 2MB "line"
MAX_FILE_BYTES = 2_000_000    # skip anything implausibly large for source
MAX_NAME_RESULTS = 200

# Extensions worth reading as text. An allow-list rather than a deny-list: new
# binary formats appear constantly, new source formats rarely, so guessing wrong
# on the allow-list costs a missed match while guessing wrong on a deny-list
# costs a screenful of decoded PNG in the model's context.
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".md", ".mdx", ".rst", ".txt",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".java", ".kt", ".go", ".rs",
    ".rb", ".php", ".swift", ".m", ".scala", ".sh", ".bash", ".zsh", ".ps1",
    ".bat", ".sql", ".graphql", ".proto", ".dockerfile", ".gitignore", ".xml",
    ".csv", ".tsv", ".lock", ".spec",
}
# Files with no suffix that are still text and often worth searching.
TEXT_STEMS = {"Dockerfile", "Makefile", "LICENSE", "README", "CHANGELOG", "Procfile"}


def _is_texty(p: Path) -> bool:
    return p.suffix.lower() in TEXT_SUFFIXES or p.name in TEXT_STEMS


def _walk(root: Path) -> tuple[list[Path], bool]:
    """Every candidate file under root, skipping the noise directories.

    Returns (files, truncated). Pruning SKIP_DIRS in place matters: on a real
    project `node_modules` is 90% of the filesystem and 0% of the answer, and
    walking it would exhaust MAX_FILES_SCANNED before reaching any source.
    """
    files: list[Path] = []
    # os.walk rather than Path.walk: the latter is 3.12+ and this app targets
    # 3.11. Mutating `dirnames` in place is what prunes the walk — assigning a
    # new list would not, os.walk only honours edits to the SAME list object.
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            files.append(Path(dirpath) / name)
            if len(files) >= MAX_FILES_SCANNED:
                return files, True
    return files, False


def _root_of(ctx: ToolContext) -> Path:
    """Workspace root as a Path.

    Pulled out of the async tool bodies so the blocking pathlib construction
    isn't sitting inside a coroutine (ruff ASYNC240). The filesystem work here
    is fast and local; if search ever grows slow enough to stall the event loop
    it should move to a thread wholesale, not be papered over call by call.
    """
    return Path(ctx.workspace_root).resolve()


class FindFilesArgs(BaseModel):
    pattern: str = Field(
        min_length=1, max_length=200,
        description="Glob-style name pattern, e.g. '*.py', 'test_*.py', 'ChangesPanel.jsx'",
    )
    path: str = Field(default=".", description="Folder to search under, relative to the workspace")


class FindFilesTool(Tool):
    name = "find_files"
    description = (
        "Find files by NAME anywhere in the workspace using a glob pattern "
        "(e.g. '*.jsx', 'test_*.py'). Use this to locate a file when you know "
        "roughly what it is called; use search_files to find text inside files."
    )
    Args = FindFilesArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def approval_summary(self, args: FindFilesArgs) -> str:
        return f"Find files matching {args.pattern}"

    async def execute(self, args: FindFilesArgs, ctx: ToolContext) -> ToolResult:
        base = safe_path(ctx.workspace_root, args.path)
        if not base.is_dir():
            return ToolResult(ok=False, content=f"No such folder: {args.path}", summary="not found")

        root = _root_of(ctx)
        files, walk_truncated = _walk(base)
        # Match on the FULL relative path as well as the bare name, so both
        # '*.jsx' and 'components/code/*.jsx' behave the way a person expects.
        hits = []
        for f in files:
            rel = f.relative_to(root).as_posix()
            if fnmatch.fnmatch(f.name, args.pattern) or fnmatch.fnmatch(rel, args.pattern):
                hits.append(rel)

        if not hits:
            return ToolResult(
                ok=True, summary=f"Found files matching {args.pattern!r}", detail="none",
                content=(f"No files match {args.pattern!r} under {args.path}. "
                         "Try a broader pattern, or list_files to see what is there."),
            )

        hits.sort()
        clipped = len(hits) > MAX_NAME_RESULTS
        shown = hits[:MAX_NAME_RESULTS]
        notes = []
        if clipped:
            notes.append(f"showing the first {MAX_NAME_RESULTS} of {len(hits)}")
        if walk_truncated:
            notes.append(f"stopped after scanning {MAX_FILES_SCANNED} files")
        tail = f"\n({'; '.join(notes)})" if notes else ""
        return ToolResult(
            ok=True, summary=f"Found files matching {args.pattern!r}", detail=f"{len(hits)} files",
            content=f"{len(hits)} match {args.pattern!r}:\n" + "\n".join(shown) + tail,
        )


class SearchFilesArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="Text or regular expression to find")
    path: str = Field(default=".", description="Folder to search under, relative to the workspace")
    file_pattern: str = Field(
        default="", max_length=200,
        description="Optional glob to limit which files are searched, e.g. '*.py'",
    )
    regex: bool = Field(default=False, description="Treat query as a regular expression")
    case_sensitive: bool = Field(default=False)


class SearchFilesTool(Tool):
    name = "search_files"
    description = (
        "Search the CONTENTS of files in the workspace and return matching lines "
        "with their file and line number. This is the fastest way to find where "
        "something is defined or used — prefer it over reading files one by one."
    )
    Args = SearchFilesArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def approval_summary(self, args: SearchFilesArgs) -> str:
        return f"Search for {args.query!r}"

    async def execute(self, args: SearchFilesArgs, ctx: ToolContext) -> ToolResult:
        base = safe_path(ctx.workspace_root, args.path)
        if not base.is_dir():
            return ToolResult(ok=False, content=f"No such folder: {args.path}", summary="not found")

        flags = 0 if args.case_sensitive else re.IGNORECASE
        try:
            # A plain query is escaped, so a search for "cost: $5.00 (net)" is a
            # search for that text and not a regex that fails to compile. The
            # model opts into regex explicitly; it does not get it by accident
            # because its query happened to contain a bracket.
            pattern = re.compile(args.query if args.regex else re.escape(args.query), flags)
        except re.error as e:
            return ToolResult(ok=False, summary="bad regex",
                              content=f"That regular expression is invalid: {e}. Retry with regex=false "
                                      "to search for it as plain text.")

        root = _root_of(ctx)
        files, walk_truncated = _walk(base)

        # Search reads through the changeset for exactly the reason read_file
        # does: the agent must find text it just wrote, and must not find text
        # it just deleted. `overlay` maps relative path -> staged text (None =
        # staged for deletion).
        overlay = ctx.changes.staged_contents() if ctx.changes is not None else {}
        prefix = "" if args.path in (".", "") else args.path.strip("/") + "/"
        on_disk = {f.relative_to(root).as_posix() for f in files}
        for rel in overlay:
            # Staged CREATES have no file to walk to, so add them by hand —
            # scoped to the folder being searched, like everything else.
            if rel not in on_disk and overlay[rel] is not None and rel.startswith(prefix):
                files.append(root / rel)

        lines_out: list[str] = []
        files_with_hits = 0
        total_hits = 0
        hit_cap_reached = False

        for f in sorted(files):
            if not _is_texty(f):
                continue
            rel = f.relative_to(root).as_posix()
            if args.file_pattern and not (
                fnmatch.fnmatch(f.name, args.file_pattern)
                or fnmatch.fnmatch(rel, args.file_pattern)
            ):
                continue
            if rel in overlay:
                if overlay[rel] is None:
                    continue          # staged for deletion: already gone, as far as the agent knows
                text = overlay[rel]
            else:
                try:
                    if f.stat().st_size > MAX_FILE_BYTES:
                        continue
                    text = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError, ValueError):
                    continue  # unreadable or not really text; a search is not the place to complain

            in_file = 0
            for n, line in enumerate(text.splitlines(), 1):
                if not pattern.search(line):
                    continue
                in_file += 1
                total_hits += 1
                if in_file == 1:
                    files_with_hits += 1
                if in_file > MAX_MATCHES_PER_FILE:
                    continue  # keep counting for the report, stop printing
                snippet = line.strip()
                if len(snippet) > MAX_LINE_CHARS:
                    snippet = snippet[:MAX_LINE_CHARS] + "…"
                lines_out.append(f"{rel}:{n}: {snippet}")
                if len(lines_out) >= MAX_MATCHES:
                    hit_cap_reached = True
                    break
            if hit_cap_reached:
                break

        if not total_hits:
            scope = f" in {args.file_pattern}" if args.file_pattern else ""
            return ToolResult(
                ok=True, summary=f"Searched for {args.query!r}", detail="no matches",
                content=(f"No matches for {args.query!r}{scope} under {args.path}. "
                         "Try a shorter or more general query, or find_files to check the "
                         "file is where you think it is."),
            )

        # LIMITS ARE ALWAYS STATED. An agent that knows it saw a partial answer
        # narrows the search; one that thinks it saw everything concludes the
        # thing does not exist and confidently acts on that.
        notes = []
        if hit_cap_reached:
            notes.append(f"stopped at {MAX_MATCHES} matches — narrow the query for the rest")
        elif total_hits > len(lines_out):
            notes.append(f"{total_hits - len(lines_out)} more matches hidden "
                         f"(max {MAX_MATCHES_PER_FILE} shown per file)")
        if walk_truncated:
            notes.append(f"stopped after scanning {MAX_FILES_SCANNED} files")
        tail = f"\n({'; '.join(notes)})" if notes else ""

        head = (f"{total_hits} match{'es' if total_hits != 1 else ''} for {args.query!r} "
                f"in {files_with_hits} file{'s' if files_with_hits != 1 else ''}:")
        return ToolResult(
            ok=True, summary=f"Searched for {args.query!r}",
            detail=f"{total_hits} in {files_with_hits} file{'s' if files_with_hits != 1 else ''}",
            content=f"{head}\n" + "\n".join(lines_out) + tail,
        )
