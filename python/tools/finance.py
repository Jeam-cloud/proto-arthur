"""Finance tools: yfinance inside the finance sandbox, wrapped in a cache and
a circuit breaker.

WHY both wrappers: yfinance is an UNOFFICIAL client of endpoints Yahoo can
change any day (the README is honest about this). The cache (15 min) avoids
hammering Yahoo for repeated questions about the same ticker; the circuit
breaker stops a broken yfinance from adding 30s of container time to every
message once it starts failing — after 3 consecutive failures the tool
reports itself down for 10 minutes instead of trying again.
"""

from __future__ import annotations

import json
import logging
import time

from pydantic import BaseModel, Field, field_validator

from sandbox.runner import SandboxRunner
from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

# BUMP THIS WHENEVER finance_query.py CHANGES.
#
# The script is COPYed into the image, and ensure_image() returns early if the
# tag already exists locally — it never rebuilds. So an edit to the script is
# invisible on any machine that has already built the previous tag, and the
# symptom is the worst kind: new code, old behaviour, no error anywhere.
#
# :2 — batched sparkline download, names passed in from the app's cache
#      instead of an `.info` call per symbol on every refresh.
FINANCE_IMAGE = "arthur-finance:2"
CACHE_TTL_S = 15 * 60
BREAKER_FAILS = 3
BREAKER_COOLDOWN_S = 10 * 60


class _Breaker:
    def __init__(self):
        self.fails = 0
        self.open_until = 0.0

    def ok(self) -> bool:
        return time.time() >= self.open_until

    def record(self, success: bool) -> None:
        if success:
            self.fails = 0
            return
        self.fails += 1
        if self.fails >= BREAKER_FAILS:
            self.open_until = time.time() + BREAKER_COOLDOWN_S
            self.fails = 0


class _FinanceBase(Tool):
    modes = {TaskMode.FINANCE}
    risk = Risk.SAFE  # read-only market data

    _cache: dict[str, tuple[float, str]] = {}
    _breaker = _Breaker()

    def __init__(self, sandbox: SandboxRunner):
        self._sandbox = sandbox

    async def _query(
        self, op: str, symbols: list[str], period: str = "1mo",
        names: dict[str, str] | None = None, timeout_s: int = 45,
    ) -> ToolResult:
        if not self._breaker.ok():
            wait = max(1, int((self._breaker.open_until - time.time()) / 60))
            return ToolResult(
                ok=False, summary="finance data paused",
                content=(
                    f"Paused after {BREAKER_FAILS} failed attempts in a row. "
                    f"Retrying in about {wait} min. The cause was whatever the last "
                    "attempt reported — Docker not running, the container timing out, "
                    "or Yahoo rate-limiting."
                ),
            )

        key = f"{op}:{','.join(sorted(symbols))}:{period}"
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL_S:
            return ToolResult(ok=True, content=cached[1], summary=f"{op} (cached)")

        # Docker checked FIRST and reported as itself. Rolling a missing daemon
        # into the generic failure path meant three attempts against something
        # that was never going to work, and then a message blaming "repeated
        # Yahoo Finance failures" for a problem Yahoo had no part in.
        if not await self._sandbox.is_available():
            return ToolResult(
                ok=False, summary="Docker not running",
                content=("Market data runs in a container and Docker isn't running. "
                         "Start Docker Desktop and try again."),
            )

        payload = json.dumps({"op": op, "symbols": symbols, "period": period,
                              "names": names or {}})
        try:
            await self._sandbox.ensure_image(FINANCE_IMAGE, "finance.Dockerfile")
            res = await self._sandbox.run(
                FINANCE_IMAGE, [], stdin_data=payload, network="bridge", timeout_s=timeout_s
            )
        except Exception as e:
            self._breaker.record(False)
            return ToolResult(ok=False, content=f"Market data fetch failed: {e}", summary="fetch failed")

        # THE CONTAINER'S OWN ERROR IS THE ONLY USEFUL DIAGNOSTIC, so it must
        # not be discarded. This used to be `res.stdout.strip().splitlines()[-1]`
        # inside the try above, which meant an empty stdout raised IndexError and
        # the operator was shown "list index out of range" — a fact about our
        # parsing, not about what went wrong. Meanwhile stderr, which held the
        # actual traceback, was captured and thrown away.
        #
        # The script prints JSON on EVERY path including its own exceptions, so
        # empty stdout means it never got that far: killed on timeout, or dead
        # before main() (a failed import, a missing dependency in the image).
        stdout = (res.stdout or "").strip()
        if not stdout:
            self._breaker.record(False)
            detail = (res.stderr or "").strip().splitlines()
            reason = detail[-1] if detail else f"no output, exit code {res.exit_code}"
            if getattr(res, "timed_out", False):
                reason = f"timed out after {timeout_s}s"
            log.warning("finance container produced no output (%s): %s",
                        reason, (res.stderr or "")[-2000:])
            return ToolResult(ok=False, summary="container failed",
                              content=f"Market data container failed: {reason}")

        try:
            data = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            self._breaker.record(False)
            log.warning("finance container printed non-JSON: %s", stdout[-2000:])
            return ToolResult(ok=False, summary="bad response",
                              content=f"Market data returned something unreadable: {stdout[-200:]}")

        if not data.get("ok"):
            self._breaker.record(False)
            return ToolResult(ok=False, content=f"Market data error: {data.get('error')}", summary="data error")

        self._breaker.record(True)
        content = json.dumps(data["data"], indent=1)
        self._cache[key] = (time.time(), content)
        return ToolResult(ok=True, content=content, summary=f"{op} for {', '.join(symbols[:5])}")


