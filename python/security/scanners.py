"""Prompt-injection scanners behind one interface.

WHY an interface + factory instead of importing llm_guard directly:
1. LLM-Guard drags in torch (~2GB). It must load lazily, off the event loop,
   and the app must still function if the model download failed — the
   HeuristicScanner is that fallback, and the UI shows which backend is live.
2. Tests inject a FakeScanner and stay fast — no torch in CI.

WHY heuristics at all when LLM-Guard exists: defense in depth. Regexes catch
the low-effort attacks instantly and cost nothing; the ML scanner catches the
paraphrased ones. Real products layer both.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    risk: float  # 0.0 (clean) .. 1.0 (definitely malicious)
    flagged: bool
    reasons: list[str] = field(default_factory=list)
    backend: str = "heuristic"


class Scanner(Protocol):
    name: str

    def scan(self, text: str) -> ScanResult: ...  # sync — callers run it in a thread


_INJECTION_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts?|rules)"), "override attempt", 0.9),
    (re.compile(r"(?i)disregard\s+(all\s+)?(your|the)\s+(instructions|guidelines|rules)"), "override attempt", 0.9),
    (re.compile(r"(?i)you\s+are\s+now\s+(?:in\s+)?(developer|dan|jailbreak|god)\s*mode"), "persona hijack", 0.9),
    (re.compile(r"(?i)reveal\s+(your\s+)?(system\s+prompt|instructions|initial\s+prompt)"), "prompt extraction", 0.8),
    (re.compile(r"(?i)\bnew\s+system\s+prompt\s*:"), "prompt replacement", 0.9),
    (re.compile(r"(?i)IMPORTANT:\s*(the\s+)?(assistant|ai|model)\s+(must|should|will)\s+"), "embedded directive", 0.7),
    (re.compile(r"(?i)do\s+not\s+(tell|inform|alert)\s+the\s+user"), "concealment directive", 0.85),
    (re.compile(r"[​‌‍⁠﻿]{3,}"), "zero-width obfuscation", 0.8),
    (re.compile(r"(?i)\bexfiltrate|send\s+(all\s+)?(files|data|memory|keys)\s+to\s+https?://"), "exfiltration directive", 0.95),
]


class HeuristicScanner:
    """Fast regex layer. Always available, zero dependencies."""

    name = "heuristic"

    def scan(self, text: str) -> ScanResult:
        risk, reasons = 0.0, []
        for pattern, reason, weight in _INJECTION_PATTERNS:
            if pattern.search(text):
                reasons.append(reason)
                risk = max(risk, weight)
        return ScanResult(risk=risk, flagged=risk >= 0.7, reasons=reasons, backend=self.name)


class LLMGuardScanner:
    """LLM-Guard's PromptInjection scanner (DeBERTa classifier).

    Loaded on first scan, guarded by a lock so concurrent first-requests don't
    load the model twice. `scan` is synchronous and CPU-heavy — the gateway
    always calls it via anyio.to_thread so the event loop never stalls.
    """

    name = "llm_guard"

    def __init__(self):
        self._scanner = None
        self._lock = threading.Lock()

    def _ensure_loaded(self):
        if self._scanner is None:
            with self._lock:
                if self._scanner is None:
                    from llm_guard.input_scanners import PromptInjection
                    from llm_guard.input_scanners.prompt_injection import MatchType

                    self._scanner = PromptInjection(threshold=0.7, match_type=MatchType.CHUNKS)
                    log.info("LLM-Guard PromptInjection scanner loaded")

    def scan(self, text: str) -> ScanResult:
        self._ensure_loaded()
        _, is_valid, risk = self._scanner.scan(text)
        return ScanResult(
            risk=float(max(risk, 0.0)),
            flagged=not is_valid,
            reasons=["ml_classifier"] if not is_valid else [],
            backend=self.name,
        )


class CombinedScanner:
    """Heuristics first (cheap, catches obvious), ML second. Max of both risks."""

    name = "combined"

    def __init__(self, ml: Scanner):
        self._heuristic = HeuristicScanner()
        self._ml = ml

    def scan(self, text: str) -> ScanResult:
        h = self._heuristic.scan(text)
        if h.risk >= 0.9:  # already certain — skip the expensive pass
            return h
        m = self._ml.scan(text)
        if m.risk >= h.risk:
            m.reasons = h.reasons + m.reasons
            return m
        return h


def build_scanner(backend: str) -> Scanner:
    """`auto` tries LLM-Guard and falls back to heuristics if it can't load
    (not installed / model files missing / first-run offline)."""
    if backend == "heuristic":
        return HeuristicScanner()
    if backend in ("llm_guard", "auto", "combined"):
        try:
            import llm_guard  # noqa: F401  — probe the import before committing

            return CombinedScanner(LLMGuardScanner())
        except Exception as e:
            if backend == "llm_guard":
                raise
            log.warning("LLM-Guard unavailable (%s); using heuristic scanner", e)
            return HeuristicScanner()
    return HeuristicScanner()


# ---- secrets redaction (output side) ----
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"tvly-[A-Za-z0-9-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact_secrets(text: str) -> tuple[str, int]:
    """Returns (redacted_text, hit_count). Applied to everything the model
    emits and every tool result — an injected page must not be able to make
    Arthur repeat a key it saw in context."""
    hits = 0
    for pat in _SECRET_PATTERNS:
        text, n = pat.subn("[REDACTED-SECRET]", text)
        hits += n
    return text, hits
