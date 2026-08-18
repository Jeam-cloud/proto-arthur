"""Runs INSIDE the finance container. stdin: JSON {"op","symbols","period"}.
stdout: JSON result. Ops are a fixed allowlist — the model can't invoke
arbitrary yfinance methods.

WHY THE PER-SYMBOL try/except EVERYWHERE: yfinance is an unofficial client of
endpoints Yahoo changes without notice, and a single bad or delisted ticker
raises. Letting that propagate would blank a whole watchlist because one row
failed, so every symbol is isolated and reports its own failure. The UI renders
a failed row in place with a Retry rather than dropping it — a row that
disappears looks like the user removed it.
"""

import json
import sys

import yfinance as yf

# Roughly a year of trading days. The old code truncated to the last 120
# points, which silently turned a "1y" chart into the last six months and a
# "5y" chart into the last six months as well — the label said one thing and
# the pixels showed another. Downsampling keeps the full span and only drops
# resolution, which is the honest trade for a line chart a few hundred pixels
# wide.
MAX_POINTS = 260
# One month of daily closes is enough shape for a sparkline and keeps the
# batched watchlist request small.
SPARK_PERIOD = "1mo"
SPARK_POINTS = 30

VALID_PERIODS = {"5d", "1mo", "3mo", "6mo", "1y", "5y"}


def _downsample(rows, limit):
    """Evenly thin a series, always keeping the first and last point.

    The endpoints matter more than the interior: they are what the summary line
    ("from $78.12 to $118.63") is computed from, and dropping either would make
    the caption disagree with the chart.
    """
    n = len(rows)
    if n <= limit:
        return rows
    step = (n - 1) / (limit - 1)
    picked = [rows[round(i * step)] for i in range(limit)]
    picked[-1] = rows[-1]
    return picked


def _name_of(ticker, sym):
    """Display name, or the symbol if Yahoo won't say.

    `.info` IS THE EXPENSIVE CALL IN yfinance — it is the one that is slow and
    that rate-limits, unlike `fast_info` which is a light quote endpoint. Every
    caller here must therefore treat it as optional: a row showing a ticker and
    no company name is perfectly usable; a row that failed because a name
    lookup 429'd is not.

    Callers should avoid it entirely when they already know the name — see the
    `names` argument to watchlist(). Company names effectively never change, so
    this should run once per symbol for the life of an install, not on every
    refresh.
    """
    try:
        info = ticker.info or {}
        return info.get("shortName") or info.get("longName") or sym
    except Exception:
        return sym


def _pct(value, already_pct_above=None):
    """Yahoo's ratios, normalised to percentages, in ONE place.

    Margins, growth rates and ownership come back as fractions (0.4632 means
    46.32%). dividendYield is the inconsistent one — some tickers report a
    fraction, some report a percentage already — so it passes a threshold above
    which the value is taken as-is.

    Doing this here rather than in the UI means no screen has to guess which
    convention it was handed, and the same number cannot render as 46% on one
    surface and 0.46% on another.
    """
    if not isinstance(value, (int, float)):
        return None
    if already_pct_above is not None and value > already_pct_above:
        return round(value, 2)
    return round(value * 100, 2)


def _quote_fields(ticker):
    fi = ticker.fast_info
    price = getattr(fi, "last_price", None)
    prev = getattr(fi, "previous_close", None)
    out = {
        "price": price,
        "previous_close": prev,
        "currency": getattr(fi, "currency", None),
        "day_high": getattr(fi, "day_high", None),
        "day_low": getattr(fi, "day_low", None),
        "year_high": getattr(fi, "year_high", None),
        "year_low": getattr(fi, "year_low", None),
        "market_cap": getattr(fi, "market_cap", None),
    }
    # Derived here rather than in the UI so every surface showing a change
    # agrees, and so "no previous close" reads as absent instead of as 0%.
    if isinstance(price, (int, float)) and isinstance(prev, (int, float)) and prev:
        out["change"] = round(price - prev, 4)
        out["change_pct"] = round((price - prev) / prev * 100, 2)
    else:
        out["change"] = None
        out["change_pct"] = None
    return out


def quote(symbols):
    out = {}
    for sym in symbols[:10]:
        try:
            t = yf.Ticker(sym)
            row = _quote_fields(t)
            row["name"] = _name_of(t, sym)
            out[sym] = row
        except Exception as e:
            out[sym] = {"failed": True, "error": str(e)[:160]}
    return out


