"""Agent loop behavior under a deliberately unreliable model."""

import asyncio

from agent.loop import (
    MAX_FORCED,
    AgentLoop,
    claims_a_file_change,
    recover_text_tool_call,
    strip_tool_call_json,
)
from agent.registry import ToolRegistry
from coding.changeset import ChangeSet
from core import events
from security.approvals import ApprovalBroker
from security.audit import AuditLog
from security.gateway import SecurityGateway
from tests.fakes import (
    CollectingEmit,
    ConfirmEchoTool,
    CrashTool,
    EchoTool,
    EditFileTool,
    ExternalTool,
    FakeLLM,
    FakeScanner,
    WriteFileTool,
)
from tools.base import TaskMode, ToolContext


def make_loop(db, settings, llm, tools, max_iterations=4, timeout=0.05):
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    gateway = SecurityGateway(FakeScanner(), AuditLog(db), settings)
    broker = ApprovalBroker(timeout_s=timeout)
    return AgentLoop(llm, registry, gateway, broker, max_iterations), broker


CTX = ToolContext(conversation_id="conv1")


async def test_plain_answer_streams_tokens(db, settings):
    llm = FakeLLM([{"tokens": ["Hel", "lo"]}])
    loop, _ = make_loop(db, settings, llm, [EchoTool()])
    emit = CollectingEmit()
    text = await loop.run("m", [{"role": "user", "content": "hi"}], TaskMode.GENERAL, CTX, emit)
    assert text == "Hello"
    assert [d["content"] for d in emit.of(events.TOKEN)] == ["Hel", "lo"]


async def test_tool_call_roundtrip(db, settings):
    tool = EchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "echo", "arguments": {"text": "ping"}}]},
        {"tokens": ["got it"]},
    ])
    loop, _ = make_loop(db, settings, llm, [tool])
    emit = CollectingEmit()
    text = await loop.run("m", [{"role": "user", "content": "echo ping"}], TaskMode.GENERAL, CTX, emit)

    assert tool.executions == ["ping"]
    assert text == "got it"
    # the model saw its own call + the tool result in history on the 2nd request
    roles = [m["role"] for m in llm.calls[1]["messages"]]
    assert roles[-2:] == ["assistant", "tool"]
    assert emit.of(events.TOOL_START) and emit.of(events.TOOL_RESULT)[0]["ok"]


async def test_out_of_mode_tool_is_refused(db, settings):
    """Privilege separation: echo exists, but NOT in EMAIL mode."""
    tool = EchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "echo", "arguments": {"text": "x"}}]},
        {"tokens": ["ok"]},
    ])
    loop, _ = make_loop(db, settings, llm, [tool])
    await loop.run("m", [], TaskMode.EMAIL, CTX, CollectingEmit())
    assert tool.executions == []  # never ran
    tool_msg = llm.calls[1]["messages"][-1]
    assert "not available" in tool_msg["content"]


async def test_invalid_args_fed_back_for_retry(db, settings):
    tool = EchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "echo", "arguments": {"wrong_field": 1}}]},
        {"tool_calls": [{"name": "echo", "arguments": {"text": "fixed"}}]},
        {"tokens": ["done"]},
    ])
    loop, _ = make_loop(db, settings, llm, [tool])
    text = await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
    assert tool.executions == ["fixed"]  # model corrected itself and succeeded
    assert text == "done"


async def test_confirm_tool_denied_on_timeout(db, settings):
    tool = ConfirmEchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "confirm_echo", "arguments": {"text": "risky"}}]},
        {"tokens": ["understood"]},
    ])
    loop, _ = make_loop(db, settings, llm, [tool], timeout=0.05)
    emit = CollectingEmit()
    await loop.run("m", [], TaskMode.GENERAL, CTX, emit)

    assert tool.executions == []  # denied -> never executed
    assert emit.of(events.APPROVAL_REQUIRED)
    assert emit.of(events.APPROVAL_RESOLVED)[0]["approved"] is False
    assert "declined" in llm.calls[1]["messages"][-1]["content"]


async def test_confirm_tool_runs_when_approved(db, settings):
    tool = ConfirmEchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "confirm_echo", "arguments": {"text": "risky"}}]},
        {"tokens": ["did it"]},
    ])
    loop, broker = make_loop(db, settings, llm, [tool], timeout=2.0)

    async def approve_when_asked():
        for _ in range(200):
            if broker.pending():
                broker.resolve(broker.pending()[0].id, True)
                return
            await asyncio.sleep(0.005)

    _, text = await asyncio.gather(
        approve_when_asked(),
        loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit()),
    )
    assert tool.executions == ["risky"]
    assert text == "did it"


async def test_confirm_tool_runs_with_edited_args_when_user_rewrote_the_draft(db, settings):
    """The approval dialog lets the person editing (e.g. an email) change the
    draft before it's sent. What actually runs must be the EDITED text, not
    the model's original -- this is the whole point of the feature."""
    tool = ConfirmEchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "confirm_echo", "arguments": {"text": "draft from model"}}]},
        {"tokens": ["sent"]},
    ])
    loop, broker = make_loop(db, settings, llm, [tool], timeout=2.0)

    async def approve_with_edit():
        for _ in range(200):
            if broker.pending():
                broker.resolve(broker.pending()[0].id, True, {"text": "user's rewritten version"})
                return
            await asyncio.sleep(0.005)

    await asyncio.gather(
        approve_with_edit(),
        loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit()),
    )
    assert tool.executions == ["user's rewritten version"]  # not the model's draft


