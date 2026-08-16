"""Explain-a-move and comparison.

Both exist because ORCHESTRATION is what a small local model gets wrong, not
arithmetic. explain_move gathers the quote, the history and the news in one
call so the model's only job is to write; comparison is one stock_history call
with several symbols, ranked in the caption so "which did better" is answered
rather than left as a subtraction.
"""
import json

import pytest

from tools.base import TaskMode
from tools.finance import ExplainMoveTool, StockHistoryTool


def _rows(sym, start, step, n=30):
    return {sym: [{"date": f"2026-06-{1 + i % 28:02d}", "close": round(start + i * step, 2)}
                  for i in range(n)]}


class TestComparisonPayload:
    def test_multi_symbol_is_flagged_normalised(self):
        """The renderer must not have to infer this — it writes the caption and
        the axis against the same decision."""
        data = {**_rows("NVDA", 100, 1), **_rows("AMD", 50, -0.2)}
        chart = StockHistoryTool._chart_from_rows(data, "1mo")
        assert chart["normalised"] is True
        assert "% from start" in chart["subtitle"]

    def test_single_symbol_is_not_normalised(self):
        chart = StockHistoryTool._chart_from_rows(_rows("NVDA", 100, 1), "1mo")
        assert chart["normalised"] is False
        assert "% from start" not in chart["subtitle"]

    def test_the_caption_ranks_best_to_worst(self):
        """'Which did better' is the question; a list in request order leaves
        the reader subtracting two percentages."""
        data = {**_rows("LOSER", 100, -1), **_rows("WINNER", 100, 2)}
        chart = StockHistoryTool._chart_from_rows(data, "1mo")
        assert chart["summary"].index("WINNER") < chart["summary"].index("LOSER")
        assert "best to worst" in chart["summary"]

    def test_direction_is_a_word(self):
        chart = StockHistoryTool._chart_from_rows(_rows("X", 100, -1), "1mo")
        assert "down" in chart["summary"]

    def test_a_failed_symbol_does_not_break_the_comparison(self):
        data = {**_rows("GOOD", 100, 1), "BAD": {"failed": True}}
        chart = StockHistoryTool._chart_from_rows(data, "1mo")
        assert [s["label"] for s in chart["series"]] == ["GOOD"]

    def test_nothing_plottable_yields_no_chart(self):
        assert StockHistoryTool._chart_from_rows({"BAD": {"failed": True}}, "1mo") is None


class TestExplainMove:
    def test_it_is_finance_only(self):
        assert ExplainMoveTool.modes == {TaskMode.FINANCE}

    def test_a_bad_ticker_is_refused(self):
        with pytest.raises(ValueError):
            ExplainMoveTool.Args(symbol="AAPL; rm -rf /")

    def test_the_ticker_is_normalised(self):
        assert ExplainMoveTool.Args(symbol=" nvda ").symbol == "NVDA"

    async def test_it_returns_numbers_a_chart_and_sources(self, app_state):
        class Sandbox:
            async def is_available(self): return True
            async def ensure_image(self, *a): pass
            async def run(self, *a, **k):
                row = {"NVDA": {
                    "price": 118.63, "previous_close": 122.9, "currency": "USD",
                    "change": -4.27, "change_pct": -3.47,
                    "day_low": 117.0, "day_high": 120.0,
                    "year_low": 47.32, "year_high": 140.76,
                    "name": "NVIDIA Corporation",
                    "history": _rows("NVDA", 120, -0.1)["NVDA"],
                }}
                class R: exit_code = 0; stderr = ""; timed_out = False
                R.stdout = json.dumps({"ok": True, "data": row})
                return R

        tool = ExplainMoveTool(Sandbox(), app_state.vault)
        tool._cache.clear(); tool._breaker.fails = 0; tool._breaker.open_until = 0
        res = await tool.execute(tool.Args(symbol="NVDA"), None)

        assert res.ok
        assert "118.63" in res.content and "-3.47" in res.content
        assert "down today" in res.content
        # The chart comes from the same payload, so it cannot disagree.
        assert res.chart and res.chart["series"][0]["label"] == "NVDA"
        # No Tavily key in tests -> no headlines, and it must say so rather
        # than leave the model to invent a reason.
        assert "No recent coverage" in res.content

    async def test_it_forbids_asserting_causation(self, app_state):
        """Headlines near a move are evidence, not a cause. The instruction
        travels with the data so it cannot be lost between turns."""
        class Sandbox:
            async def is_available(self): return True
            async def ensure_image(self, *a): pass
            async def run(self, *a, **k):
                class R: exit_code = 0; stderr = ""; timed_out = False
                R.stdout = json.dumps({"ok": True, "data": {"X": {
                    "price": 10, "previous_close": 9, "change": 1, "change_pct": 11.1,
                    "name": "Ex Corp", "history": _rows("X", 9, 0.03)["X"],
                }}})
                return R

        app_state.vault.set("tavily", "k")

        class FakeClient:
            def __init__(self, api_key=None): pass
            def search(self, q, **kw):
                return {"results": [{"title": "Ex Corp wins contract",
                                     "url": "https://reuters.com/a"}]}

        import sys, types
        sys.modules["tavily"] = types.SimpleNamespace(TavilyClient=FakeClient)
        try:
            tool = ExplainMoveTool(Sandbox(), app_state.vault)
            tool._cache.clear(); tool._breaker.fails = 0; tool._breaker.open_until = 0
            res = await tool.execute(tool.Args(symbol="X"), None)
        finally:
            sys.modules.pop("tavily", None)

        assert "EVIDENCE, NOT A CAUSE" in res.content
        assert "[1] Ex Corp wins contract" in res.content
        assert "reuters.com" in res.content
