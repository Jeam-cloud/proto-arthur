import httpx, pytest
from core.app import create_app

@pytest.fixture
async def client(app_state, settings):
    app = create_app(settings=settings, state=app_state)
    async with httpx.ASGITransport(app=app) as tr:
        app.state.arthur = app_state
        async with httpx.AsyncClient(transport=tr, base_url="http://127.0.0.1",
                                     headers={"Authorization": "Bearer test-token-123"}) as c:
            yield c

class TestRepro:
    async def test_exactly_what_the_frontend_sends(self, client):
        # The portfolio add form's payload, verbatim.
        r = await client.post("/finance/portfolio", json={
            "symbol": "BTC-CAD", "quantity": 0.000222,
            "cost_basis": 88784.0, "purchase_date": None, "cost_currency": None})
        print("portfolio POST:", r.status_code, r.text[:300])

        # The watchlist add's payload.
        r2 = await client.put("/finance/watchlist",
                              json={"symbols": ["XEQT", "NVDA", "XEQT.TO", "VFV.TO", "SPCX", "BTC-CAD"]})
        print("watchlist PUT :", r2.status_code, r2.text[:300])

        # And the resolve call the form fires on blur.
        r3 = await client.get("/finance/resolve/BTC-CAD")
        print("resolve GET   :", r3.status_code, r3.text[:200])
