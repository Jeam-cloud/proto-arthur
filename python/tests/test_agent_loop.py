"""Agent loop behavior under a deliberately unreliable model."""

import asyncio

from agent.loop import AgentLoop, recover_text_tool_call
from agent.registry import ToolRegistry
from core import events
from security.approvals import ApprovalBroker
from security.audit import AuditLog
from security.gateway import SecurityGateway
from tests.fakes import (
    CollectingEmit, ConfirmEchoTool, CrashTool, EchoTool, ExternalTool, FakeLLM, FakeScanner,
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