async def test_confirm_tool_rejects_an_invalid_edit_without_running(db, settings):
    """A person can still typo an edit (EchoArgs caps text at 100 chars here).
    The edit goes through the SAME Pydantic gate the model's args did -- an
    invalid edit must not execute, and the model must be told plainly that
    nothing happened."""
    tool = ConfirmEchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "confirm_echo", "arguments": {"text": "ok"}}]},
        {"tokens": ["noted"]},
    ])
    loop, broker = make_loop(db, settings, llm, [tool], timeout=2.0)

    async def approve_with_bad_edit():
        for _ in range(200):
            if broker.pending():
                broker.resolve(broker.pending()[0].id, True, {"text": "x" * 200})
                return
            await asyncio.sleep(0.005)

    text = (await asyncio.gather(
        approve_with_bad_edit(),
        loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit()),
    ))[1]
    assert tool.executions == []  # never ran
    assert text == "noted"
    assert "invalid" in llm.calls[1]["messages"][-1]["content"].lower()


async def test_crashing_tool_reports_error_not_exception(db, settings):
    llm = FakeLLM([
        {"tool_calls": [{"name": "crash", "arguments": {"text": "x"}}]},
        {"tokens": ["sorry"]},
    ])
    loop, _ = make_loop(db, settings, llm, [CrashTool()])
    emit = CollectingEmit()
    text = await loop.run("m", [], TaskMode.GENERAL, CTX, emit)
    assert text == "sorry"  # stream survived the crash
    assert emit.of(events.TOOL_RESULT)[0]["ok"] is False


async def test_external_output_is_spotlighted_in_history(db, settings):
    llm = FakeLLM([
        {"tool_calls": [{"name": "external_fetch", "arguments": {"text": "page body"}}]},
        {"tokens": ["summarized"]},
    ])
    loop, _ = make_loop(db, settings, llm, [ExternalTool()])
    await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
    tool_msg = llm.calls[1]["messages"][-1]["content"]
    assert tool_msg.startswith("<<EXTERNAL test_source ")


class TestTextToolCallRecovery:
    """Small models often WRITE tool calls as prose JSON instead of emitting
    them structurally. Recovery must execute the intent — through all the
    normal gates — and must never widen what's allowed."""

    async def test_prose_json_call_is_recovered_and_executed(self, db, settings):
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ['I will call the tool. {"name": "echo", "parameters": {"text": "hi"}}']},
            {"tokens": ["done"]},
        ])
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == ["hi"]

    async def test_arguments_key_variant(self, db, settings):
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ['{"name": "echo", "arguments": {"text": "variant"}}']},
            {"tokens": ["ok"]},
        ])
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == ["variant"]

    async def test_recovered_confirm_tool_still_needs_approval(self, db, settings):
        """Recovery must NOT skip the human gate — timeout still denies."""
        tool = ConfirmEchoTool()
        llm = FakeLLM([
            {"tokens": ['{"name": "confirm_echo", "parameters": {"text": "risky"}}']},
            {"tokens": ["understood"]},
        ])
        loop, _ = make_loop(db, settings, llm, [tool], timeout=0.05)
        emit = CollectingEmit()
        await loop.run("m", [], TaskMode.GENERAL, CTX, emit)
        assert tool.executions == []  # denied on timeout, exactly like a native call
        assert emit.of(events.APPROVAL_REQUIRED)

    async def test_out_of_mode_text_call_not_recovered(self, db, settings):
        """Privilege separation holds: prose JSON can't summon out-of-mode tools."""
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ['{"name": "echo", "parameters": {"text": "x"}}']},
        ])
        loop, _ = make_loop(db, settings, llm, [tool])
        text = await loop.run("m", [], TaskMode.EMAIL, CTX, CollectingEmit())
        assert tool.executions == []
        assert '"echo"' in text  # left as plain text, nothing executed

    async def test_ordinary_json_in_answer_not_hijacked(self, db, settings):
        """A model ANSWERING with JSON (e.g. showing example code) must not
        trigger tools — only name+arguments-shaped objects for granted tools."""
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ['Here is sample config: {"port": 8080, "debug": true}']},
        ])
        loop, _ = make_loop(db, settings, llm, [tool])
        text = await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == []
        assert "8080" in text

    async def test_malformed_json_falls_through_gracefully(self, db, settings):
        llm = FakeLLM([{"tokens": ['{"name": "echo", "parameters": {broken']}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        text = await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert "broken" in text  # treated as a normal (odd) answer, no crash


async def test_iteration_cap_stops_runaway(db, settings):
    tool = EchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "echo", "arguments": {"text": str(i)}}]} for i in range(10)
    ])
    loop, _ = make_loop(db, settings, llm, [tool], max_iterations=3)
    emit = CollectingEmit()
    await loop.run("m", [], TaskMode.GENERAL, CTX, emit)
    assert len(tool.executions) == 3  # hard stop
    assert any("limit" in d["text"] for d in emit.of(events.STATUS))


