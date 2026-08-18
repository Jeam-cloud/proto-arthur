"""Portfolio: hand-entered holdings, valued against live prices.

The two rules that shape all of this:
  * totals are PER CURRENCY and never converted — no FX rate is fetched, and a
    single wrong total is worse than two right subtotals;
  * a holding whose quote failed keeps its cost basis and stays on screen — a
    position that disappears reads as lost data.
"""
import httpx
import pytest

from core.app import create_app
from core.holdings import value_holdings


@pytest.fixture
async def client(app_state, settings):
    app = create_app(settings=settings, state=app_state)
    async with httpx.ASGITransport(app=app) as tr:
        app.state.arthur = app_state
        async with httpx.AsyncClient(
            transport=tr, base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer test-token-123"},
        ) as c:
            yield c


class TestValuation:
    def test_currencies_are_never_summed_together(self):
        out = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 1, "cost_basis": 10},
             {"id": "2", "symbol": "B", "quantity": 1, "cost_basis": 10}],
            {"A": {"price": 20, "currency": "USD"}, "B": {"price": 20, "currency": "EUR"}},
        )
        assert set(out["totals"]) == {"USD", "EUR"}
        assert out["totals"]["USD"]["value"] == 20
        assert out["totals"]["EUR"]["value"] == 20

    def _suspect(self, cost, price):
        out = value_holdings(
            [{"id": "1", "symbol": "X", "quantity": 1, "cost_basis": cost}],
            {"X": {"price": price, "currency": "USD"}},
        )
        return out["holdings"][0].get("cost_suspect")

    def test_a_wrong_instrument_loss_is_flagged(self):
        # The real case: plain "BTC" is the Grayscale Bitcoin Mini Trust at
        # ~$28, so a cost basis paid for the coin reads as a 99.97% loss.
        assert self._suspect(88_784.00, 28.43) is True

    def test_a_huge_genuine_gain_is_never_flagged(self):
        # THE ASYMMETRY THAT MATTERS. NVDA bought at $12 and held to $300 is a
        # 2400% return, not a mistake — casting doubt on it would be both wrong
        # and insulting. Only the loss side is checked. See holdings.py.
        assert self._suspect(12.0, 300.0) is False
        assert self._suspect(1.0, 1000.0) is False

    def test_ordinary_moves_are_left_alone(self):
        assert self._suspect(143.66, 146.23) is False   # +1.8%
        assert self._suspect(3.07, 11.17) is False      # +264%, believable
        assert self._suspect(100.0, 40.0) is False      # -60%, a bad year

    def test_a_zero_cost_basis_cannot_divide_by_zero(self):
        # A gift or a spin-off has no basis and therefore no ratio — the flag
        # must be absent rather than False-by-accident or an exception.
        out = value_holdings(
            [{"id": "1", "symbol": "X", "quantity": 10, "cost_basis": 0.0}],
            {"X": {"price": 5.0, "currency": "USD"}},
        )
        assert "cost_suspect" not in out["holdings"][0]
        assert out["holdings"][0]["pl_pct"] is None

class TestValidationErrorsAreReadable:
    """A 422 must name the field. "Request failed (422)" is not a message.

    The real failure: typing "88,784.00" — the format this page PRINTS numbers
    in — makes Number() return NaN, JSON.stringify writes NaN as null, and the
    API rejects a field the user can see they filled in.
    """

    async def test_a_null_number_names_the_field(self, client):
        r = await client.post("/finance/portfolio", json={
            "symbol": "BTC-CAD", "quantity": 0.000222, "cost_basis": None})
        assert r.status_code == 422
        body = r.json()
        # Arthur's envelope, not FastAPI's `detail` list — otherwise the
        # renderer's client cannot find a message and falls back to its generic
        # text, which is the whole bug.
        assert "error" in body, body
        assert "cost_basis" in body["error"]["message"]
        assert body["error"]["code"] == "invalid_request"

    async def test_a_bad_ticker_says_so(self, client):
        r = await client.post("/finance/portfolio", json={
            "symbol": "not a ticker!", "quantity": 1, "cost_basis": 1})
        assert r.status_code == 422
        assert "symbol" in r.json()["error"]["message"]

    async def test_a_valid_crypto_pair_is_accepted(self, client):
        # BTC-CAD / XRP-USD must pass the ticker validator — the hyphen is the
        # normal form for a crypto-fiat pair.
        for sym in ("BTC-CAD", "XRP-USD", "BTC-USD"):
            r = await client.post("/finance/portfolio", json={
                "symbol": sym, "quantity": 1.0, "cost_basis": 100.0})
            assert r.status_code == 200, (sym, r.text)


