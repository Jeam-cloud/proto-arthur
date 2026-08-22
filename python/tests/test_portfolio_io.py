"""Portfolio export and import.

The data here was typed by hand and exists in one file on one computer, so
these tests care about two things above all: a round trip must not change the
numbers, and a malformed import must never write anything.
"""
import httpx
import pytest

from core import portfolio_io
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


class TestExport:
    def test_a_round_trip_preserves_every_entered_figure(self):
        # The four hand-entered fields are the only ones that cannot be
        # recovered from anywhere else, so they are the ones that must survive.
        holdings = [
            {"id": "1", "symbol": "XEQT.TO", "quantity": 16.4593,
             "cost_basis": 41.0309, "cost_currency": None, "purchase_date": "2024-02-03"},
            {"id": "2", "symbol": "BTC-CAD", "quantity": 0.000222,
             "cost_basis": 157657.6577, "cost_currency": "CAD", "purchase_date": None},
        ]
        quotes = {"XEQT.TO": {"price": 46.08, "currency": "CAD", "name": "iShares"},
                  "BTC-CAD": {"price": 100063.05, "currency": "CAD", "name": "Bitcoin CAD"}}
        valued = value_holdings(holdings, quotes)["holdings"]

        back = portfolio_io.parse_csv(portfolio_io.export_csv(valued))
        assert back["errors"] == []
        assert [r["symbol"] for r in back["rows"]] == ["XEQT.TO", "BTC-CAD"]
        assert back["rows"][0]["quantity"] == 16.4593
        assert back["rows"][1]["cost_basis"] == 157657.6577
        assert back["rows"][0]["purchase_date"] == "2024-02-03"

    def test_the_currency_is_written_even_when_it_was_null(self):
        # NULL in the database means "same as the quote" — a statement about
        # Arthur's defaults. A file someone reads in a year has to say which
        # currency it actually means.
        valued = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 1, "cost_basis": 10.0,
              "cost_currency": None, "purchase_date": None}],
            {"A": {"price": 12.0, "currency": "CAD"}},
        )["holdings"]
        csv_text = portfolio_io.export_csv(valued)
        assert "CAD" in csv_text
        assert portfolio_io.parse_csv(csv_text)["rows"][0]["cost_currency"] == "CAD"

    def test_valuation_columns_are_commentary_and_do_not_import(self):
        valued = value_holdings(
            [{"id": "1", "symbol": "A", "quantity": 2, "cost_basis": 10.0,
              "cost_currency": None, "purchase_date": None}],
            {"A": {"price": 50.0, "currency": "USD"}},
        )["holdings"]
        text = portfolio_io.export_csv(valued)
        assert "100.0" in text                      # the value column is there
        row = portfolio_io.parse_csv(text)["rows"][0]
        assert set(row) == {"symbol", "quantity", "cost_basis",
                            "cost_currency", "purchase_date"}


class TestParsing:
    def test_spreadsheet_numbers_are_accepted(self):
        # What Excel and Sheets actually hand back for cells the user thinks of
        # as numbers. Rejecting these would break the module's own round trip.
        text = ("symbol,quantity,cost_basis\n"
                'AAPL,"1,234.56","$1,234.56"\n'
                "MSFT,1234;56,1234\n"
                "NVDA,10,\"1 234.56\"\n")
        out = portfolio_io.parse_csv(text.replace(";", ","))
        by = {r["symbol"]: r for r in out["rows"]}
        assert by["AAPL"]["quantity"] == 1234.56
        assert by["AAPL"]["cost_basis"] == 1234.56
        assert by["NVDA"]["cost_basis"] == 1234.56

    def test_european_decimal_commas_when_quoted(self):
        # A decimal comma only survives a comma-separated file if the cell is
        # QUOTED — unquoted, `742,15` is two fields and no parser can tell
        # otherwise. Locale-quoting is what European exports actually do, and
        # it is the only case we can honestly claim to handle.
        out = portfolio_io.parse_csv('symbol,quantity,cost_basis\nASML,10,"742,15"\n')
        assert out["rows"][0]["cost_basis"] == 742.15

    def test_an_unquoted_decimal_comma_truncates_rather_than_inventing(self):
        # Documented, not fixed. `742,15` unquoted reads as 742 and a stray
        # column. Guessing that a trailing 2-digit field is really decimals
        # would corrupt "AAPL,10,150,2024" (a stray date) far more often than
        # it would rescue a European number.
        out = portfolio_io.parse_csv("symbol,quantity,cost_basis\nASML,10,742,15\n")
        assert out["rows"][0]["cost_basis"] == 742.0

    def test_one_bad_row_does_not_lose_the_good_ones(self):
        text = ("symbol,quantity,cost_basis\n"
                "AAPL,10,150\n"
                "not a ticker!,5,10\n"
                "MSFT,abc,400\n"
                "NVDA,3,0\n")            # zero cost is legitimate (a gift)
        out = portfolio_io.parse_csv(text)
        assert [r["symbol"] for r in out["rows"]] == ["AAPL", "NVDA"]
        assert len(out["errors"]) == 2
        assert all("line" in e for e in out["errors"])

    def test_headers_are_matched_case_and_space_insensitively(self):
        out = portfolio_io.parse_csv(" Symbol , Quantity , Cost_Basis \nAAPL,1,2\n")
        assert out["rows"] == [{"symbol": "AAPL", "quantity": 1.0, "cost_basis": 2.0,
                                "cost_currency": None, "purchase_date": None}]

    def test_a_bom_does_not_hide_the_symbol_column(self):
        # Excel writes one, and without stripping it the first header becomes
        # "﻿symbol" and the whole file looks unreadable.
        out = portfolio_io.parse_csv("﻿symbol,quantity,cost_basis\nAAPL,1,2\n")
        assert out["rows"][0]["symbol"] == "AAPL"

    def test_a_file_with_no_symbol_column_is_refused_clearly(self):
        out = portfolio_io.parse_csv("ticker,qty\nAAPL,1\n")
        assert out["rows"] == []
        assert "symbol" in out["errors"][0]["reason"]

    def test_blank_lines_are_not_errors(self):
        out = portfolio_io.parse_csv("symbol,quantity,cost_basis\nAAPL,1,2\n\n,,\n")
        assert len(out["rows"]) == 1 and out["errors"] == []

    def test_a_bad_currency_keeps_the_holding(self):
        out = portfolio_io.parse_csv(
            "symbol,quantity,cost_basis,cost_currency\nAAPL,1,2,DOLLARS\n")
        assert out["rows"][0]["cost_currency"] is None   # label dropped
        assert out["rows"][0]["quantity"] == 1.0         # holding kept
        assert len(out["errors"]) == 1


