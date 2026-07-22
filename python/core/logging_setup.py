"""Logging with secret redaction.

WHY a redaction filter: logs end up in bug reports. The auth token, BYOK keys
and OAuth tokens must never appear in them, so redaction happens at the logging
layer — one choke point — instead of trusting every log call site to remember.
"""

from __future__ import annotations

import logging
import logging.handlers
import re

from core.config import Settings

# Common secret shapes: OpenAI/Anthropic/GitHub/Slack keys, bearer headers, PEM blocks.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"tvly-[A-Za-z0-9-]{16,}"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


class _RedactFilter(logging.Filter):
    def __init__(self, extra_literals: list[str]):
        super().__init__()
        self._literals = [s for s in extra_literals if s]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for lit in self._literals:
            msg = msg.replace(lit, "[REDACTED]")
        record.msg = redact(msg)
        record.args = ()
        return True


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)

    # 3 x 2MB rotating files in the app data dir — enough history for bug
    # reports without silently eating the user's disk.
    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_dir / "backend.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    redactor = _RedactFilter(extra_literals=[settings.auth_token])
    for h in (console, file_handler):
        h.addFilter(redactor)
        root.addHandler(h)

    # uvicorn access logs include URLs; keep them but quiet the noise
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
