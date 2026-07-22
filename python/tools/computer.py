"""Computer control — the one capability that CANNOT be sandboxed, so every
mutation is CONFIRM-gated and the design leans on three layers:

1. Approval dialog shows exactly what will happen ("Type 'hello' at the
   current cursor") before anything moves.
2. PyAutoGUI FAILSAFE stays ON: slam the mouse into the top-left corner and
   any in-flight automation aborts with an exception. This is the user's
   physical kill switch and must never be disabled.
3. Inputs are constrained: app names match a conservative pattern (blocks
   `cmd /c evil` smuggling), typed text is capped, key combos come from an
   allowlist. A hijacked model turn gets a narrow, human-approved needle —
   not a shell.

Screenshots are SAFE (read-only) but audited, and downscaled to keep
multimodal prompts small.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import platform
import re
import shutil
import subprocess

from pydantic import BaseModel, Field, field_validator

from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult

_APP_NAME = re.compile(r"^[\w .+&()-]{1,64}$")

_ALLOWED_KEYS = {
    "enter", "tab", "esc", "escape", "space", "backspace", "delete", "up", "down",
    "left", "right", "home", "end", "pageup", "pagedown", "f1", "f2", "f3", "f4",
    "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12", "ctrl", "alt", "shift", "win",
    "cmd", "a", "c", "v", "x", "z", "s", "f", "n", "t", "w",
}


def _pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = True  # corner-slam abort — never turn this off
    pyautogui.PAUSE = 0.05
    return pyautogui


# ---------------- app launching (the part that actually has to WORK) ----------------
#
# WHY the rework: v1 just shelled out to `start <name>`. That only launches
# things already on PATH — "file explorer" or "vscode" popped a Windows error
# dialog while the tool reported success (Popen returns before `start` fails).
# Real resolution, in order of cost:
#   1. alias table    — how humans name apps -> what Windows calls them
#   2. PATH lookup    — classic executables
#   3. App Paths reg  — where installers register GUI apps (code, chrome, ...)
#   4. Start-menu app index (Get-StartApps, cached 5 min) with fuzzy matching
#      — covers Store/Electron apps like Spotify, Discord, WhatsApp
# If nothing matches, the tool FAILS with close-match suggestions the model can
# relay — never a blind `start` that throws an OS error dialog at the user.

APP_ALIASES = {
    "file explorer": "explorer", "explorer": "explorer", "files": "explorer",
    "vscode": "code", "vs code": "code", "visual studio code": "code",
    "chrome": "chrome", "google chrome": "chrome",
    "edge": "msedge", "microsoft edge": "msedge",
    "firefox": "firefox",
    "word": "winword", "microsoft word": "winword",
    "excel": "excel", "microsoft excel": "excel",
    "powerpoint": "powerpnt", "microsoft powerpoint": "powerpnt",
    "outlook": "outlook",
    "notepad": "notepad",
    "calculator": "calc", "calc": "calc",
    "paint": "mspaint",
    "task manager": "taskmgr",
    "terminal": "wt", "windows terminal": "wt",
    "cmd": "cmd", "command prompt": "cmd",
    "powershell": "powershell",
    "settings": "ms-settings:", "windows settings": "ms-settings:",
    "snipping tool": "snippingtool",
    "control panel": "control",
}

_start_apps_cache: tuple[float, list[tuple[str, str]]] = (0.0, [])


def _get_start_apps() -> list[tuple[str, str]]:
    """[(display name, AppID)] from the Start-menu index. PowerShell call takes
    ~1s, so results are cached for 5 minutes."""
    import time

    global _start_apps_cache
    ts, cached = _start_apps_cache
    if time.time() - ts < 300 and cached:
        return cached
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
            capture_output=True, text=True, timeout=10,
        )
        apps = []
        for line in out.stdout.splitlines():
            if "|" in line:
                name, appid = line.split("|", 1)
                apps.append((name.strip(), appid.strip()))
        _start_apps_cache = (time.time(), apps)
        return apps
    except Exception:
        return cached  # stale beats nothing


def _windows_app_paths_lookup(exe: str) -> str | None:
    """Installers register GUI apps under App Paths — how the Run dialog finds
    'code' or 'chrome' when they're not on PATH."""
    try:
        import winreg
    except ImportError:  # non-Windows (dev/CI) — registry doesn't exist
        return None

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}.exe"
            ) as key:
                path, _ = winreg.QueryValueEx(key, "")
                if path:
                    return path.strip('"')
        except OSError:
            continue
    return None


