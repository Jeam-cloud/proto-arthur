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

    IF THE LOAD FAILS IT DEGRADES, it does not raise. The module header promises
    "the app must still function if the model download failed", and until now it
    did not: build_scanner's probe only proved the package was *importable*, and
    the download/model-file failure happens later, here, on first scan — where
    nothing caught it and it surfaced as a broken user message instead. One
    failed load switches this instance to heuristics for the life of the
    process; a scanner that raises is worse than a weaker scanner that answers,
    because every path through the gateway depends on getting an answer.
    """

    name = "llm_guard"

    def __init__(self):
        self._scanner = None
        self._lock = threading.Lock()
        # Set once if loading fails; scans then run on this instead.
        self._fallback: HeuristicScanner | None = None

    def _ensure_loaded(self) -> bool:
        """True if the ML scanner is usable, False if we've fallen back."""
        if self._scanner is not None:
            return True
        if self._fallback is not None:
            return False
        with self._lock:
            if self._scanner is not None:
                return True
            if self._fallback is not None:
                return False
            try:
                from llm_guard.input_scanners import PromptInjection
                from llm_guard.input_scanners.prompt_injection import MatchType

                self._scanner = PromptInjection(threshold=0.7, match_type=MatchType.CHUNKS)
                log.info("LLM-Guard PromptInjection scanner loaded")
                return True
            except Exception as e:
                # Logged at error, not warning: the app keeps working but with
                # materially weaker injection detection, and that is a fact the
                # operator should be able to find in the log.
                log.error(
                    "LLM-Guard failed to load (%s); falling back to heuristics "
                    "for the rest of this session", e,
                )
                self._fallback = HeuristicScanner()
                return False

    def scan(self, text: str) -> ScanResult:
        if not self._ensure_loaded():
            return self._fallback.scan(text)
        try:
            _, is_valid, risk = self._scanner.scan(text)
        except Exception as e:
            # A model that loaded but throws mid-scan gets the same treatment:
            # answer with the heuristics rather than failing the request.
            log.error("LLM-Guard scan failed (%s); using heuristics for this scan", e)
            return HeuristicScanner().scan(text)
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
    (not installed / model files missing / first-run offline).

    THE AVAILABILITY CHECK MUST NOT IMPORT THE PACKAGE. This used to be a real
    `import llm_guard`, done here purely to find out whether the package
    existed — which executed llm_guard's __init__ and pulled in torch and
    transformers, ~2GB of native libraries, before this function returned.

    That single line undid the lazy loading this whole module is built around
    (see the header, and LLMGuardScanner._ensure_loaded), and it did it in the
    worst possible place: build_scanner runs inside build_state, which runs
    inside FastAPI's lifespan, and uvicorn does not accept a single connection
    until lifespan startup returns. So /health could not answer until torch had
    finished importing. On a cold first launch — nothing in the OS file cache —
    that is often over a minute; on the next launch the same import is a few
    seconds because Windows still has the DLLs cached. Which is exactly the
    "first boot fails, second boot works" symptom.

    find_spec answers the same question (is it installed?) by looking at the
    module finder, without executing anything.
    """
    if backend == "heuristic":
        return HeuristicScanner()
    if backend in ("llm_guard", "auto", "combined"):
        from importlib.util import find_spec

        try:
            found = find_spec("llm_guard") is not None
        except (ImportError, ValueError):
            # A broken/partial install can make find_spec itself raise; that is
            # an unavailable package as far as we are concerned.
            found = False
        if found:
            return CombinedScanner(LLMGuardScanner())
        if backend == "llm_guard":
            raise RuntimeError(
                "scanner_backend='llm_guard' but the llm_guard package is not installed"
            )
        log.warning("LLM-Guard not installed; using heuristic scanner")
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
