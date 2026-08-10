"""A Code turn ends by WRITING, not by asking.

The old flow stopped at a review panel and waited for Apply. That put the cost
on every good edit to catch the rare bad one, and — the failure that actually
bit — it let "Arthur says it edited login.css" and "login.css changed" be two
separate facts, which is exactly what a small local model gets wrong.

Now the turn applies its own changeset and reports what landed, with the undo
snapshot as the safety net. These tests pin the parts a user would notice: the
file really changes, the receipt names it, and the "nothing was staged" warning
does not fire on a turn that plainly did stage something.
"""

from __future__ import annotations

import pytest

from core import events
from tests.fakes import CollectingEmit
from tools.base import TaskMode


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "login.css").write_text(".label { background: #FE5654; }\n")
    return root


async def run_turn(app_state, project, turns, user_text="make it blue"):
    conv = await app_state.conversations.create(mode="code", workspace_root=str(project))
    app_state.llm.turns = turns
    emit = CollectingEmit()
    await app_state.chat.stream_reply(
        conversation_id=conv["id"], user_text=user_text,
        mode=TaskMode.CODE, model="m", emit=emit, workspace_root=str(project),
    )
    return conv["id"], emit


def write_turns(path="login.css", content=".label { background: #1E88E5; }\n"):
    return [
        {"tool_calls": [{"name": "write_file",
                         "arguments": {"path": path, "content": content}}]},
        {"tokens": ["Done — the login page is blue now."]},
    ]


class TestTheEditLands:
    async def test_the_file_changes_without_anyone_clicking_apply(self, app_state, project):
        await run_turn(app_state, project, write_turns())
        assert (project / "login.css").read_text() == ".label { background: #1E88E5; }\n"

    async def test_the_changeset_is_empty_afterwards(self, app_state, project):
        """Nothing is left pending, so the panel cannot show applied work as if
        it were still a decision waiting to be made."""
        cid, _ = await run_turn(app_state, project, write_turns())
        assert app_state.changesets.peek(cid).is_empty()

    async def test_it_announces_what_it_wrote(self, app_state, project):
        _, emit = await run_turn(app_state, project, write_turns())
        applied = emit.of(events.CHANGES_APPLIED)
        assert applied and applied[0]["applied"] == ["login.css"]

    async def test_the_receipt_names_the_file(self, app_state, project):
        """The receipt is written from the apply result, so it cannot describe
        work that did not happen — which is the whole reason it replaced a
        model-authored 'Done!'."""
        _, emit = await run_turn(app_state, project, write_turns())
        assert "login.css" in emit.of(events.CHANGES_APPLIED)[0]["receipt"]["content"]

    async def test_the_receipt_is_in_the_transcript_but_not_the_prompt(
        self, app_state, project,
    ):
        cid, _ = await run_turn(app_state, project, write_turns())
        roles = [m["role"] for m in await app_state.conversations.messages(cid)]
        assert "receipt" in roles
        history = await app_state.conversations.history_for_model(cid)
        assert all(m["role"] != "receipt" for m in history)

    async def test_an_undo_is_available_immediately(self, app_state, project):
        cid, emit = await run_turn(app_state, project, write_turns())
        assert emit.of(events.CHANGES_APPLIED)[0]["undo_id"]
        assert app_state.undos.latest(cid) is not None

    async def test_undoing_restores_the_original(self, app_state, project):
        cid, _ = await run_turn(app_state, project, write_turns())
        app_state.undos.undo(app_state.undos.latest(cid)["id"])
        assert (project / "login.css").read_text() == ".label { background: #FE5654; }\n"


class TestItStillTellsTheTruth:
    async def test_no_nothing_was_staged_warning_after_a_real_edit(self, app_state, project):
        """REGRESSION GUARD. Auto-apply empties the changeset, so a naive
        "is it still the size it was?" check reads as "nothing staged" on every
        successful edit — telling the user nothing reached their files at the
        exact moment it did."""
        _, emit = await run_turn(app_state, project, write_turns())
        text = " ".join(d["text"] for d in emit.of(events.STATUS))
        assert "Nothing was staged" not in text

    async def test_a_turn_that_only_printed_code_is_still_called_out(
        self, app_state, project,
    ):
        css = "\n".join(["```css", "a{}", "b{}", "c{}", "d{}", "e{}", "```"])
        _, emit = await run_turn(app_state, project, [{"tokens": [f"Here you go:\n{css}"]}])
        text = " ".join(d["text"] for d in emit.of(events.STATUS))
        assert "Nothing was staged" in text
        assert not emit.of(events.CHANGES_APPLIED)

    async def test_a_conflicted_file_is_left_pending_and_reported(
        self, app_state, project, monkeypatch,
    ):
        """A file the user changed underneath Arthur is skipped, not clobbered.
        It stays in the panel, because a skipped file that vanishes silently
        looks exactly like one that succeeded."""
        conv = await app_state.conversations.create(mode="code", workspace_root=str(project))
        cs = app_state.changesets.get(conv["id"], str(project))
        cs.stage_write("login.css", "arthur's version\n")
        (project / "login.css").write_text("MY OWN EDIT\n")

        app_state.llm.turns = [{"tokens": ["All set."]}]
        emit = CollectingEmit()
        await app_state.chat.stream_reply(
            conversation_id=conv["id"], user_text="carry on",
            mode=TaskMode.CODE, model="m", emit=emit, workspace_root=str(project),
        )
        assert (project / "login.css").read_text() == "MY OWN EDIT\n"
        assert emit.of(events.CHANGES_APPLIED)[0]["conflicts"] == ["login.css"]
        assert emit.of(events.CHANGES_UPDATED)          # still on screen to deal with


class TestReviewFirstIsStillAvailable:
    """"Nothing lands without my say-so" is a legitimate way to want to work,
    and it costs one branch to honour it."""

    async def test_nothing_is_written_when_the_setting_is_on(
        self, app_state, project, monkeypatch,
    ):
        monkeypatch.setattr(app_state.settings, "code_review_before_apply", True)
        cid, emit = await run_turn(app_state, project, write_turns())

        assert (project / "login.css").read_text() == ".label { background: #FE5654; }\n"
        assert not emit.of(events.CHANGES_APPLIED)
        assert emit.of(events.CHANGES_UPDATED)[0]["files"] == 1
        assert not app_state.changesets.peek(cid).is_empty()

    async def test_the_default_is_off(self, settings):
        assert settings.code_review_before_apply is False

    async def test_the_toggle_takes_effect_without_a_restart(self, app_state, settings):
        """It is stored in the DB but READ off the Settings object every turn,
        so patching has to mirror it across. A safety switch that silently does
        nothing until relaunch is worse than no switch."""
        import httpx

        from core.app import create_app

        app = create_app(settings=settings, state=app_state)
        async with httpx.ASGITransport(app=app) as transport:
            app.state.arthur = app_state
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1",
                headers={"Authorization": "Bearer test-token-123"},
            ) as c:
                await c.patch("/settings", json={"code_review_before_apply": True})
                assert app_state.settings.code_review_before_apply is True
                body = (await c.get("/settings")).json()
                assert body["code_review_before_apply"] is True