def history(symbols, period):
    period = period if period in VALID_PERIODS else "1mo"
    out = {}
    for sym in symbols[:5]:
        try:
            df = yf.Ticker(sym).history(period=period)
            rows = [
                {"date": str(idx.date()), "close": round(float(row["Close"]), 4)}
                for idx, row in df.iterrows()
            ]
            out[sym] = _downsample(rows, MAX_POINTS)
        except Exception as e:
            out[sym] = {"failed": True, "error": str(e)[:160]}
    return out


def _batch_sparklines(symbols):
    """One HTTP request for every symbol's closes, instead of one per symbol.

    `yf.download` accepts a list and returns a column-grouped frame. For a
    six-row watchlist this is the difference between one request and six, which
    matters because the whole op runs inside a container with a fixed timeout
    and Yahoo rate-limits aggressively.

    Returns {sym: [closes]} and never raises — a missing sparkline empties one
    cell, it does not fail the row.
    """
    out = {sym: [] for sym in symbols}
    if not symbols:
        return out
    try:
        df = yf.download(
            tickers=symbols, period=SPARK_PERIOD, interval="1d",
            group_by="ticker", auto_adjust=True, progress=False, threads=True,
        )
    except Exception:
        return out

    for sym in symbols:
        # SHAPE-SNIFFED, NOT COUNT-SNIFFED. yfinance returns either a flat
        # frame or one with MultiIndex columns depending on version, on
        # `group_by`, and on how many tickers came back — NOT reliably on how
        # many were requested. Branching on len(symbols) therefore worked for
        # some installs and silently produced no sparkline on others, which is
        # exactly what a one-symbol watchlist hit. Try both and take whichever
        # answers.
        col = None
        for get in (
            lambda: df[sym]["Close"],      # MultiIndex, grouped by ticker
            lambda: df["Close"][sym],      # MultiIndex, grouped by field
            lambda: df["Close"],           # flat, single ticker
        ):
            try:
                candidate = get()
                if candidate is not None and len(candidate):
                    col = candidate
                    break
            except Exception:
                continue
        if col is None:
            out[sym] = []
            continue
        try:
            closes = [round(float(c), 4) for c in col.dropna().tolist()]
            out[sym] = _downsample(closes, SPARK_POINTS)
        except Exception:
            out[sym] = []
    return out


def watchlist(symbols, names=None):
    """Everything the panel needs for every row, in ONE container run.

    WHY THIS OP EXISTS: the panel needs a quote AND a sparkline per symbol.
    Built from the existing ops that is a `quote` call plus a `history` call
    per five symbols — three container starts for a six-row list, each with its
    own timeout, on a feed that is 15 minutes delayed anyway.

    THE COST MODEL MATTERS HERE, and getting it wrong is what made the first
    version of this fail outright. Per symbol there are three possible calls:

      fast_info   cheap   — always needed, it is the quote
      history     medium  — now batched into ONE request for all symbols
      .info       EXPENSIVE — the rate-limiting one; only for unknown names

    `names` carries the names the caller already has, so the expensive call
    runs once per symbol ever rather than on every refresh. A six-row watchlist
    settles at six cheap calls plus one batch download.
    """
    symbols = symbols[:20]
    names = names or {}
    sparks = _batch_sparklines(symbols)
    out = {}
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            row = _quote_fields(t)
            known = names.get(sym)
            row["name"] = known or _name_of(t, sym)
            row["spark"] = sparks.get(sym) or []
            out[sym] = row
        except Exception as e:
            out[sym] = {"failed": True, "error": str(e)[:160]}
    return out


