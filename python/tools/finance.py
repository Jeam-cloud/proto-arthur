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
# :3 — request arrives via argv instead of stdin (see the call site).
# :4 — sparkline extraction sniffs the frame SHAPE instead of assuming one
#      from the symbol count, which left one-symbol watchlists with no chart.
# :5 — `detail` op for the symbol page (quote + history + .info profile).
FINANCE_IMAGE = "arthur-finance:5"
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
            # PASSED AS ARGV, NOT STDIN. Piping stdin into a container goes
            # through docker-py's attach_socket, which does not behave the same
            # on Windows named pipes — when the write does not land the script
            # blocks forever in stdin.read() and gets killed by the timeout,
            # having done nothing. Argv needs no socket and works identically
            # everywhere. The list form means Docker execs directly, so the
            # JSON is never shell-interpreted.
            res = await self._sandbox.run(
                FINANCE_IMAGE, [payload], network="bridge", timeout_s=timeout_s
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

    # How the period reads in the caption. "5d" is a label, not a sentence.
    _SPAN = {"5d": "5 days", "1mo": "1 month", "3mo": "3 months",
             "6mo": "6 months", "1y": "1 year", "5y": "5 years"}

    async def execute(self, args: HistoryArgs, ctx: ToolContext) -> ToolResult:
        result = await self._query("history", args.symbols, args.period)
        if result.ok:
            result.chart = self._chart(result.content, args.period)
        return result

    def _chart(self, content: str, period: str) -> dict | None:
        try:
            return self._chart_from_rows(json.loads(content), period)
        except json.JSONDecodeError:
            return None

    @classmethod
    def _chart_from_rows(cls, data: dict, period: str) -> dict | None:
        """Turn fetched series into something the transcript can draw.

        Returns None rather than an empty chart when there is nothing to plot:
        a chart frame with no line looks like a rendering bug, where no chart
        reads correctly as an absence of data.

        A classmethod because explain_move builds one from history it already
        has — the alternative was a second copy of this, and two chart builders
        drift.
        """
        series, results = [], []
        for sym, rows in (data or {}).items():
            # A per-symbol failure is a dict with `failed`, not a list.
            if not isinstance(rows, list) or len(rows) < 2:
                continue
            points = [{"t": r["date"], "v": r["close"]} for r in rows
                      if isinstance(r, dict) and "date" in r and "close" in r]
            if len(points) < 2:
                continue
            series.append({"label": sym, "points": points})
            first, last = points[0]["v"], points[-1]["v"]
            if first:
                results.append((sym, (last - first) / first * 100, first, last))

        if not series:
            return None

        span = cls._SPAN.get(period, period)
        multi = len(series) > 1
        # COMPARISONS ARE RANKED, not just listed. Asked to compare two stocks,
        # the answer is which did better — so the caption sorts by return and
        # says so, rather than leaving the reader to subtract two percentages.
        if multi:
            results.sort(key=lambda r: r[1], reverse=True)
            parts = [f"{s} {'up' if p >= 0 else 'down'} {abs(p):.1f}%" for s, p, _, _ in results]
            summary = (f"Over {span}, best to worst: " + "; ".join(parts)
                       + ". Percentages from each series' own starting price.")
        else:
            parts = [f"{s} {'up' if p >= 0 else 'down'} {abs(p):.1f}%, from {f:,.2f} to {l:,.2f}"
                     for s, p, f, l in results]
            summary = f"Over {span}: " + "; ".join(parts) + ". Daily closes."

        return {
            "kind": "line",
            "series": series,
            # The renderer normalises multi-series charts to percentages from a
            # shared zero. Flagged in the payload rather than inferred from the
            # series count, so the axis label and the caption cannot disagree
            # about what is being drawn.
            "normalised": multi,
            "title": ", ".join(s["label"] for s in series),
            "subtitle": f"{span} · daily closes" + (" · % from start" if multi else ""),
            # Stated on the chart itself, not just in the panel: this picture
            # will be scrolled back to long after the "updated 3:42pm" line at
            # the edge of the screen has gone.
            "note": "Delayed ~15 min",
            "summary": summary,
        }


class ExplainMoveArgs(BaseModel):
    symbol: str = Field(description="One ticker, e.g. 'NVDA'", max_length=12)

    @field_validator("symbol")
    @classmethod
    def clean(cls, v: str) -> str:
        s = v.strip().upper()
        if not (1 <= len(s) <= 12 and s.replace(".", "").replace("-", "").replace("=", "").isalnum()):
            raise ValueError(f"invalid ticker: {v!r}")
        return s


class ExplainMoveTool(_FinanceBase):
    """Why did X move? — gathered in ONE call, so the model only has to write.

    WHY THIS IS A TOOL AND NOT A PROMPT. Answering this properly needs a quote,
    a month of history, and a news search, then a synthesis. Asked to do that
    unaided, a 7B model has to choose three tools in the right order, remember
    the ticker across all of them, and only then write — and the failure we
    already watched was it announcing "[Using stock_quote(nvda)]" in prose and
    inventing a price.

    So the orchestration is done here, deterministically, and the model is left
    with the one part it is actually good at: reading the numbers and the
    headlines and saying what connects them. The chart and the citations come
    from the payload, so neither can be fabricated.

    IT DOES NOT ASSERT CAUSATION. The tool returns a move and some coverage
    from the same window; whether one explains the other is a judgement, and
    the content says so explicitly. Headlines near a price move are evidence,
    not a cause — and a confident "X fell BECAUSE Y" is the easiest wrong thing
    for this feature to say.
    """

    name = "explain_move"
    description = (
        "Why a stock moved: current price, the change, recent history and "
        "related news, gathered together. Use for 'why is X up/down'."
    )
    Args = ExplainMoveArgs
    risk = Risk.SAFE

    def __init__(self, sandbox: SandboxRunner, vault):
        super().__init__(sandbox)
        self._vault = vault

    def approval_summary(self, args: ExplainMoveArgs) -> str:
        return f"Look up why {args.symbol} moved"

    async def execute(self, args: ExplainMoveArgs, ctx: ToolContext) -> ToolResult:
        sym = args.symbol
        quoted = await self._query("detail", [sym], period="1mo", timeout_s=90)
        if not quoted.ok:
            return quoted

        try:
            row = json.loads(quoted.content).get(sym) or {}
        except json.JSONDecodeError:
            return ToolResult(ok=False, summary="bad response",
                              content="Market data returned something unreadable.")
        if row.get("failed") or row.get("price") is None:
            return ToolResult(ok=False, summary=f"no data for {sym}",
                              content=f"No market data for {sym}. Check the ticker.")

        name = row.get("name") or sym
        pct = row.get("change_pct")
        direction = "unchanged"
        if isinstance(pct, (int, float)):
            direction = "up" if pct > 0 else "down" if pct < 0 else "flat"

        lines = [
            f"{sym} ({name})",
            f"Price {row.get('price')} {row.get('currency') or ''}".strip(),
            f"Previous close {row.get('previous_close')}",
            f"Change {row.get('change')} ({pct}%) — {direction} today",
            f"Day range {row.get('day_low')} to {row.get('day_high')}",
            f"52-week range {row.get('year_low')} to {row.get('year_high')}",
        ]

        articles = await self._news(sym, name)
        if articles:
            lines.append("\nRecent coverage:")
            lines += [f"[{i}] {a['title']} — {a['domain']}" for i, a in enumerate(articles, 1)]
            lines.append(
                "\nThese headlines are from the same period as the move. They are "
                "EVIDENCE, NOT A CAUSE: say what they report and whether it plausibly "
                "relates, and do not claim one caused the other. Cite them as [1], [2]."
            )
        else:
            lines.append(
                "\nNo recent coverage was found. Say so plainly — a move with no "
                "reporting behind it is a normal outcome, not a gap to fill with a guess."
            )

        chart = StockHistoryTool._chart_from_rows(
            {sym: row.get("history") or []}, "1mo",
        )
        return ToolResult(
            ok=True,
            content="\n".join(lines),
            summary=f"{sym} {direction} {abs(pct):.2f}%" if isinstance(pct, (int, float)) else sym,
            detail=f"{len(articles)} sources",
            chart=chart,
        )

    async def _news(self, sym: str, name: str) -> list[dict]:
        """Coverage from Tavily. Never fatal — an explanation with numbers and
        no headlines is still worth having."""
        api_key = self._vault.get("tavily")
        if not api_key:
            return []
        # The company NAME as well as the ticker: bare symbols are ambiguous
        # words (ALL, IT, ON, KEY) and searching them returns everything but
        # the company. Same lesson as the symbol page's news route.
        query = f"{name} ({sym}) stock" if name != sym else f"{sym} stock news"
        try:
            import asyncio

            from tavily import TavilyClient

            res = await asyncio.to_thread(
                lambda: TavilyClient(api_key=api_key).search(query, max_results=5, topic="news")
            )
        except Exception as e:
            log.info("explain_move news search failed for %s: %s", sym, e)
            return []
        out = []
        for r in (res.get("results") or [])[:5]:
            url = r.get("url") or ""
            out.append({
                "title": r.get("title") or url,
                "url": url,
                "domain": url.split("//")[-1].split("/")[0].removeprefix("www."),
            })
        return out


class WatchlistFetcher(_FinanceBase):
    """NOT a tool — the watchlist panel's data source.

    It deliberately has no `name`/`Args`/`description`, so the registry never
    offers it to the model: the panel is the user's own view of symbols they
    chose, not something a turn should be able to fetch or change. It subclasses
    _FinanceBase purely to inherit the cache and the circuit breaker, which must
    be shared with the tools — the panel and a `stock_quote` call hit the same
    upstream, so they have to back off together.
    """

    async def detail(
        self, symbol: str, period: str = "1mo", names: dict[str, str] | None = None,
    ) -> ToolResult:
        """One symbol's full page: quote, history and profile in one run.

        Longer timeout than the watchlist because this one deliberately pays
        for `.info` — see the docstring on detail() in finance_query.py.
        """
        return await self._query("detail", [symbol], period=period,
                                 names=names, timeout_s=90)

    async def fetch(
        self, symbols: list[str], names: dict[str, str] | None = None,
    ) -> ToolResult:
        # 90s rather than the tools' 45: this op covers every row on the panel,
        # and the first run for an unseen symbol still pays for one `.info`
        # lookup. Later refreshes reuse the cached names and finish quickly.
        return await self._query("watchlist", symbols, names=names, timeout_s=90)
