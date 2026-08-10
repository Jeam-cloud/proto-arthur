"""How much of the window history is allowed to take.

Two numbers used to be hardcoded independently — num_ctx (8192) and the history
budget (24_000 chars) — with nothing tying them together, so raising one did
nothing and raising the other overflowed. They are the same decision from two
ends, so they are derived in one place.
"""

from __future__ import annotations

from core.context_budget import (
    CHARS_PER_TOKEN,
    HISTORY_SHARE,
    MIN_HISTORY_CHARS,
    history_char_budget,
)


def test_it_scales_with_the_window():
    small = history_char_budget(8_192)
    large = history_char_budget(32_768)
    assert large > small
    assert large == int(32_768 * HISTORY_SHARE * CHARS_PER_TOKEN)


def test_history_never_takes_the_whole_window():
    """The rest holds the system prompt, tool schemas, this turn, tool results
    (whole files, in Code mode) and the reply. History is the only part that can
    always be trimmed without breaking the turn, so it is the part capped."""
    window_chars = 8_192 * CHARS_PER_TOKEN
    assert history_char_budget(8_192) < window_chars / 2


def test_a_tiny_window_still_keeps_a_couple_of_exchanges():
    """Below the floor the model loses what "it" refers to, which is worse than
    being slightly over budget."""
    assert history_char_budget(512) == MIN_HISTORY_CHARS
    assert history_char_budget(0) == MIN_HISTORY_CHARS


def test_nonsense_input_does_not_crash_the_turn():
    assert history_char_budget(-1) == MIN_HISTORY_CHARS
    assert history_char_budget(None) == MIN_HISTORY_CHARS


async def test_the_chat_path_actually_uses_it(app_state, monkeypatch):
    """The wiring is the point: a budget module nothing consults is just a
    number in a different file."""
    seen = {}
    original = app_state.conversations.history_for_model

    async def spy(cid, char_budget=24_000, exclude_id=None):
        seen["budget"] = char_budget
        return await original(cid, char_budget=char_budget, exclude_id=exclude_id)

    monkeypatch.setattr(app_state.conversations, "history_for_model", spy)
    monkeypatch.setattr(app_state.settings, "num_ctx", 32_768)

    conv = await app_state.conversations.create()
    app_state.llm.turns = [{"tokens": ["hi"]}]
    from tests.fakes import CollectingEmit
    from tools.base import TaskMode

    await app_state.chat.stream_reply(
        conversation_id=conv["id"], user_text="hello",
        mode=TaskMode.GENERAL, model="m", emit=CollectingEmit(),
    )
    assert seen["budget"] == history_char_budget(32_768)
