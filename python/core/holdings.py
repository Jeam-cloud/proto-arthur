"""Portfolio holdings: what the user says they own.

TRACKING, NOT BROKERAGE. Every number here was typed in by a person. Arthur
values it against a live price and computes the difference; it never connects
to a broker, never syncs positions, and never executes anything. That boundary
is why this module has no concept of an order, a transaction, or a settlement.

WHAT IS DELIBERATELY NOT COMPUTED: IRR, time-weighted return, benchmark
comparison, tax lots. All of them need a full transaction history, and we ask
for three fields. Deriving them from a single cost basis would produce numbers
that look authoritative and are wrong, which is worse than their absence.
"""

from __future__ import annotations

from core.db import Database, new_id, now


class HoldingStore:
    def __init__(self, db: Database):
        self._db = db

    async def list_all(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM holdings ORDER BY created_at", ()
        )

    async def symbols(self) -> list[str]:
        """Distinct tickers, for the one batched price fetch that values the
        whole portfolio. Two lots of the same stock are two rows but one
        quote."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT symbol FROM holdings ORDER BY symbol", ()
        )
        return [r["symbol"] for r in rows]

    async def add(
        self, symbol: str, quantity: float, cost_basis: float,
        purchase_date: str | None = None,
    ) -> dict:
        """A new lot.

        NOT merged with an existing holding of the same symbol. Buying more at
        a different price is a second lot with its own cost basis, and silently
        averaging them would destroy the information the user came here with.
        The UI can group by symbol for display; the data keeps the truth.
        """
        hid, ts = new_id(), now()
        await self._db.write(
            "INSERT INTO holdings(id, symbol, quantity, cost_basis, purchase_date, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (hid, symbol, quantity, cost_basis, purchase_date, ts, ts),
        )
        return {"id": hid, "symbol": symbol, "quantity": quantity,
                "cost_basis": cost_basis, "purchase_date": purchase_date,
                "created_at": ts, "updated_at": ts}

    async def update(
        self, hid: str, *, quantity: float | None = None,
        cost_basis: float | None = None, purchase_date: str | None = None,
    ) -> bool:
        sets, args = [], []
        if quantity is not None:
            sets.append("quantity=?"); args.append(quantity)
        if cost_basis is not None:
            sets.append("cost_basis=?"); args.append(cost_basis)
        if purchase_date is not None:
            sets.append("purchase_date=?"); args.append(purchase_date)
        if not sets:
            return False
        sets.append("updated_at=?"); args.append(now())
        args.append(hid)
        await self._db.write(f"UPDATE holdings SET {', '.join(sets)} WHERE id=?", tuple(args))
        return True

    async def remove(self, hid: str) -> None:
        await self._db.write("DELETE FROM holdings WHERE id=?", (hid,))


def value_holdings(holdings: list[dict], quotes: dict) -> dict:
    """Price the holdings and total them.

    TOTALS ARE PER CURRENCY, NEVER CONVERTED. A watchlist can hold ASML in EUR
    beside AAPL in USD, and adding those numbers together needs an FX rate this
    app does not fetch. A single wrong total is worse than two right subtotals,
    so the sum is grouped by currency and the UI shows "Total value · USD".

    A holding whose quote failed keeps its cost basis and reports `priced:
    False`. It is never dropped: a position that disappears reads as lost data,
    and this is the one screen where that would be alarming.
    """
    rows, totals = [], {}
    for h in holdings:
        q = quotes.get(h["symbol"]) or {}
        price = q.get("price") if not q.get("failed") else None
        currency = q.get("currency") or "USD"
        cost = h["quantity"] * h["cost_basis"]

        row = {
            **h,
            "name": q.get("name") or h["symbol"],
            "price": price,
            "currency": currency,
            "cost_total": round(cost, 2),
            "priced": price is not None,
        }
        if price is not None:
            value = h["quantity"] * price
            row["value"] = round(value, 2)
            row["pl"] = round(value - cost, 2)
            # Guarded: a zero cost basis (a gift, a spin-off) has no percentage
            # gain, and dividing anyway would produce infinity.
            row["pl_pct"] = round((value - cost) / cost * 100, 2) if cost else None
            # Today's move for THIS position, which is the number the user came
            # for — not the stock's percentage, but their money.
            chg = q.get("change")
            row["day_change"] = round(h["quantity"] * chg, 2) if isinstance(chg, (int, float)) else None

            t = totals.setdefault(currency, {"value": 0.0, "cost": 0.0, "day_change": 0.0,
                                             "priced": 0, "unpriced": 0})
            t["value"] += value
            t["cost"] += cost
            if row["day_change"] is not None:
                t["day_change"] += row["day_change"]
            t["priced"] += 1
        else:
            totals.setdefault(currency, {"value": 0.0, "cost": 0.0, "day_change": 0.0,
                                         "priced": 0, "unpriced": 0})["unpriced"] += 1
        rows.append(row)

    for cur, t in totals.items():
        t["value"] = round(t["value"], 2)
        t["cost"] = round(t["cost"], 2)
        t["day_change"] = round(t["day_change"], 2)
        t["pl"] = round(t["value"] - t["cost"], 2)
        t["pl_pct"] = round((t["value"] - t["cost"]) / t["cost"] * 100, 2) if t["cost"] else None
    return {"holdings": rows, "totals": totals}