def resolve_windows_app(name: str) -> tuple[str, str] | tuple[None, list[str]]:
    """-> (kind, target) where kind is 'uri' | 'exe' | 'appid',
    or (None, suggestions) when unresolvable. Pure lookup — launching is
    separate, so this is unit-testable without opening anything."""
    normalized = name.lower().strip()
    target = APP_ALIASES.get(normalized, normalized)

    if target.endswith(":"):  # ms-settings: and friends
        return ("uri", target)
    exe = shutil.which(target)
    if exe:
        return ("exe", exe)
    reg_path = _windows_app_paths_lookup(target)
    if reg_path:
        return ("exe", reg_path)

    import difflib

    apps = _get_start_apps()
    names = [n for n, _ in apps]
    match = difflib.get_close_matches(name.strip(), names, n=1, cutoff=0.6)
    # also try substring containment ("spotify" in "Spotify Music")
    if not match:
        contains = [n for n in names if normalized in n.lower()]
        match = contains[:1]
    if match:
        appid = dict(apps)[match[0]]
        return ("appid", appid)

    suggestions = difflib.get_close_matches(name.strip(), names, n=3, cutoff=0.4)
    return (None, suggestions)


class OpenAppArgs(BaseModel):
    name: str = Field(description="Application name as a person would say it, e.g. 'vscode', 'file explorer', 'chrome', 'spotify'")

    @field_validator("name")
    @classmethod
    def valid_name(cls, v: str) -> str:
        v = v.strip()
        if not _APP_NAME.match(v):
            raise ValueError("app name contains disallowed characters")
        return v


class OpenAppTool(Tool):
    name = "open_app"
    description = (
        "Open an application on the user's computer by name (e.g. 'vscode', "
        "'file explorer', 'chrome'). If it can't be found you'll get close-match "
        "suggestions to offer the user."
    )
    Args = OpenAppArgs
    risk = Risk.CONFIRM
    modes = {TaskMode.COMPUTER}

    def approval_summary(self, args: OpenAppArgs) -> str:
        return f"Open the application “{args.name}”"

    async def execute(self, args: OpenAppArgs, ctx: ToolContext) -> ToolResult:
        system = platform.system()

        def _launch() -> str:
            if system == "Windows":
                kind, target = resolve_windows_app(args.name)
                if kind is None:
                    hint = f" Did you mean: {', '.join(target)}?" if target else ""
                    raise FileNotFoundError(f"No app matching '{args.name}' found.{hint}")
                if kind == "uri":
                    os.startfile(target)  # noqa: S606 — resolver output only, never raw input
                elif kind == "appid":
                    # shell:AppsFolder launches Store/indexed apps by AppID
                    subprocess.Popen(["explorer", f"shell:AppsFolder\\{target}"])
                else:
                    subprocess.Popen([target])
                return target
            elif system == "Darwin":
                subprocess.Popen(["open", "-a", args.name])
                return args.name
            else:
                exe = shutil.which(args.name.lower())
                if not exe:
                    raise FileNotFoundError(f"'{args.name}' not found on PATH")
                subprocess.Popen([exe])
                return exe

        try:
            launched = await asyncio.to_thread(_launch)
        except Exception as e:
            return ToolResult(ok=False, content=f"Could not open {args.name}: {e}", summary="launch failed")
        return ToolResult(ok=True, content=f"Opened {args.name} ({launched}).", summary=f"opened {args.name}")


class ScreenshotArgs(BaseModel):
    pass