class TestImportRoutes:
    def _file(self, text: str):
        return {"file": ("p.csv", text.encode("utf-8"), "text/csv")}

    async def test_preview_writes_nothing(self, client):
        text = "symbol,quantity,cost_basis\nAAPL,10,150\nbad!,1,1\n"
        r = await client.post("/finance/portfolio/import/preview", files=self._file(text))
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1 and len(body["errors"]) == 1
        # THE POINT: nothing was committed by looking.
        assert (await client.get("/finance/portfolio")).json()["holdings"] == []

    async def test_import_appends_by_default(self, client):
        await client.post("/finance/portfolio",
                          json={"symbol": "NVDA", "quantity": 1, "cost_basis": 100})
        r = await client.post("/finance/portfolio/import",
                              files=self._file("symbol,quantity,cost_basis\nAAPL,10,150\n"))
        assert r.json() == {"ok": True, "added": 1, "removed": 0, "skipped": 0, "errors": []}
        syms = {h["symbol"] for h in (await client.get("/finance/portfolio")).json()["holdings"]}
        assert syms == {"NVDA", "AAPL"}

    async def test_replace_is_opt_in(self, client):
        await client.post("/finance/portfolio",
                          json={"symbol": "NVDA", "quantity": 1, "cost_basis": 100})
        r = await client.post("/finance/portfolio/import?replace=true",
                              files=self._file("symbol,quantity,cost_basis\nAAPL,10,150\n"))
        assert r.json()["removed"] == 1
        syms = {h["symbol"] for h in (await client.get("/finance/portfolio")).json()["holdings"]}
        assert syms == {"AAPL"}

    async def test_an_unusable_file_changes_nothing(self, client):
        await client.post("/finance/portfolio",
                          json={"symbol": "NVDA", "quantity": 1, "cost_basis": 100})
        r = await client.post("/finance/portfolio/import",
                              files=self._file("nonsense\nrows\n"))
        assert r.status_code == 400
        # Even with replace requested, a file that parses to nothing must not
        # be allowed to empty the portfolio.
        r2 = await client.post("/finance/portfolio/import?replace=true",
                               files=self._file("nonsense\nrows\n"))
        assert r2.status_code == 400
        assert len((await client.get("/finance/portfolio")).json()["holdings"]) == 1

    async def test_export_is_a_named_csv_download(self, client):
        await client.post("/finance/portfolio",
                          json={"symbol": "AAPL", "quantity": 10, "cost_basis": 150})
        r = await client.get("/finance/portfolio/export")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "arthur-portfolio-" in r.headers["content-disposition"]
        assert "AAPL" in r.text

    async def test_export_still_works_when_prices_are_unavailable(self, client):
        # The hand-entered columns are the ones that matter for a backup, and
        # they do not depend on the feed being up.
        await client.post("/finance/portfolio",
                          json={"symbol": "AAPL", "quantity": 10, "cost_basis": 150})
        r = await client.get("/finance/portfolio/export")
        assert "AAPL" in r.text and "150" in r.text