class QuoteArgs(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=10, description="Ticker symbols, e.g. ['AAPL','MSFT']")

    @field_validator("symbols")
    @classmethod
    def clean(cls, v: list[str]) -> list[str]:
        # Tickers only — an injected "symbol" can't smuggle a payload into the container.
        cleaned = [s.strip().upper() for s in v]
        for s in cleaned:
            if not (1 <= len(s) <= 12 and s.replace(".", "").replace("-", "").replace("=", "").isalnum()):
                raise ValueError(f"invalid ticker: {s!r}")
        return cleaned


class StockQuoteTool(_FinanceBase):
    name = "stock_quote"
    description = "Current price and key stats for stock/ETF/index tickers."
    Args = QuoteArgs

    def approval_summary(self, args: QuoteArgs) -> str:
        return f"Fetch quotes for {', '.join(args.symbols)}"

    async def execute(self, args: QuoteArgs, ctx: ToolContext) -> ToolResult:
        return await self._query("quote", args.symbols)


class HistoryArgs(QuoteArgs):
    period: str = Field(default="1mo", pattern="^(5d|1mo|3mo|6mo|1y|5y)$")


class StockHistoryTool(_FinanceBase):
    name = "stock_history"
    description = "Historical daily closing prices for tickers over a period (5d, 1mo, 3mo, 6mo, 1y, 5y)."
    Args = HistoryArgs

    def approval_summary(self, args: HistoryArgs) -> str:
        return f"Fetch {args.period} history for {', '.join(args.symbols)}"

    async def execute(self, args: HistoryArgs, ctx: ToolContext) -> ToolResult:
        return await self._query("history", args.symbols, args.period)


class WatchlistFetcher(_FinanceBase):
    """NOT a tool — the watchlist panel's data source.

    It deliberately has no `name`/`Args`/`description`, so the registry never
    offers it to the model: the panel is the user's own view of symbols they
    chose, not something a turn should be able to fetch or change. It subclasses
    _FinanceBase purely to inherit the cache and the circuit breaker, which must
    be shared with the tools — the panel and a `stock_quote` call hit the same
    upstream, so they have to back off together.
    """

    async def fetch(
        self, symbols: list[str], names: dict[str, str] | None = None,
    ) -> ToolResult:
        # 90s rather than the tools' 45: this op covers every row on the panel,
        # and the first run for an unseen symbol still pays for one `.info`
        # lookup. Later refreshes reuse the cached names and finish quickly.
        return await self._query("watchlist", symbols, names=names, timeout_s=90)