class ScreenshotTool(Tool):
    name = "screenshot"
    description = "Take a screenshot of the primary screen so you can see what's on it."
    Args = ScreenshotArgs
    risk = Risk.SAFE  # read-only; still logged in the security event feed
    modes = {TaskMode.COMPUTER}

    def approval_summary(self, args: ScreenshotArgs) -> str:
        return "Take a screenshot of the screen"

    async def execute(self, args: ScreenshotArgs, ctx: ToolContext) -> ToolResult:
        def _grab() -> str:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            if img.width > 1280:  # keep the multimodal prompt small
                img = img.resize((1280, int(img.height * 1280 / img.width)))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()

        try:
            b64 = await asyncio.to_thread(_grab)
        except Exception as e:
            return ToolResult(ok=False, content=f"Screenshot failed: {e}", summary="failed")
        return ToolResult(
            ok=True,
            content="Screenshot attached. Describe or act on what you can see.",
            images_b64=[b64],
            summary="screenshot captured",
        )


class ClickArgs(BaseModel):
    x: int = Field(ge=0, le=10_000)
    y: int = Field(ge=0, le=10_000)
    button: str = Field(default="left", pattern="^(left|right|double)$")


class ClickTool(Tool):
    name = "mouse_click"
    description = "Click at screen coordinates (take a screenshot first to find them)."
    Args = ClickArgs
    risk = Risk.CONFIRM
    modes = {TaskMode.COMPUTER}

    def approval_summary(self, args: ClickArgs) -> str:
        return f"{args.button.capitalize()}-click at ({args.x}, {args.y})"

    async def execute(self, args: ClickArgs, ctx: ToolContext) -> ToolResult:
        def _click():
            pg = _pyautogui()
            if args.button == "double":
                pg.doubleClick(args.x, args.y)
            elif args.button == "right":
                pg.rightClick(args.x, args.y)
            else:
                pg.click(args.x, args.y)

        try:
            await asyncio.to_thread(_click)
        except Exception as e:  # includes the FAILSAFE corner abort
            return ToolResult(ok=False, content=f"Click aborted: {e}", summary="aborted")
        return ToolResult(ok=True, content=f"Clicked at ({args.x}, {args.y}).", summary="clicked")


class TypeTextArgs(BaseModel):
    text: str = Field(max_length=500, description="Text to type at the current cursor position")


class TypeTextTool(Tool):
    name = "type_text"
    description = "Type text at the current cursor position."
    Args = TypeTextArgs
    risk = Risk.CONFIRM
    modes = {TaskMode.COMPUTER}

    def approval_summary(self, args: TypeTextArgs) -> str:
        return f"Type: “{args.text[:120]}{'…' if len(args.text) > 120 else ''}”"

    async def execute(self, args: TypeTextArgs, ctx: ToolContext) -> ToolResult:
        try:
            await asyncio.to_thread(lambda: _pyautogui().write(args.text, interval=0.01))
        except Exception as e:
            return ToolResult(ok=False, content=f"Typing aborted: {e}", summary="aborted")
        return ToolResult(ok=True, content="Text typed.", summary=f"typed {len(args.text)} chars")


class PressKeysArgs(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=3, description="Key combo, e.g. ['ctrl','s']")

    @field_validator("keys")
    @classmethod
    def allowed(cls, v: list[str]) -> list[str]:
        v = [k.lower().strip() for k in v]
        bad = [k for k in v if k not in _ALLOWED_KEYS]
        if bad:
            raise ValueError(f"keys not allowed: {bad}")
        return v


class PressKeysTool(Tool):
    name = "press_keys"
    description = "Press a keyboard shortcut like ctrl+s. Allowed keys only."
    Args = PressKeysArgs
    risk = Risk.CONFIRM
    modes = {TaskMode.COMPUTER}

    def approval_summary(self, args: PressKeysArgs) -> str:
        return f"Press {'+'.join(args.keys)}"

    async def execute(self, args: PressKeysArgs, ctx: ToolContext) -> ToolResult:
        try:
            await asyncio.to_thread(lambda: _pyautogui().hotkey(*args.keys))
        except Exception as e:
            return ToolResult(ok=False, content=f"Key press aborted: {e}", summary="aborted")
        return ToolResult(ok=True, content=f"Pressed {'+'.join(args.keys)}.", summary="keys pressed")