class TestPrettyPrintedToolCalls:
    """Regression, found with qwen2.5-coder:14b. The salvage scan looked for the
    literal strings '{"' and '{ "', so a PRETTY-PRINTED call opening with
    '{\\n  "' was never found. On screen: Arthur says "I'll search for it",
    prints a JSON code block, and stops. Every turn. Pretty-printing is the
    default for coder-tuned models."""

    def test_recovers_a_pretty_printed_call(self):
        text = (
            "Sure, let me look.\n\n```json\n{\n"
            '  "name": "find_files",\n'
            '  "arguments": {\n    "pattern": "login.css"\n  }\n'
            "}\n```\n\nThen I'll change the colours."
        )
        assert recover_text_tool_call(text) == {
            "name": "find_files", "arguments": {"pattern": "login.css"},
        }

    def test_recovers_with_windows_line_endings(self):
        text = '{\r\n  "name": "read_file",\r\n  "arguments": {"path": "a.py"}\r\n}'
        assert recover_text_tool_call(text)["name"] == "read_file"

    def test_still_recovers_compact_and_spaced_forms(self):
        compact = '{"name": "read_file", "arguments": {"path": "a.py"}}'
        spaced = '{ "name": "read_file", "arguments": {"path": "a.py"}}'
        assert recover_text_tool_call(compact)["name"] == "read_file"
        assert recover_text_tool_call(spaced)["name"] == "read_file"

    def test_plain_prose_is_still_not_a_tool_call(self):
        assert recover_text_tool_call("I had a look and found nothing useful.") is None

    def test_recovers_a_call_with_no_arguments(self):
        """`{}` is falsy, so the old `arguments or parameters or args` chain
        silently rejected zero-argument calls — and those are exactly what a
        model reaches for first when orienting itself in a project."""
        assert recover_text_tool_call('{"name": "list_files", "arguments": {}}') == {
            "name": "list_files", "arguments": {},
        }

    async def test_a_pretty_printed_call_actually_executes(self, db, settings):
        """The end-to-end shape of the bug: the loop must run the tool and
        continue, not return the JSON as its final answer."""
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ['{\n  "name": "echo",\n  "arguments": {\n    "text": "hi"\n  }\n}']},
            {"tokens": ["done"]},
        ])
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == ["hi"]


class TestStripToolCallJson:
    """The user should never read a raw tool-call blob. It explains nothing the
    activity row doesn't already say in English, and leaving it in the persisted
    text teaches the model that printing JSON into chat is normal."""

    def test_strips_a_fenced_call_and_keeps_the_prose(self):
        text = (
            "Let me look at that file.\n\n```json\n{\n"
            '  "name": "read_file",\n  "arguments": {"path": "a.css"}\n}\n```\n\n'
            "Then I'll change the colours."
        )
        out = strip_tool_call_json(text)
        assert "name" not in out and "```" not in out
        assert "Let me look at that file." in out
        assert "Then I'll change the colours." in out

    def test_strips_a_bare_unfenced_call(self):
        out = strip_tool_call_json('Sure.\n{"name": "echo", "arguments": {"text": "hi"}}\nDone.')
        assert out == "Sure.\n\nDone."

    def test_strips_several_calls_in_one_message(self):
        text = ('{"name": "a", "arguments": {}}\nmiddle\n{"name": "b", "arguments": {}}')
        assert strip_tool_call_json(text).strip() == "middle"

    def test_leaves_ordinary_json_alone(self):
        """JSON the user asked to see is content, not plumbing."""
        text = 'Here is your config:\n```json\n{"port": 8080, "debug": true}\n```'
        assert strip_tool_call_json(text) == text

    def test_leaves_prose_alone(self):
        assert strip_tool_call_json("No JSON here.") == "No JSON here."

    def test_strips_an_unparseable_call_from_the_brace_onward(self):
        """The unescaped-field case: raw file content runs to the end and
        strict decoding fails, but it is still a blob nobody should read."""
        text = 'Updating it now.\n{"name": "write_file", "arguments": {"content": "a "quote" broke it'
        assert strip_tool_call_json(text) == "Updating it now."

    async def test_the_draft_is_replaced_on_screen(self, db, settings):
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ['Working on it.\n{"name": "echo", "arguments": {"text": "hi"}}']},
            {"tokens": ["done"]},
        ])
        loop, _ = make_loop(db, settings, llm, [tool])
        emit = CollectingEmit()
        await loop.run("m", [], TaskMode.GENERAL, CTX, emit)
        replaced = emit.of(events.DRAFT_REPLACE)
        assert replaced and replaced[-1]["content"] == "Working on it."

    async def test_the_json_never_reaches_the_persisted_text(self, db, settings):
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ['Working on it.\n{"name": "echo", "arguments": {"text": "hi"}}']},
            {"tokens": [" All set."]},
        ])
        loop, _ = make_loop(db, settings, llm, [tool])
        final = await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert '"name"' not in final and "Working on it." in final


