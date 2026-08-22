"""Portfolio export and import.

WHY THIS EXISTS: every figure in the holdings table was typed by hand and
exists in exactly one file on one computer. A tracker with no way out is a
lock-in nobody chose, and a laptop is a single point of failure for data that
cannot be re-fetched from anywhere.

CSV, NOT JSON. The file is meant to be opened, checked and edited in a
spreadsheet — which is where this data usually comes from in the first place.
JSON would round-trip more precisely and be useless to the person holding it.

IMPORT IS THE DANGEROUS DIRECTION and is written accordingly:

  * It NEVER writes during parsing. Parse produces a report; the caller decides
    whether to apply it. A half-applied import of a malformed file is the worst
    outcome available here, and this makes it unreachable.
  * A bad row does not fail the file. Twenty good rows and one typo should
    import twenty rows and tell you about the one, not reject everything.
  * It reports what it will do BEFORE doing it, so the UI can show a preview.
"""

from __future__ import annotations

import csv
import io
from datetime import date

# The column order the exporter writes. `name` and the valuation columns are
# included for the human reading the file and ignored on the way back in — they
# are derived from live prices, so importing them would resurrect stale numbers
# as if they were facts.
EXPORT_COLUMNS = [
    "symbol", "quantity", "cost_basis", "cost_currency", "purchase_date",
    "name", "price", "value", "pl", "pl_pct",
]
# What import actually reads. Everything else in the file is commentary.
IMPORT_COLUMNS = {"symbol", "quantity", "cost_basis", "cost_currency", "purchase_date"}

MAX_ROWS = 500          # a portfolio, not a data feed
MAX_BYTES = 1_000_000   # 500 rows of this shape is ~40KB; 1MB is already absurd


def export_csv(rows: list[dict]) -> str:
    """Valued holdings -> CSV text.

    Takes the OUTPUT of value_holdings, not the raw store, so the file carries
    the same figures the user was looking at when they clicked Export.
    """
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore",
                       lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({
            "symbol": r.get("symbol", ""),
            "quantity": r.get("quantity", ""),
            "cost_basis": r.get("cost_basis", ""),
            # Written EXPLICITLY even when it matches the quote's currency. In
            # the database a null means "same as the quote", which is a
            # statement about Arthur's defaults, not about the money. A file
            # someone may read in a year should say which currency it means.
            "cost_currency": r.get("cost_currency") or "",
            "purchase_date": r.get("purchase_date") or "",
            "name": r.get("name", ""),
            "price": "" if r.get("price") is None else r["price"],
            "value": "" if r.get("value") is None else r["value"],
            "pl": "" if r.get("pl") is None else r["pl"],
            "pl_pct": "" if r.get("pl_pct") is None else r["pl_pct"],
        })
    return buf.getvalue()


def export_filename(today: date | None = None) -> str:
    return f"arthur-portfolio-{(today or date.today()).isoformat()}.csv"


def _clean_symbol(raw: str) -> str | None:
    """The same rule the API applies, because an imported ticker reaches the
    finance container exactly like a typed one does."""
    s = (raw or "").strip().upper()
    if not s:
        return None
    if not (1 <= len(s) <= 12
            and s.replace(".", "").replace("-", "").replace("=", "").isalnum()):
        return None
    return s


def _clean_number(raw: str) -> float | None:
    """Accepts what a spreadsheet actually produces.

    Excel and Google Sheets will happily hand back "$1,234.56" or "1 234,56"
    for a cell the user thinks of as a number, and a CSV round-trip preserves
    every one of those. Rejecting them would make the export/import pair fail
    on files this module itself is designed to accept back.
    """
    s = str(raw or "").strip().replace("$", "").replace("£", "").replace("€", "")
    s = s.replace(" ", "").replace(" ", "")
    if not s:
        return None
    # A comma is a thousands separator in "1,234.56" and a decimal point in
    # "1234,56". Deciding by whether a dot is also present is the only
    # distinction available from the text alone.
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v == v and abs(v) != float("inf") else None


def parse_csv(text: str) -> dict:
    """CSV text -> {rows, errors, columns}. WRITES NOTHING.

    Returns every row it could read and a line-numbered complaint for each one
    it could not, so the UI can preview both before anything is committed.
    """
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        return {"rows": [], "errors": [{"line": 0, "reason": "File is too large to be a portfolio."}],
                "columns": []}

    # utf-8-sig: Excel writes a BOM, which otherwise becomes part of the first
    # header name and makes "symbol" unfindable.
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    if "symbol" not in fields:
        return {"rows": [], "columns": fields, "errors": [{
            "line": 1,
            "reason": "No 'symbol' column. Expected a header row with at least "
                      "symbol, quantity and cost_basis.",
        }]}

    rows: list[dict] = []
    errors: list[dict] = []
    for i, raw in enumerate(reader, start=2):    # line 1 is the header
        if len(rows) >= MAX_ROWS:
            errors.append({"line": i, "reason": f"Stopped at {MAX_ROWS} holdings."})
            break
        # Normalise keys so "Symbol", "SYMBOL" and " symbol " all land.
        r = {(k or "").strip().lower(): v for k, v in raw.items() if k}
        if not any((v or "").strip() for v in r.values()):
            continue                              # blank line, not an error

        symbol = _clean_symbol(r.get("symbol", ""))
        quantity = _clean_number(r.get("quantity", ""))
        cost = _clean_number(r.get("cost_basis", r.get("paid", "")))

        if symbol is None:
            errors.append({"line": i, "reason": f"{r.get('symbol', '')!r} is not a valid ticker."})
            continue
        if quantity is None or quantity <= 0:
            errors.append({"line": i, "symbol": symbol,
                           "reason": "Quantity must be a number above zero."})
            continue
        if cost is None or cost < 0:
            errors.append({"line": i, "symbol": symbol,
                           "reason": "Cost must be a number of zero or more."})
            continue

        cur = (r.get("cost_currency") or "").strip().upper() or None
        if cur and len(cur) != 3:
            # Not fatal: the holding is still sound, only the currency label is
            # unusable, and dropping the row over it would lose real data.
            errors.append({"line": i, "symbol": symbol,
                           "reason": f"Ignored currency {cur!r} — expected a 3-letter code."})
            cur = None

        rows.append({
            "symbol": symbol,
            "quantity": quantity,
            "cost_basis": cost,
            "cost_currency": cur,
            "purchase_date": (r.get("purchase_date") or "").strip()[:10] or None,
        })

    return {"rows": rows, "errors": errors, "columns": fields}
