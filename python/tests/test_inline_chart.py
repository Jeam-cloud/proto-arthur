"""A chart is built from the tool's own data, never from the model's prose.

The point of the payload is that a 7B model cannot be asked to transcribe 250
closing prices into a chart spec — it would invent most of them. So the numbers
travel from the fetch to the screen without passing through the model, and the
caption is computed from the same points the line is drawn from.
"""
import json

import pytest

from core import events
from tools.base import TaskMode
from tools.finance import StockHistoryTool
from tests.fakes import CollectingEmit


def _series(n=252, start=78.12, step=0.161):
    return {"NVDA": [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                      "close": round(start + i * step, 2)} for i in range(n)]}


@pytest.fixture
def tool():
    return StockHistoryTool.__new__(StockHistoryTool)


class TestThePayload:
    def test_a_full_year_survives(self, tool):
        """The 120-point cap used to render a 1y request as ~6 months."""
        chart = tool._chart(json.dumps(_series()), "1y")
        assert len(chart["series"][0]["points"]) == 252

    def test_the_caption_matches_the_drawn_endpoints(self, tool):
        chart = tool._chart(json.dumps(_series()), "1y")
        pts = chart["series"][0]["points"]
        assert f"{pts[0]['v']:,.2f}" in chart["summary"]
        assert f"{pts[-1]['v']:,.2f}" in chart["summary"]

    def test_direction_is_a_word_not_a_sign(self, tool):
        """A minus sign is easy to miss; "down" is not."""
        falling = {"X": [{"date": f"d{i}", "close": 100 - i} for i in range(10)]}
        assert "down" in tool._chart(json.dumps(falling), "5d")["summary"]

    def test_the_delay_travels_with_the_chart(self, tool):
        assert "Delayed" in tool._chart(json.dumps(_series(30)), "1mo")["note"]

    @pytest.mark.parametrize("payload", [
        {"X": {"failed": True}},          # per-symbol failure
        {"X": [{"date": "d", "close": 1}]},   # one point is not a line
        {},                                # nothing came back
    ])
    def test_nothing_to_plot_yields_no_chart(self, tool, payload):
        """An empty chart frame reads as a rendering bug; absence reads as
        absence."""
        assert tool._chart(json.dumps(payload), "1y") is None

    def test_unparseable_content_is_survived(self, tool):
        assert tool._chart("not json", "1y") is None

    def test_two_symbols_give_two_series_and_one_caption(self, tool):
        both = {**_series(30),
                "AMD": [{"date": f"d{i}", "close": 100 - i * 0.2} for i in range(30)]}
        chart = tool._chart(json.dumps(both), "1mo")
        assert [s["label"] for s in chart["series"]] == ["NVDA", "AMD"]
        assert "NVDA" in chart["summary"] and "AMD" in chart["summary"]


class TestItReachesTheScreen:
    async def test_a_chart_result_emits_a_chart_event(self, app_state):
        """The loop must forward it — the payload is useless if it stops at the
        tool boundary."""
        from tools.base import Risk, Tool, ToolResult
        from pydantic import BaseModel

        class NoArgs(BaseModel):
            pass

        class FakeChartTool(Tool):
            name = "fake_chart"
            description = "x"
            Args = NoArgs
            # SAFE, or the broker asks for approval and the test's 0.05s
            # timeout denies it — Tool.risk defaults to CONFIRM on purpose.
            risk = Risk.SAFE
            modes = {TaskMode.FINANCE}

            async def execute(self, args, ctx):
                return ToolResult(ok=True, content="{}", summary="drew",
                                  chart={"kind": "line", "series": [{"label": "X", "points": []}],
                                         "title": "X", "summary": "s"})

        app_state.registry.register(FakeChartTool())
        app_state.llm.turns = [
            {"tool_calls": [{"name": "fake_chart", "arguments": {}}]},
            {"tokens": ["there it is"]},
        ]
        conv = await app_state.conversations.create(mode="finance")
        emit = CollectingEmit()
        await app_state.chat.stream_reply(
            conversation_id=conv["id"], user_text="chart it",
            mode=TaskMode.FINANCE, model="m", emit=emit)

        charts = emit.of(events.CHART)
        assert charts and charts[0]["title"] == "X"

    async def test_a_chart_does_not_end_the_turn(self, app_state):
        """Unlike ask_user: the model still has to say what the picture means."""
        from tools.base import Risk, Tool, ToolResult
        from pydantic import BaseModel

        class NoArgs(BaseModel):
            pass

        class FakeChartTool(Tool):
            name = "fake_chart2"
            description = "x"
            Args = NoArgs
            # SAFE, or the broker asks for approval and the test's 0.05s
            # timeout denies it — Tool.risk defaults to CONFIRM on purpose.
            risk = Risk.SAFE
            modes = {TaskMode.FINANCE}

            async def execute(self, args, ctx):
                return ToolResult(ok=True, content="{}", summary="drew",
                                  chart={"kind": "line", "series": [], "summary": "s"})

        app_state.registry.register(FakeChartTool())
        app_state.llm.turns = [
            {"tool_calls": [{"name": "fake_chart2", "arguments": {}}]},
            {"tokens": ["NVDA rose over the year."]},
        ]
        conv = await app_state.conversations.create(mode="finance")
        emit = CollectingEmit()
        text = await app_state.chat.stream_reply(
            conversation_id=conv["id"], user_text="chart it",
            mode=TaskMode.FINANCE, model="m", emit=emit)
        stored = [m for m in await app_state.conversations.messages(conv["id"])
                  if m["role"] == "assistant"][0]["content"]
        assert "NVDA rose" in stored
