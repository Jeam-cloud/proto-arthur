"""Code mode must say so when it produced code but changed nothing.

THE FAILURE THIS CATCHES, verbatim from a real session: the model printed a
whole CSS file into the chat — invented content, for a file it had never read —
the user replied "go ahead and apply it", and it printed the same invented file
again. Twice more. Nothing was ever staged, so the review panel stayed empty and
there was no diff to contradict it.

The only signal that the work had not happened was an ABSENCE, and absence is
what a person scrolling a confident answer does not notice. So the absence gets
said out loud.
"""

from __future__ import annotations

import pytest

from core import events
from tools.base import TaskMode

CSS = "\n".join([
    "```css",
    ".login-header { color: blue; }",
    ".input-field { border: 1px solid blue; }",
    ".button { background-color: blue; }",
    ".button:hover { background-color: darkblue; }",
    "a { color: blue; }",
    "```",
])


@pytest.fixture
def service(app_state):
    return app_state.chat


def warnings_from(emit) -> str:
    return " ".join(d["text"] for d in emit.of(events.STATUS))


class TestPrintedCodeWarning:
    async def test_warns_when_a_code_block_is_printed_and_nothing_staged(self, service):
        from tests.fakes import CollectingEmit

        emit = CollectingEmit()
        await service._warn_if_code_was_only_printed(f"Here is the update:\n{CSS}", emit)
        text = warnings_from(emit)
        assert "Nothing was saved" in text
        assert "read the file first" in text  # points at the fix, not just the problem

    async def test_silent_for_a_short_inline_snippet(self, service):
        """A two-line example in an explanation is not a file. Warning about it
        would train the user to ignore the warning that matters."""
        from tests.fakes import CollectingEmit

        emit = CollectingEmit()
        await service._warn_if_code_was_only_printed("Use ```css\ncolor: blue;\n```", emit)
        assert warnings_from(emit) == ""

    async def test_silent_for_an_answer_with_no_code_at_all(self, service):
        from tests.fakes import CollectingEmit

        emit = CollectingEmit()
        await service._warn_if_code_was_only_printed("I had a look and nothing needs changing.", emit)
        assert warnings_from(emit) == ""

    async def test_no_warning_when_the_turn_actually_staged_something(
        self, app_state, settings, tmp_path,
    ):
        """The whole point is the ABSENCE of a diff. A turn that produced one
        has nothing to apologise for, even if it also printed the code."""
        from tests.fakes import CollectingEmit

        root = tmp_path / "proj"
        root.mkdir()
        (root / "a.css").write_text("a { color: red; }\n")
        conv = await app_state.conversations.create(mode="code", workspace_root=str(root))

        # The turn itself must do the staging. Staging beforehand would be a
        # different (and correctly warned) situation: a turn that printed code
        # and changed nothing, next to someone else's older pending edit.
        app_state.llm.turns = [
            {"tool_calls": [{"name": "write_file", "arguments": {
                "path": "a.css", "content": "a { color: blue; }\n"}}]},
            {"tokens": [f"Done, here it is:\n{CSS}"]},
        ]

        emit = CollectingEmit()
        await app_state.chat.stream_reply(
            conversation_id=conv["id"], user_text="make it blue",
            mode=TaskMode.CODE, model="m", emit=emit, workspace_root=str(root),
        )
        assert "Nothing was saved" not in warnings_from(emit)

    async def test_warning_fires_through_the_real_stream(self, app_state, tmp_path):
        from tests.fakes import CollectingEmit

        root = tmp_path / "proj2"
        root.mkdir()
        conv = await app_state.conversations.create(mode="code", workspace_root=str(root))
        app_state.llm.turns = [{"tokens": [f"Here is the updated file:\n{CSS}"]}]

        emit = CollectingEmit()
        await app_state.chat.stream_reply(
            conversation_id=conv["id"], user_text="make it blue",
            mode=TaskMode.CODE, model="m", emit=emit, workspace_root=str(root),
        )
        assert "Nothing was saved" in warnings_from(emit)