def detail(symbols, period="1mo", names=None):
    """Everything one symbol page needs, in one container run.

    THIS IS THE ONE PLACE `.info` IS APPROPRIATE. It is yfinance's slow,
    rate-limited call, and it was pulled out of the watchlist because firing it
    per symbol on every refresh timed the container out. A detail page is the
    opposite shape: one symbol, opened deliberately, and the fields it carries
    (sector, industry, business summary, P/E) do not change intraday — so it is
    paid for once and cached hard upstream.

    Everything from `.info` is best-effort. The page must be useful with only
    the price and the chart; a profile that failed to load is a missing row,
    not a failed request.
    """
    sym = (symbols or [""])[0]
    if not sym:
        return {}
    names = names or {}
    try:
        t = yf.Ticker(sym)
        row = _quote_fields(t)
    except Exception as e:
        return {sym: {"failed": True, "error": str(e)[:160]}}

    try:
        df = t.history(period=period if period in VALID_PERIODS else "1mo")
        rows = [{"date": str(idx.date()), "close": round(float(r["Close"]), 4)}
                for idx, r in df.iterrows()]
        row["history"] = _downsample(rows, MAX_POINTS)
    except Exception:
        row["history"] = []

    row["name"] = names.get(sym) or sym
    info = {}
    try:
        info = t.info or {}
    except Exception:
        pass
    if info:
        row["name"] = info.get("shortName") or info.get("longName") or row["name"]
        # THE RESEARCH LAYER. All of this rides along on the `.info` call the
        # page already pays for — no extra request. Grouped by the question it
        # answers rather than by where Yahoo happens to put it, because the
        # sections on screen are the groups.
        #
        # Margins and growth arrive as FRACTIONS from Yahoo (0.4632 = 46.32%)
        # while dividendYield is inconsistent — normalised in one place here so
        # no UI has to guess which convention it received.
        row["research"] = {
            "valuation": {
                "pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "price_to_sales": info.get("priceToSalesTrailing12Months"),
                "price_to_book": info.get("priceToBook"),
                "peg": info.get("pegRatio"),
            },
            "profitability": {
                "gross_margin": _pct(info.get("grossMargins")),
                "operating_margin": _pct(info.get("operatingMargins")),
                "profit_margin": _pct(info.get("profitMargins")),
                "roe": _pct(info.get("returnOnEquity")),
                "roa": _pct(info.get("returnOnAssets")),
            },
            "health": {
                "debt_to_equity": info.get("debtToEquity"),
                "current_ratio": info.get("currentRatio"),
                "quick_ratio": info.get("quickRatio"),
                "free_cash_flow": info.get("freeCashflow"),
                "total_cash": info.get("totalCash"),
                "total_debt": info.get("totalDebt"),
            },
            "growth": {
                "revenue_growth": _pct(info.get("revenueGrowth")),
                "earnings_growth": _pct(info.get("earningsGrowth")),
            },
            "dividend": {
                "yield": _pct(info.get("dividendYield"), already_pct_above=1),
                "rate": info.get("dividendRate"),
                "payout_ratio": _pct(info.get("payoutRatio")),
            },
            "ownership": {
                "institutions": _pct(info.get("heldPercentInstitutions")),
                "insiders": _pct(info.get("heldPercentInsiders")),
                "beta": info.get("beta"),
                "shares_outstanding": info.get("sharesOutstanding"),
            },
        }
        row["profile"] = {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "summary": info.get("longBusinessSummary"),
            "pe": info.get("trailingPE"),
            # Yahoo is inconsistent here: some tickers report a fraction, some
            # a percentage already. Normalised to a percentage so the UI never
            # has to guess which one it got.
            "dividend_yield": (
                info.get("dividendYield") * 100
                if isinstance(info.get("dividendYield"), (int, float))
                and info.get("dividendYield") < 1
                else info.get("dividendYield")
            ),
            "employees": info.get("fullTimeEmployees"),
            "website": info.get("website"),
        }
    return {sym: row}


def _read_request() -> dict:
    """The request, from argv if given, else stdin.

    ARGV IS THE PRIMARY PATH ON PURPOSE. Piping stdin into a container relies
    on docker-py's attach_socket, which behaves differently on Windows named
    pipes than on Unix sockets — and when the write does not land, this script
    blocks in stdin.read() waiting for an EOF that never arrives, prints
    nothing, and gets killed by the timeout. The symptom is a container that
    "times out" while having done no work at all.

    The payload here is a few hundred bytes of tickers, so it fits in argv
    comfortably. It is passed as a list to Docker (exec form, no shell), so
    quotes and braces in the JSON need no escaping and nothing is interpreted.

    stdin is kept as a fallback so an older image and a newer app still work.
    """
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return json.loads(sys.argv[1])
    return json.loads(sys.stdin.read())


def main() -> None:
    req = _read_request()
    op, symbols, period = req.get("op"), req.get("symbols", []), req.get("period", "1mo")
    try:
        if op == "quote":
            result = quote(symbols)
        elif op == "history":
            result = history(symbols, period)
        elif op == "watchlist":
            result = watchlist(symbols, req.get("names"))
        elif op == "detail":
            result = detail(symbols, period, req.get("names"))
        else:
            raise ValueError(f"unknown op: {op}")
        print(json.dumps({"ok": True, "data": result}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)[:300]}))


if __name__ == "__main__":
    main()
