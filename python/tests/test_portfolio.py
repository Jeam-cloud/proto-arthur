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
