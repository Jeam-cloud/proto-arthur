"""Finance mode must actually hand the model its tools.

Written after a Finance chat replied "I'd need to switch modes" to a request
for a stock quote — which is either the model inventing a limitation, or the
tools genuinely not arriving. Only one of those is our bug, and a transcript
cannot tell them apart, so the wiring gets a test.
"""
import pytest

from tools.base import TaskMode
from tools.finance import StockHistoryTool, StockQuoteTool
from tests.fakes import CollectingEmit


@pytest.fixture
def finance_state(app_state):
    """The shared fixture registers no finance tools (they need a sandbox), so
    add them here rather than assert against a registry that never had them."""
    class _Sandbox:
        async def is_available(self): return False
    app_state.registry.register(StockQuoteTool(_Sandbox()))
    app_state.registry.register(StockHistoryTool(_Sandbox()))
    return app_state


def test_finance_mode_grants_the_finance_tools(finance_state):
    granted = {t.name for t in finance_state.registry.for_mode(TaskMode.FINANCE)}
    assert {"stock_quote", "stock_history"} <= granted, granted


def test_they_are_not_granted_elsewhere(finance_state):
    """Mode is the privilege boundary — a General chat must not reach them."""
    general = {t.name for t in finance_state.registry.for_mode(TaskMode.GENERAL)}
    assert "stock_quote" not in general


async def test_the_schemas_reach_the_model(finance_state):
    finance_state.llm.turns = [{"tokens": ["ok"]}]
    conv = await finance_state.conversations.create(mode="finance")
    await finance_state.chat.stream_reply(
        conversation_id=conv["id"], user_text="pull up AAPL stats",
        mode=TaskMode.FINANCE, model="m", emit=CollectingEmit())

    sent = {t["function"]["name"] for t in (finance_state.llm.calls[0].get("tools") or [])}
    assert "stock_quote" in sent, f"finance tools never reached the model: {sent}"


async def test_the_guidance_names_the_tools(finance_state):
    finance_state.llm.turns = [{"tokens": ["ok"]}]
    conv = await finance_state.conversations.create(mode="finance")
    await finance_state.chat.stream_reply(
        conversation_id=conv["id"], user_text="aapl",
        mode=TaskMode.FINANCE, model="m", emit=CollectingEmit())
    assert "stock_quote" in finance_state.llm.calls[0]["messages"][0]["content"]
