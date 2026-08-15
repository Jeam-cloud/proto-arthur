"""The symbol page's two routes.

Coverage comes from Tavily, NOT yfinance: yfinance's news endpoint has a
long-standing bug returning articles unrelated to the requested ticker, and a
symbol page confidently showing a competitor's headline is worse than showing
none. See get_symbol_news.
"""
import httpx
import pytest

from core.app import create_app


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


class TestDetail:
    async def test_a_bad_ticker_is_refused_before_the_container(self, client):
        """The symbol reaches a container, so it is validated like a tool
        argument — not merely as a 404."""
        # No trailing slash: FastAPI would 307 before the handler sees it,
        # which would test the router rather than the validation.
        r = await client.get("/finance/symbol/AAPL;rm")
        assert r.status_code == 400, r.text
        assert "ticker" in r.text.lower()

    async def test_upstream_failure_is_a_200_the_page_can_render(self, client):
        """Docker is off in tests. The page needs a retry state, not an
        exception."""
        r = await client.get("/finance/symbol/AAPL")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["symbol"] == "AAPL"
        assert body["error"]

    async def test_the_symbol_is_normalised(self, client):
        body = (await client.get("/finance/symbol/aapl")).json()
        assert body["symbol"] == "AAPL"


class TestNews:
    async def test_no_tavily_key_is_not_an_error(self, client):
        """The rest of the page works without it, so this is an empty state
        with somewhere to go — not a failure."""
        body = (await client.get("/finance/symbol/AAPL/news")).json()
        assert body["ok"] is True
        assert body["unconfigured"] is True
        assert body["items"] == []

    async def test_the_query_carries_the_company_name(self, client, app_state):
        """Bare tickers are ambiguous words — ALL, IT, ON, KEY — and searching
        them returns everything except the company."""
        await app_state.db.set_setting("finance_symbol_names", {"ALL": "Allstate Corp"})
        app_state.vault.set("tavily", "k")

        seen = {}

        class FakeClient:
            def __init__(self, api_key=None): pass
            def search(self, q, **kw):
                seen["q"] = q
                return {"results": [{"title": "T", "url": "https://reuters.com/x"}]}

        import sys, types
        sys.modules["tavily"] = types.SimpleNamespace(TavilyClient=FakeClient)
        try:
            body = (await client.get("/finance/symbol/ALL/news")).json()
        finally:
            sys.modules.pop("tavily", None)

        assert "Allstate" in seen["q"], seen
        assert body["items"][0]["domain"] == "reuters.com"
