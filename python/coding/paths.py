"""Workspace containment: turn a model-supplied relative path into a real
absolute path that is PROVABLY inside the user's chosen folder.

PATH TRAVERSAL DEFENSE — the classic attack is `../../../Users/you/.ssh/
id_rsa`, or simply an absolute path. Defense in two layers:

  1. Reject absolute-SHAPED paths under BOTH OS conventions up front. Relying
     only on join+resolve is platform-dependent: on Linux "C:\\evil" is just a
     weird filename, on Windows it would replace the root entirely. Explicit
     rejection fails the same way everywhere.
  2. Join to the root, `resolve()` to collapse `..` and follow symlinks into a
     real absolute path, then require the result to still be inside the root.

Checking AFTER resolve() is the whole trick. A string prefix check done first
("does it start with the root?") is bypassable, because `root/../../etc` starts
with the root as text but not as a location.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from core.errors import PathTraversalError

# Folders no agent should be rummaging through or writing into. .git is the
# dangerous one: a crafted write to .git/hooks/post-checkout is arbitrary code
# execution on the user's machine the next time they use git, which would walk
# straight around the sandbox. The rest are noise that would blow the context
# window for no benefit.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".turbo", "target",
}


def safe_path(workspace_root: str | None, relative: str) -> Path:
    if not workspace_root:
        # The signpost has to be RIGHT. This used to say "Settings -> Workspace",
        # and there is no Workspace tab -- so the one piece of guidance a user
        # got when Code mode refused to work pointed at a screen that does not
        # exist. The folder is chosen in Code mode itself.
        raise PathTraversalError(
            "No folder is set for this chat. Choose one from the folder bar at the top of Code mode."
        )
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


def rel_key(workspace_root: str, path: Path) -> str:
    """Canonical identity for a file inside the workspace: its path relative to
    the root, always with forward slashes.

    WHY a canonical key matters: the changeset is a dict keyed by path, and the
    model will refer to the same file as `src/app.py`, `./src/app.py`, and
    `src\\app.py` across a single conversation. Without normalisation those are
    three separate pending edits to one file, and the last apply silently wins.
    """
    return path.resolve().relative_to(Path(workspace_root).resolve()).as_posix()
