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

    `.info` is the heavy, rate-limit-prone call in yfinance, so this is
    best-effort and never fatal: a row with a symbol and no name is perfectly
    usable, a row that failed because a name lookup 429'd is not.
    """
    try:
        info = ticker.info or {}
        return info.get("shortName") or info.get("longName") or sym
    except Exception:
        return sym


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


def watchlist(symbols):
    """Everything one watchlist row needs, for every row, in ONE container run.

    WHY THIS OP EXISTS: the panel needs a quote AND a sparkline per symbol.
    Done with the existing ops that is one `quote` call plus a `history` call
    per five symbols — three container starts for a six-row list, each with a
    45s timeout, on a feed that is 15 minutes delayed anyway. Batching it here
    makes the whole panel one round trip.
    """
    symbols = symbols[:20]
    out = {}
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            row = _quote_fields(t)
            row["name"] = _name_of(t, sym)
            try:
                df = t.history(period=SPARK_PERIOD)
                closes = [round(float(c), 4) for c in df["Close"].tolist()]
                row["spark"] = _downsample(closes, SPARK_POINTS)
            except Exception:
                # A quote without a sparkline is still a usable row. The panel
                # just leaves that cell empty rather than failing the symbol.
                row["spark"] = []
            out[sym] = row
        except Exception as e:
            out[sym] = {"failed": True, "error": str(e)[:160]}
    return out


def main() -> None:
    req = json.loads(sys.stdin.read())
    op, symbols, period = req.get("op"), req.get("symbols", []), req.get("period", "1mo")
    try:
        if op == "quote":
            result = quote(symbols)
        elif op == "history":
            result = history(symbols, period)
        elif op == "watchlist":
            result = watchlist(symbols)
        else:
            raise ValueError(f"unknown op: {op}")
        print(json.dumps({"ok": True, "data": result}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)[:300]}))


if __name__ == "__main__":
    main()