class TestCostCurrency:
    """Buying in one currency something that quotes in another.

    Ordinary for anyone with a Canadian or European broker holding US-listed
    stock, and before cost_currency existed the P/L simply subtracted CAD from
    USD and reported the result with a confident arrow on it.
    """

    def test_a_cross_currency_cost_refuses_to_produce_a_pl(self):
        out = value_holdings(
            [{"id": "1", "symbol": "BTC", "quantity": 2,
              "cost_basis": 100.0, "cost_currency": "CAD"}],
            {"BTC": {"price": 50.0, "currency": "USD", "change": 1.0}},
        )
        row = out["holdings"][0]
        assert row["fx_blocked"] is True
        assert row["pl"] is None and row["pl_pct"] is None
        # The VALUE is still real: quantity x price, entirely in USD, never
        # touching the cost basis.
        assert row["value"] == 100.0
        # As is today's move — also pure quote currency.
        assert row["day_change"] == 2.0

    def test_the_total_pl_excludes_what_the_row_refused(self):
        # THE BUG THIS PINS: if the total added the FX-blocked holding's value
        # but not its cost, it would reintroduce at the total level exactly the
        # cross-currency subtraction the row just declined to make.
        out = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 1, "cost_basis": 10.0},
             {"id": "2", "symbol": "B", "quantity": 1,
              "cost_basis": 999.0, "cost_currency": "CAD"}],
            {"A": {"price": 15.0, "currency": "USD"},
             "B": {"price": 100.0, "currency": "USD"}},
        )
        t = out["totals"]["USD"]
        assert t["value"] == 115.0        # both positions counted
        assert t["cost"] == 10.0          # only the comparable one
        assert t["pl"] == 5.0             # 15 - 10, NOT 115 - 10
        assert t["pl_pct"] == 50.0
        assert t["fx_blocked"] == 1
        assert t["pl_covers_all"] is False

    def test_no_cost_currency_means_the_quotes_currency(self):
        # Every row written before migration 9 has NULL here, and its old
        # behaviour must be preserved exactly.
        out = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 1, "cost_basis": 10.0,
              "cost_currency": None}],
            {"A": {"price": 15.0, "currency": "CAD"}},
        )
        row = out["holdings"][0]
        assert row["fx_blocked"] is False
        assert row["cost_currency"] == "CAD"
        assert row["pl"] == 5.0
        assert out["totals"]["CAD"]["pl_covers_all"] is True

    def test_matching_currency_stated_explicitly_is_not_blocked(self):
        out = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 1, "cost_basis": 10.0,
              "cost_currency": "USD"}],
            {"A": {"price": 15.0, "currency": "USD"}},
        )
        assert out["holdings"][0]["fx_blocked"] is False
        assert out["holdings"][0]["pl"] == 5.0


class TestValuationMore:
    def test_an_unpriced_holding_survives_with_its_cost(self):
        out = value_holdings(
            [{"id": "1", "symbol": "DEAD", "quantity": 3, "cost_basis": 10}],
            {"DEAD": {"failed": True}},
        )
        row = out["holdings"][0]
        assert row["priced"] is False
        assert row["cost_total"] == 30
        assert "value" not in row
        assert out["totals"]["USD"]["unpriced"] == 1

    def test_two_lots_of_one_symbol_stay_separate(self):
        """Averaging them silently destroys the cost basis the user entered."""
        out = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 10, "cost_basis": 100},
             {"id": "2", "symbol": "A", "quantity": 10, "cost_basis": 200}],
            {"A": {"price": 150, "currency": "USD"}},
        )
        assert len(out["holdings"]) == 2
        assert out["holdings"][0]["pl"] == 500      # bought at 100
        assert out["holdings"][1]["pl"] == -500     # bought at 200
        assert out["totals"]["USD"]["pl"] == 0

    def test_zero_cost_basis_has_no_percentage(self):
        """A gift or a spin-off has no percentage gain; dividing anyway is inf."""
        out = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 1, "cost_basis": 0}],
            {"A": {"price": 5, "currency": "USD"}},
        )
        assert out["holdings"][0]["pl_pct"] is None

    def test_day_change_is_the_users_money_not_the_stocks_percent(self):
        out = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 40, "cost_basis": 100}],
            {"A": {"price": 110, "currency": "USD", "change": 2.5}},
        )
        assert out["holdings"][0]["day_change"] == 100.0   # 40 shares x 2.50


class TestRoutes:
    async def test_empty_portfolio_is_not_an_error(self, client):
        body = (await client.get("/finance/portfolio")).json()
        assert body["ok"] is True
        assert body["holdings"] == [] and body["totals"] == {}

    async def test_add_list_patch_delete(self, client):
        made = (await client.post("/finance/portfolio", json={
            "symbol": "aapl", "quantity": 40, "cost_basis": 171.2})).json()
        assert made["symbol"] == "AAPL"

        body = (await client.get("/finance/portfolio")).json()
        assert len(body["holdings"]) == 1
        assert body["holdings"][0]["cost_total"] == 6848.0

        await client.patch(f"/finance/portfolio/{made['id']}", json={"quantity": 50})
        body = (await client.get("/finance/portfolio")).json()
        assert body["holdings"][0]["quantity"] == 50

        await client.delete(f"/finance/portfolio/{made['id']}")
        assert (await client.get("/finance/portfolio")).json()["holdings"] == []

    async def test_the_holdings_survive_an_upstream_outage(self, client):
        """Docker is off in tests, so pricing fails. The rows and cost basis
        must still come back — losing the VALUATION is not losing the data."""
        await client.post("/finance/portfolio", json={
            "symbol": "AAPL", "quantity": 40, "cost_basis": 171.2})
        body = (await client.get("/finance/portfolio")).json()

        assert body["ok"] is False          # pricing failed
        assert body["error"]
        assert len(body["holdings"]) == 1   # ...but the holding is here
        assert body["holdings"][0]["cost_total"] == 6848.0
        assert body["holdings"][0]["priced"] is False

    @pytest.mark.parametrize("bad", [
        {"symbol": "A; rm -rf /", "quantity": 1, "cost_basis": 1},
        {"symbol": "AAPL", "quantity": 0, "cost_basis": 1},      # gt=0
        {"symbol": "AAPL", "quantity": -5, "cost_basis": 1},
        {"symbol": "AAPL", "quantity": 1, "cost_basis": -1},     # ge=0
    ])
    async def test_bad_input_is_refused(self, client, bad):
        assert (await client.post("/finance/portfolio", json=bad)).status_code == 422

    async def test_fractional_shares_are_allowed(self, client):
        made = (await client.post("/finance/portfolio", json={
            "symbol": "AAPL", "quantity": 0.5, "cost_basis": 171.2})).json()
        assert made["quantity"] == 0.5
