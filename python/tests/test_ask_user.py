"""Asking the user — the move that isn't guessing or stalling.

Given a request it cannot pin down, the model previously had two options: guess,
or write a question into its reply that the app had no way to collect an answer
to. The second is worse, because the turn ends looking like an answer.

The properties that matter: the question reaches the user, the turn STOPS, and
the model cannot answer on the user's behalf.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.loop import AgentLoop
from agent.registry import ToolRegistry
from core import events
from security.approvals import ApprovalBroker
from security.audit import AuditLog
from security.gateway import SecurityGateway
from tests.fakes import CollectingEmit, EchoTool, FakeLLM, FakeScanner
from tools.base import TaskMode, ToolContext
from tools.interaction import AskUserTool

CTX = ToolContext(conversation_id="conv1")

ASK = {
    "name": "ask_user",
    "arguments": {
        "question": "Which login page did you mean?",
        "options": [
            {"label": "app/static/login.css", "description": "the styles"},
            {"label": "templates/login.html", "description": "the markup"},
        ],
    },
}


def make_loop(db, settings, llm, tools, max_iterations=6):
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    gateway = SecurityGateway(FakeScanner(), AuditLog(db), settings)
    return AgentLoop(llm, registry, gateway, ApprovalBroker(timeout_s=0.05), max_iterations)


class TestTheQuestionReachesTheUser:
    async def test_it_emits_the_question_with_its_options(self, db, settings):
        llm = FakeLLM([{"tool_calls": [ASK]}])
        loop = make_loop(db, settings, llm, [AskUserTool()])
        emit = CollectingEmit()
        await loop.run("m", [], TaskMode.CODE, CTX, emit)

        asked = emit.of(events.ASK_USER)
        assert asked and asked[0]["question"] == "Which login page did you mean?"
        assert [o["label"] for o in asked[0]["options"]] == [
            "app/static/login.css", "templates/login.html",
        ]

    async def test_the_turn_ends_there(self, db, settings):
        """A model that carries on after asking is a model that answered its own
        question — the original guessing failure in a politer costume."""
        llm = FakeLLM([
            {"tool_calls": [ASK]},
            {"tokens": ["I'll assume you meant the CSS and proceed."]},
        ])
        loop = make_loop(db, settings, llm, [AskUserTool()])
        await loop.run("m", [], TaskMode.CODE, CTX, CollectingEmit())

        # One request only: the second scripted turn was never reached.
        assert len([c for c in llm.calls if "messages" in c and "schema" not in c]) == 1

    async def test_the_model_is_told_the_answer_has_not_arrived(self, db, settings):
        """The tool result is replayed as history. If it read as though the user
        had already chosen, the next turn would invent which option they picked."""
        llm = FakeLLM([{"tool_calls": [ASK]}])
        loop = make_loop(db, settings, llm, [AskUserTool()])
        emit = CollectingEmit()
        await loop.run("m", [], TaskMode.CODE, CTX, emit)

        result = emit.of(events.TOOL_RESULT)[0]
        assert result["ok"] is True
        tool = AskUserTool()
        out = await tool.execute(tool.Args.model_validate(ASK["arguments"]), CTX)
        assert "Awaiting their reply" in out.content
        assert "Do not answer on their behalf" in out.content

    async def test_it_is_available_in_every_mode(self, db, settings):
        """Ambiguity is not a property of Code mode, and a mode where the model
        can act but cannot ask is a mode where it has to guess."""
        for mode in (TaskMode.GENERAL, TaskMode.CODE, TaskMode.EMAIL, TaskMode.RESEARCH):
            llm = FakeLLM([{"tool_calls": [ASK]}])
            loop = make_loop(db, settings, llm, [AskUserTool()])
            emit = CollectingEmit()
            await loop.run("m", [], mode, CTX, emit)
            assert emit.of(events.ASK_USER), mode


class TestItRefusesAQuestionNobodyCanAnswer:
    def test_one_option_is_not_a_choice(self):
        with pytest.raises(ValidationError):
            AskUserTool.Args(question="Which one?", options=[{"label": "only"}])

    def test_duplicate_labels_are_refused(self):
        """Two identical labels render as two identical buttons. Rejected rather
        than deduped, so the model is told and asks a real question on retry."""
        with pytest.raises(ValidationError):
            AskUserTool.Args(
                question="Which one?",
                options=[{"label": "login.css"}, {"label": "Login.CSS"}],
            )

    def test_more_than_six_options_is_refused(self):
        with pytest.raises(ValidationError):
            AskUserTool.Args(
                question="Which one?",
                options=[{"label": f"opt {i}"} for i in range(7)],
            )

    async def test_a_bad_question_becomes_a_retryable_tool_error(self, db, settings):
        """It must not crash the turn: the model gets a validation error back and
        can ask properly on the next round, like any other tool."""
        llm = FakeLLM([
            {"tool_calls": [{"name": "ask_user",
                             "arguments": {"question": "hm?", "options": [{"label": "a"}]}}]},
            {"tokens": ["never mind"]},
        ])
        loop = make_loop(db, settings, llm, [AskUserTool()])
        emit = CollectingEmit()
        text = await loop.run("m", [], TaskMode.CODE, CTX, emit)

        assert not emit.of(events.ASK_USER)
        assert text == "never mind"
        assert "invalid arguments" in emit.of(events.TOOL_RESULT)[0]["summary"]


class TestTheCapabilityNote:
    async def test_it_distinguishes_asking_from_permission_when_granted(self, db, settings):
        """The note tells the model not to ask permission, which is right for
        "may I?" and wrong for "which one?". A model given only the prohibition
        guesses — so the exception is stated wherever ask_user is granted."""
        llm = FakeLLM([{"tokens": ["ok"]}])
        loop = make_loop(db, settings, llm, [AskUserTool(), EchoTool()])
        await loop.run("m", [], TaskMode.CODE, CTX, CollectingEmit())
        note = llm.calls[0]["messages"][0]["content"]
        assert "Asking for INFORMATION is different from asking permission" in note

    async def test_it_stays_quiet_when_ask_user_is_not_granted(self, db, settings):
        llm = FakeLLM([{"tokens": ["ok"]}])
        loop = make_loop(db, settings, llm, [EchoTool()])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        note = llm.calls[0]["messages"][0]["content"]
        assert "Asking for INFORMATION" not in note
