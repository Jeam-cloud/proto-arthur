from core import events
from tools.base import TaskMode
from tests.fakes import CollectingEmit

async def test_sanitised_output_reaches_the_screen(app_state):
    """The renderer keeps its own token accumulation, so scrubbing only the
    stored text left the DISPLAYED reply dirty."""
    app_state.llm.turns = [{"tokens": ["Price is <<EXTERNAL 1>>$307.25<<END-EXTERNAL 1>> today."]}]
    conv = await app_state.conversations.create(mode="finance")
    emit = CollectingEmit()
    await app_state.chat.stream_reply(
        conversation_id=conv["id"], user_text="nvda",
        mode=TaskMode.FINANCE, model="m", emit=emit)

    streamed = "".join(t["content"] for t in emit.of(events.TOKEN))
    assert "<<EXTERNAL" in streamed, "precondition: raw tokens carried the markers"

    repl = emit.of(events.DRAFT_REPLACE)
    assert repl, "no correction emitted — the screen would keep the markers"
    assert "<<EXTERNAL" not in repl[-1]["content"]
    assert "$307.25" in repl[-1]["content"], "the number itself must survive"

    stored = [m for m in await app_state.conversations.messages(conv["id"])
              if m["role"] == "assistant"][0]["content"]
    assert "<<EXTERNAL" not in stored


async def test_clean_output_emits_no_correction(app_state):
    """A reply that needed no scrubbing must not flicker through a replace."""
    app_state.llm.turns = [{"tokens": ["NVDA closed at $307.25."]}]
    conv = await app_state.conversations.create(mode="finance")
    emit = CollectingEmit()
    await app_state.chat.stream_reply(
        conversation_id=conv["id"], user_text="nvda",
        mode=TaskMode.FINANCE, model="m", emit=emit)
    assert not emit.of(events.DRAFT_REPLACE)
