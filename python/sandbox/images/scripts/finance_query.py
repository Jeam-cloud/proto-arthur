"""Runs INSIDE the finance container. stdin: JSON {"op","symbols","period"}.
stdout: JSON result. Ops are a fixed allowlist — the model can't invoke
arbitrary yfinance methods."""

import json
import sys

import yfinance as yf


def quote(symbols):
    out = {}
    for sym in symbols[:10]:
        t = yf.Ticker(sym)
        info = t.fast_info
        out[sym] = {
            "price": getattr(info, "last_price", None),
            "currency": getattr(info, "currency", None),
            "day_high": getattr(info, "day_high", None),
            "day_low": getattr(info, "day_low", None),
            "year_high": getattr(info, "year_high", None),
            "year_low": getattr(info, "year_low", None),
            "market_cap": getattr(info, "market_cap", None),
        }
    return out


def history(symbols, period):
    valid = {"5d", "1mo", "3mo", "6mo", "1y", "5y"}
    period = period if period in valid else "1mo"
    out = {}
    for sym in symbols[:5]:
        df = yf.Ticker(sym).history(period=period)
        out[sym] = [
            {"date": str(idx.date()), "close": round(float(row["Close"]), 4)}
            for idx, row in df.iterrows()
        ][-120:]
    return out


def main() -> None:
    req = json.loads(sys.stdin.read())
    op, symbols, period = req.get("op"), req.get("symbols", []), req.get("period", "1mo")
    try:
        if op == "quote":
            result = quote(symbols)
        elif op == "history":
            result = history(symbols, period)
        else:
            raise ValueError(f"unknown op: {op}")
        print(json.dumps({"ok": True, "data": result}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)[:300]}))


if __name__ == "__main__":
    main()