class TestOutOfModeTextCall:
    """Observed: in GENERAL mode a coder model wrote read_file/edit_file JSON
    into the chat. Nothing could run it, so the user was left reading machine
    syntax that described a non-event, with no clue the mode was the reason."""

    async def test_json_is_stripped_even_though_it_cannot_run(self, db, settings):
        llm = FakeLLM([{"tokens": ['Let me read it.\n{"name": "read_file", "arguments": {"path": "a.css"}}']}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        emit = CollectingEmit()
        text = await loop.run("m", [], TaskMode.GENERAL, CTX, emit)
        assert '"name"' not in text
        assert emit.of(events.DRAFT_REPLACE)[-1]["content"] == "Let me read it."

    async def test_the_user_is_told_the_mode_is_the_problem(self, db, settings):
        llm = FakeLLM([{"tokens": ['{"name": "read_file", "arguments": {"path": "a.css"}}']}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        emit = CollectingEmit()
        await loop.run("m", [], TaskMode.GENERAL, CTX, emit)
        status = " ".join(d["text"] for d in emit.of(events.STATUS))
        assert "read_file" in status and "general mode" in status.lower()

    async def test_an_available_tool_still_runs_normally(self, db, settings):
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ['{"name": "echo", "arguments": {"text": "hi"}}']},
            {"tokens": ["done"]},
        ])
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == ["hi"]


class TestForcedToolCall:
    """Why a turn "just stops": the loop ends when the model asks for no tools.
    That is right for an assistant that finished answering and wrong for one
    that finished writing a PLAN — "1. Use find_files with '*login*' … let me
    know what you find!" — which is what small models do constantly.

    The fix is not another prompt. chat_json compiles a schema into a decoding
    grammar, so a plan, an apology, or malformed JSON are all unreachable: the
    only legal outputs are a tool name, then that tool's arguments."""

    async def test_a_described_step_becomes_a_real_call(self, db, settings):
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ["Here's the plan:\n1. Use echo with 'hi'.\nLet me know!"]},
            {"tokens": ["done"]},
        ])
        llm.json_turns = [{"tool": "echo"}, {"text": "hi"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        text = await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == ["hi"]      # the plan became a call
        assert text.endswith("done")

    async def test_the_name_is_picked_from_an_enum_of_real_tools(self, db, settings):
        llm = FakeLLM([{"tokens": ["I will use echo next."]}, {"tokens": ["ok"]}])
        llm.json_turns = [{"tool": "echo"}, {"text": "x"}]
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        schema = next(c["schema"] for c in llm.calls if "schema" in c)
        assert schema["properties"]["tool"]["enum"] == ["echo", "none"]

    async def test_args_come_from_that_tools_own_schema(self, db, settings):
        llm = FakeLLM([{"tokens": ["I will use echo next."]}, {"tokens": ["ok"]}])
        llm.json_turns = [{"tool": "echo"}, {"text": "x"}]
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        schemas = [c["schema"] for c in llm.calls if "schema" in c]
        assert "text" in schemas[1]["properties"]

    async def test_none_is_a_real_answer_not_a_failure(self, db, settings):
        """A model may NAME a tool while explaining rather than intending to
        use one. Forcing a call there would invent work nobody asked for."""
        tool = EchoTool()
        llm = FakeLLM([{"tokens": ["The echo tool would repeat your text."]}])
        llm.json_turns = [{"tool": "none"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == []

    async def test_an_ordinary_answer_survives_the_check(self, db, settings):
        """Every toolless turn now gets asked "did you mean to use one?", so a
        plain answer costs ONE short constrained call and is otherwise
        untouched. The trigger used to require the reply to name a tool, which
        missed the worse case: a model that skips straight to inventing the
        answer ("I've scanned your folder and found: README.md, project.py…"
        for files that do not exist)."""
        tool = EchoTool()
        llm = FakeLLM([{"tokens": ["Paris is the capital of France."]}])
        llm.json_turns = [{"tool": "none"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        text = await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert text == "Paris is the capital of France."
        assert tool.executions == []
        assert len([c for c in llm.calls if "schema" in c]) == 1  # name only, no args call

    async def test_a_fabricated_result_gets_a_real_call(self, db, settings):
        """The observed failure: no tool named, nothing run, three invented
        filenames presented as fact."""
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ["I've scanned your folder: README.md, project.py, data.csv"]},
            {"tokens": ["here is what is actually there"]},
        ])
        llm.json_turns = [{"tool": "echo"}, {"text": "listing"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == ["listing"]

    async def test_a_failure_to_force_leaves_the_answer_intact(self, db, settings):
        """chat_json returning nothing (a model that could not produce the
        shape) must degrade to the old behaviour, not break the turn."""
        llm = FakeLLM([{"tokens": ["I'll use echo."]}])
        llm.json_turns = []     # the constrained call yields nothing
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        text = await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert text == "I'll use echo."

    async def test_forcing_gives_up_rather_than_looping(self, db, settings):
        llm = FakeLLM([{"tokens": ["I'll use echo."]} for _ in range(6)])
        llm.json_turns = []
        loop, _ = make_loop(db, settings, llm, [EchoTool()], max_iterations=6)
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        # One streamed turn, then MAX_FORCED attempts at the name -- and then
        # it stops rather than spending the whole iteration budget.
        assert len([c for c in llm.calls if "schema" in c]) <= MAX_FORCED

    async def test_never_forces_a_repeat_of_a_call_that_already_ran(self, db, settings):
        """Observed loop: list_files ran, the model answered "I see three items…
        anything specific you'd like me to do?", the forced check asked "which
        tool did you mean?", it picked list_files again — and the same paragraph
        appeared on screen twice."""
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ["Let me look."]},
            {"tokens": ["I see three items. Anything specific?"]},
            {"tokens": ["I see three items. Anything specific?"]},
        ])
        # First force picks echo; the second would pick the SAME call again.
        llm.json_turns = [
            {"tool": "echo"}, {"text": "listing"},
            {"tool": "echo"}, {"text": "listing"},
        ]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == ["listing"]     # ran once, not twice

    async def test_a_different_next_step_is_still_forced(self, db, settings):
        """Deduping must not block multi-step work: a second, DIFFERENT call is
        exactly what a multi-file task needs."""
        tool = EchoTool()
        llm = FakeLLM([
            {"tokens": ["Let me look."]},
            {"tokens": ["Now the other one."]},
            {"tokens": ["done"]},
        ])
        llm.json_turns = [
            {"tool": "echo"}, {"text": "first"},
            {"tool": "echo"}, {"text": "second"},
        ]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        assert tool.executions == ["first", "second"]

    async def test_no_forcing_in_a_mode_with_no_tools(self, db, settings):
        llm = FakeLLM([{"tokens": ["I would use echo if I could."]}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        await loop.run("m", [], TaskMode.CODE, CTX, CollectingEmit())
        assert len(llm.calls) == 1


class TestForceWriteAfterPrintedFile:
    """Observed failure, twice, verbatim from real sessions: asked to recolour
    login.css, the model read the file, printed CSS into the CHAT, and stopped.
    The user said "I authorize you, apply it" and got the same block printed
    again. Nothing was ever staged.

    `_forced_tool_call` cannot rescue this, because its PICK step offers "none"
    as a legal answer and a model that just showed the user a finished file
    honestly believes that WAS the deliverable.

    The hard part is not forcing the call — it is telling a WHOLE FILE from an
    EXCERPT. In the second session the model printed only the two rules it
    changed; writing that as the file would have deleted sixty lines of working
    CSS, including the background image the user had explicitly asked to keep.
    """

    # Stands in for a real file: long enough that an excerpt of it is obviously
    # an excerpt.
    FILE = "\n".join(f"line {i}" for i in range(40))
    WHOLE = "\n".join(f"changed {i}" for i in range(40))
    EXCERPT = "\n".join(f"changed {i}" for i in range(6))

    @staticmethod
    def code_ctx(tmp_path, contents=None):
        """A ToolContext with a real changeset over a real folder — the forced
        write reads the current file to decide rewrite-vs-fragment, so a bare
        context would skip the interesting half of this class."""
        root = tmp_path / "proj"
        root.mkdir(exist_ok=True)
        for name, text in (contents or {}).items():
            (root / name).write_text(text)
        return ToolContext(conversation_id="conv1", workspace_root=str(root),
                           changes=ChangeSet(root=str(root)))

    async def test_a_printed_file_becomes_a_real_write(self, db, settings, tmp_path):
        tool = WriteFileTool()
        llm = FakeLLM([
            {"tokens": [f"Here's the updated CSS:\n```css\n{self.WHOLE}\n```\nLet me know!"]},
            {"tokens": ["Done — I've updated login.css."]},
        ])
        llm.json_turns = [{"path": "login.css"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        ctx = self.code_ctx(tmp_path, {"login.css": self.FILE})
        text = await loop.run("m", [], TaskMode.CODE, ctx, CollectingEmit())

        assert [p for p, _ in tool.writes] == ["login.css"]
        assert tool.writes[0][1].rstrip("\n") == self.WHOLE
        assert text.endswith("Done — I've updated login.css.")

    async def test_it_never_asks_the_model_to_re_emit_the_file(self, db, settings, tmp_path):
        """THE BUG THIS FIXES. The first version asked for the whole file back
        as a JSON string through constrained decoding. An 80-line file emitted
        character-by-character through a grammar does not finish inside the
        timeout on a small local model — so the call raised, recovery returned
        None, and the turn ended having done nothing at all.

        Only the path is ever asked for; the content comes from the block that
        is already on screen."""
        tool = WriteFileTool()
        llm = FakeLLM([
            {"tokens": [f"```css\n{self.WHOLE}\n```"]},
            {"tokens": ["done"]},
        ])
        llm.json_turns = [{"path": "login.css"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.CODE,
                       self.code_ctx(tmp_path, {"login.css": self.FILE}), CollectingEmit())

        asked = next(c["schema"] for c in llm.calls if "schema" in c)
        assert list(asked["properties"]) == ["path"]   # no `content`, no `tool` enum

    async def test_an_excerpt_never_replaces_the_whole_file(self, db, settings, tmp_path):
        """THE DESTRUCTIVE CASE. Printing just the rules you changed is normal,
        correct model behaviour. Reading that as a replacement would be our bug,
        and it would silently delete the rest of the user's file."""
        write, edit = WriteFileTool(), EditFileTool()
        llm = FakeLLM([
            {"tokens": [f"Change these two rules:\n```css\n{self.EXCERPT}\n```"]},
            {"tokens": ["done"]},
        ])
        llm.json_turns = [
            {"path": "login.css"},
            {"path": "login.css", "old_text": "line 3", "new_text": "changed 3"},
        ]
        loop, _ = make_loop(db, settings, llm, [write, edit])
        await loop.run("m", [], TaskMode.CODE,
                       self.code_ctx(tmp_path, {"login.css": self.FILE}), CollectingEmit())

        assert write.writes == []                        # nothing was flattened
        assert edit.edits == [("login.css", "line 3", "changed 3")]

    async def test_a_brand_new_file_is_written_even_though_it_is_short(
        self, db, settings, tmp_path,
    ):
        """The size guard compares against what is already there. With nothing
        there, any block is the whole file."""
        tool = WriteFileTool()
        block = "\n".join(f"new {i}" for i in range(6))
        llm = FakeLLM([{"tokens": [f"```py\n{block}\n```"]}, {"tokens": ["done"]}])
        llm.json_turns = [{"path": "brand_new.py"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.CODE, self.code_ctx(tmp_path), CollectingEmit())
        assert [p for p, _ in tool.writes] == ["brand_new.py"]

    async def test_the_edit_keeps_the_path_already_resolved(self, db, settings, tmp_path):
        """Two chances to name the file is two chances to name a different one,
        and the second call has no better information than the first."""
        write, edit = WriteFileTool(), EditFileTool()
        llm = FakeLLM([{"tokens": [f"```css\n{self.EXCERPT}\n```"]}, {"tokens": ["done"]}])
        llm.json_turns = [
            {"path": "login.css"},
            {"path": "SOMETHING_ELSE.css", "old_text": "line 3", "new_text": "x"},
        ]
        loop, _ = make_loop(db, settings, llm, [write, edit])
        await loop.run("m", [], TaskMode.CODE,
                       self.code_ctx(tmp_path, {"login.css": self.FILE}), CollectingEmit())
        assert edit.edits[0][0] == "login.css"

    async def test_a_path_outside_the_folder_is_refused(self, db, settings, tmp_path):
        write = WriteFileTool()
        llm = FakeLLM([{"tokens": [f"```css\n{self.WHOLE}\n```"]}])
        llm.json_turns = [{"path": "../../etc/passwd"}, {"tool": "none"}]
        loop, _ = make_loop(db, settings, llm, [write])
        await loop.run("m", [], TaskMode.CODE,
                       self.code_ctx(tmp_path, {"login.css": self.FILE}), CollectingEmit())
        assert write.writes == []

    async def test_falls_back_to_the_general_picker_without_write_file(self, db, settings):
        """No write_file granted this mode -> nothing to force; the ordinary
        PICK-with-"none" flow still runs so a genuinely toolless turn isn't
        broken by this check."""
        tool = EchoTool()
        llm = FakeLLM([{"tokens": [f"```css\n{self.WHOLE}\n```"]}])
        llm.json_turns = [{"tool": "none"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit())
        schema = next(c["schema"] for c in llm.calls if "schema" in c)
        assert schema["properties"]["tool"]["enum"] == ["echo", "none"]

    async def test_no_changeset_means_no_forcing(self, db, settings):
        """Without one there is nothing to stage into AND no way to read the
        current file — so no way to tell a rewrite from an excerpt. Refusing to
        guess is the point of the guard."""
        tool = WriteFileTool()
        llm = FakeLLM([{"tokens": [f"```css\n{self.WHOLE}\n```"]}])
        llm.json_turns = [{"tool": "none"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.CODE, CTX, CollectingEmit())
        assert tool.writes == []

    async def test_a_short_block_does_not_trigger_forcing(self, db, settings, tmp_path):
        """A small inline example (below _MIN_BLOCK_LINES) is not a file the
        user asked to save, and must not be forced into a write."""
        tool = WriteFileTool()
        llm = FakeLLM([{"tokens": ["```css\n.a { color: red; }\n```"]}])
        llm.json_turns = [{"tool": "none"}]
        loop, _ = make_loop(db, settings, llm, [tool])
        await loop.run("m", [], TaskMode.CODE, self.code_ctx(tmp_path), CollectingEmit())
        assert tool.writes == []


class TestForceAfterAClaimedChange:
    """THE FAILURE THIS EXISTS FOR, verbatim: the user said "go ahead apply it,
    no need to show the code to me", the model obliged — prose only, no fenced
    block — and answered "Changes staged for review." Nothing had been staged;
    it was echoing write_file's own description back.

    Recovery used to require a printed code block, which quietly made the safety
    net depend on the model being verbose. So the one instruction that most
    clearly means "just do it" was the one instruction that switched it off."""

    FILE = "\n".join(f"line {i}" for i in range(40))

    @staticmethod
    def code_ctx(tmp_path, contents=None):
        root = tmp_path / "proj"
        root.mkdir(exist_ok=True)
        for name, text in (contents or {}).items():
            (root / name).write_text(text)
        return ToolContext(conversation_id="conv1", workspace_root=str(root),
                           changes=ChangeSet(root=str(root)))

    async def test_a_claim_with_no_code_block_still_forces_a_real_edit(
        self, db, settings, tmp_path,
    ):
        write, edit = WriteFileTool(), EditFileTool()
        llm = FakeLLM([
            {"tokens": ["Sure, I'll edit login.css with the new colors. "
                        "Changes staged for review."]},
            {"tokens": ["done"]},
        ])
        llm.json_turns = [
            {"path": "login.css"},
            {"path": "login.css", "old_text": "line 3", "new_text": "changed 3"},
        ]
        loop, _ = make_loop(db, settings, llm, [write, edit])
        await loop.run("m", [], TaskMode.CODE,
                       self.code_ctx(tmp_path, {"login.css": self.FILE}), CollectingEmit())

        assert edit.edits == [("login.css", "line 3", "changed 3")]
        # Nothing was printed, so there is no content to write wholesale — and
        # inventing one would be exactly the destructive guess this avoids.
        assert write.writes == []

    async def test_a_stated_plan_is_not_a_claim(self, db, settings, tmp_path):
        """"I'll edit login.css" is future tense. Forcing there would invent
        work off a sentence the model had not finished acting on."""
        write, edit = WriteFileTool(), EditFileTool()
        llm = FakeLLM([{"tokens": ["I'll edit login.css next, once you confirm."]}])
        llm.json_turns = [{"tool": "none"}]
        loop, _ = make_loop(db, settings, llm, [write, edit])
        await loop.run("m", [], TaskMode.CODE,
                       self.code_ctx(tmp_path, {"login.css": self.FILE}), CollectingEmit())
        assert write.writes == [] and edit.edits == []

    async def test_an_ordinary_answer_is_not_a_claim(self, db, settings, tmp_path):
        write, edit = WriteFileTool(), EditFileTool()
        llm = FakeLLM([{"tokens": ["That file sets the login page colours."]}])
        llm.json_turns = [{"tool": "none"}]
        loop, _ = make_loop(db, settings, llm, [write, edit])
        await loop.run("m", [], TaskMode.CODE,
                       self.code_ctx(tmp_path, {"login.css": self.FILE}), CollectingEmit())
        assert write.writes == [] and edit.edits == []

    def test_the_phrases_it_catches_and_the_ones_it_leaves(self):
        assert claims_a_file_change("Changes staged for review.")
        assert claims_a_file_change("I've updated the file.")
        assert claims_a_file_change("The file has been updated.")
        assert claims_a_file_change("I have applied the changes")
        assert not claims_a_file_change("I'll edit login.css")
        assert not claims_a_file_change("Shall I update it?")
        assert not claims_a_file_change("Here is what the file does.")


class TestCapabilityNote:
    """Regression: asked to send an email in Code mode, the model answered
    "Done. Email sent to …" without calling anything. Mode scoping stopped the
    tool from running; nothing stopped the model from claiming it had."""

    async def test_note_lists_the_tools_actually_available(self, db, settings):
        llm = FakeLLM([{"content": "ok"}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        messages = [{"role": "system", "content": "You are Arthur."}]
        await loop.run("m", messages, TaskMode.GENERAL, CTX, CollectingEmit())
        sent = llm.calls[0]["messages"][0]["content"]
        assert "GENERAL mode" in sent and "echo" in sent
        assert "NEVER say an action is done" in sent

    async def test_the_note_never_forbids_something_it_just_granted(self, db, settings):
        """THE BUG: the "you cannot do this" examples were a fixed sentence
        ending "...running code, editing files". True in most modes, FALSE in
        the one mode built for editing files — so Code mode granted `edit_file`
        and then told the model editing files was impossible. Asked to scan a
        folder, a 7B believed the prohibition: "I don't have access to files or
        folders on your computer."
        """
        from tools.coding import EditFileTool, ReadFileTool

        llm = FakeLLM([{"content": "ok"}])
        loop, _ = make_loop(db, settings, llm, [ReadFileTool(), EditFileTool()])
        await loop.run("m", [{"role": "system", "content": "x"}], TaskMode.CODE, CTX,
                       CollectingEmit())
        note = llm.calls[0]["messages"][0]["content"]
        cannot = note.split("You CANNOT do these here:")[1]
        assert "edit_file" in note                 # granted
        assert "editing files" not in cannot       # and not forbidden in the same breath
        assert "sending or reading email" in cannot

    async def test_a_mode_without_file_tools_is_still_told_so(self, db, settings):
        llm = FakeLLM([{"content": "ok"}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        await loop.run("m", [{"role": "system", "content": "x"}], TaskMode.GENERAL, CTX,
                       CollectingEmit())
        cannot = llm.calls[0]["messages"][0]["content"].split("You CANNOT do these here:")[1]
        assert "reading or editing files" in cannot

    async def test_note_says_none_when_the_mode_has_no_tools(self, db, settings):
        llm = FakeLLM([{"content": "ok"}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        # EchoTool is GENERAL/RESEARCH only, so Code mode grants nothing.
        await loop.run("m", [{"role": "system", "content": "x"}], TaskMode.CODE, CTX,
                       CollectingEmit())
        assert "actions this turn are: none" in llm.calls[0]["messages"][0]["content"]

    async def test_callers_message_list_is_not_mutated(self, db, settings):
        """The note is per-turn. Mutating in place would stack a copy onto the
        system prompt on every iteration of a long conversation."""
        llm = FakeLLM([{"content": "ok"}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        messages = [{"role": "system", "content": "You are Arthur."}]
        await loop.run("m", messages, TaskMode.GENERAL, CTX, CollectingEmit())
        assert messages[0]["content"] == "You are Arthur."

    async def test_note_is_added_even_without_a_system_message(self, db, settings):
        llm = FakeLLM([{"content": "ok"}])
        loop, _ = make_loop(db, settings, llm, [EchoTool()])
        await loop.run("m", [{"role": "user", "content": "hi"}], TaskMode.GENERAL, CTX,
                       CollectingEmit())
        assert llm.calls[0]["messages"][0]["role"] == "system"


async def test_per_run_cap_overrides_the_constructor_default(db, settings):
    """The right ceiling is a property of the task, not of the loop: Code mode
    needs dozens of calls where Email needs two, so the caller passes it."""
    tool = EchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "echo", "arguments": {"text": str(i)}}]} for i in range(10)
    ])
    loop, _ = make_loop(db, settings, llm, [tool], max_iterations=2)
    await loop.run("m", [], TaskMode.GENERAL, CTX, CollectingEmit(), max_iterations=5)
    assert len(tool.executions) == 5


async def test_code_mode_warns_that_staged_edits_may_be_half_finished(db, settings):
    """Hitting the cap mid-edit leaves a PARTIAL changeset that looks identical
    to a finished one in the review panel. The user has to be told."""
    tool = EchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "echo", "arguments": {"text": str(i)}}]} for i in range(10)
    ])
    loop, _ = make_loop(db, settings, llm, [tool], max_iterations=2)
    emit = CollectingEmit()
    await loop.run("m", [], TaskMode.CODE, CTX, emit)
    assert any("half-finished" in d["text"] for d in emit.of(events.STATUS))


async def test_other_modes_get_no_such_warning(db, settings):
    tool = EchoTool()
    llm = FakeLLM([
        {"tool_calls": [{"name": "echo", "arguments": {"text": str(i)}}]} for i in range(10)
    ])
    loop, _ = make_loop(db, settings, llm, [tool], max_iterations=2)
    emit = CollectingEmit()
    await loop.run("m", [], TaskMode.GENERAL, CTX, emit)
    assert not any("half-finished" in d["text"] for d in emit.of(events.STATUS))


class TestRecoverTextToolCall:
    """Unit-level coverage for the text-tool-call salvage path, independent
    of the full agent loop. Regression source: a real run_python call whose
    code contained an unescaped f-string quote (@app.post("/rename/...")),
    which broke strict JSON decoding entirely and, before the anchored
    salvage rewrite, also broke the naive regex-scan fallback -- it mistook
    a `{"error": "..."}` dict literal INSIDE the code for a second top-level
    argument and truncated the recovered code at that point."""

    def test_well_formed_json_uses_the_fast_path(self):
        text = '{"name": "run_python", "arguments": {"code": "print(1)"}}'
        assert recover_text_tool_call(text) == {"name": "run_python", "arguments": {"code": "print(1)"}}

    def test_no_call_present_returns_none(self):
        assert recover_text_tool_call("just a normal reply, no tool call here") is None

    def test_prose_before_the_json_blob_is_skipped(self):
        text = 'Sure, here you go: {"name": "run_python", "arguments": {"code": "print(2)"}}'
        assert recover_text_tool_call(text) == {"name": "run_python", "arguments": {"code": "print(2)"}}

    def test_properly_escaped_quotes_round_trip_untouched(self):
        text = '{"name":"run_python","arguments":{"code":"print(\\"hi\\")\\nprint(2)"}}'
        assert recover_text_tool_call(text)["arguments"]["code"] == 'print("hi")\nprint(2)'

    def test_unescaped_quotes_in_a_single_field_are_salvaged(self):
        """The exact regression: an f-string quote and an embedded dict
        literal inside run_python's code, plus the model rambling past its
        own closing brace. Recovery must capture the WHOLE code block, not
        truncate at the embedded {"error": ...} lookalike."""
        text = (
            '{"name":"run_python","parameters":{"code":"import os\n'
            'from fastapi import FastAPI\n\n'
            'app = FastAPI()\n\n'
            '@app.post("/rename/{filename}")\n'
            'async def rename_filename(filename: str = None):\n'
            '    if filename is None:\n'
            '        return JSONResponse({"error": "Missing filename"}, status_code=400)\n'
            '    os.rename(filename, f"{filename[:-4]}_new.txt")"}}\n\n'
            '# Run the FastAPI application\n'
            'import uvicorn\n'
            'if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8000)"}}'
        )
        result = recover_text_tool_call(text)
        assert result["name"] == "run_python"
        code = result["arguments"]["code"]
        assert '@app.post("/rename/{filename}")' in code
        assert "uvicorn.run(app" in code

    def test_unescaped_quotes_in_the_last_of_two_fields_are_salvaged(self):
        """write_file has a clean leading field (path) followed by a messy
        one (content). The leading field must not be over-captured, and the
        embedded dict/tuple lookalikes in content must not truncate it."""
        text = (
            '{"name":"write_file","arguments":{"path":"notes/app.py","content":"'
            'x = {"k": "v"}\nprint(f"hello {x}")\ny = ("a", "b")"}}'
        )
        result = recover_text_tool_call(text)
        assert result["name"] == "write_file"
        assert result["arguments"]["path"] == "notes/app.py"
        assert 'x = {"k": "v"}' in result["arguments"]["content"]
        assert 'y = ("a", "b")' in result["arguments"]["content"]

    def test_missing_colon_before_parameters_brace_is_tolerated(self):
        """Regression: a real email_send call where the model dropped the ':'
        between "parameters" and its opening brace, AND wrapped the `to` list
        in an extra pair of quotes ("to": "[\"x@x.com\"]" instead of a real
        JSON array). Before this fix m_params never matched at all -- the
        missing colon meant recovery gave up instantly and the user saw the
        raw JSON blob as chat text."""
        text = (
            '{"name":"email_send","parameters {"to": "["drachir102175@gmail.com"]", '
            '"subject": "Meeting Invitation", "body": "Hello,\\n\\nWe have a meeting '
            'scheduled for 2 pm. I look forward to seeing you then.\\nBest '
            'regards,\\n[Your Name]"}}'
        )
        result = recover_text_tool_call(text)
        assert result["name"] == "email_send"
        assert result["arguments"]["to"] == ["drachir102175@gmail.com"]
        assert result["arguments"]["subject"] == "Meeting Invitation"
        assert result["arguments"]["body"].startswith("Hello,\n\nWe have a meeting")
        assert result["arguments"]["body"].endswith("[Your Name]")

    def test_array_field_that_fails_to_parse_falls_back_to_raw_string(self):
        """If the bracketed content isn't valid JSON even after unwrapping,
        recovery must not crash -- it keeps the raw text so Pydantic can
        reject it cleanly as an invalid argument instead of the loop blowing
        up on a malformed call."""
        text = '{"name":"email_send","arguments":{"to": "[not valid json]", "subject": "s", "body": "b"}}'
        result = recover_text_tool_call(text)
        assert result["arguments"]["to"] == "[not valid json]"
