"""The agent loop: model -> (tokens | tool calls) -> execute -> feed back -> repeat.

Built from scratch (no framework) because the loop IS the product's security
core; every line must be auditable. Frameworks hide exactly the steps we need
to interpose on: approval gates, output scanning, capability checks.

Failure design — the model is assumed to be unreliable:
  * malformed args        -> validation error goes back as a tool result; the
                             model gets a chance to correct itself
  * unknown/out-of-mode   -> "tool not available" result (never an exception;
                             injected text asking for email_send in research
                             mode just gets told no)
  * tool crash            -> error result, loop continues
  * runaway               -> hard iteration cap from Settings
  * denial                -> the model is TOLD the user declined, so it can
                             answer gracefully instead of hallucinating success
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from agent.registry import ToolRegistry
from core import events
from core.ollama_client import OllamaClient
from security.approvals import ApprovalBroker
from security.gateway import SecurityGateway
from tools.base import Risk, TaskMode, ToolContext

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


def recover_text_tool_call(text: str) -> dict[str, Any] | None:
    """Salvage a tool call the model wrote as PROSE instead of emitting
    structurally — e.g. printing {"name": "email_send", "parameters": {...}}
    into the chat. Small local models do this regularly (their chat template
    fails to catch the tool syntax), and without recovery the user sees JSON
    soup and nothing happens.

    Safe to be lenient here: a recovered call goes through the exact same
    gates as a native one — mode check, Pydantic validation, approval dialog.
    Recovery changes how the request is DETECTED, never what it's ALLOWED."""
    import json

    decoder = json.JSONDecoder()
    idx = 0
    while True:
        start = _next_object(text, idx)
        if start == -1:
            break
        try:
            obj, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if isinstance(obj, dict):
            name = obj.get("name")
            args = obj.get("arguments") or obj.get("parameters") or obj.get("args")
            if isinstance(name, str) and isinstance(args, dict):
                return {"name": name, "arguments": args}
        idx = start + 1

    # Strict decoding never found a valid call. This is the common case for
    # run_python/write_file/email_send: the model's free-text field (code,
    # content, body) contains literal quotes or newlines it never escaped, so
    # the blob isn't valid JSON at all, and the user would otherwise see raw
    # JSON soup with a code block embedded in it (this is exactly what
    # happened with a run_python call whose code contained an unescaped
    # f-string quote). Try one more salvage pass before giving up.
    start = _next_object(text, 0)
    if start != -1:
        return _salvage_unescaped_field(text, start)
    return None


# `{` then ANY whitespace then `"`. The whitespace is the whole point.
#
# THE BUG THIS FIXES. This scan used to look for the literal strings '{"' and
# '{ "', which matches compact JSON and nothing else. A model that pretty-prints
# its call —
#
#     {
#       "name": "find_files",
#       "arguments": {"pattern": "login.css"}
#     }
#
# — opens with '{\n  "', so the scan never found it, recovery never ran, and the
# loop treated a tool call as the final answer. On screen that looked like
# Arthur announcing "I'll search for it", printing a JSON code block, and
# stopping dead. Every turn. Pretty-printing is the DEFAULT for coder-tuned
# models, which is why it showed up the moment a coder model was used.
_OBJ_START = re.compile(r'\{\s*"')


def _next_object(text: str, idx: int) -> int:
    m = _OBJ_START.search(text, idx)
    return m.start() if m else -1


def _salvage_unescaped_field(text: str, start: int) -> dict[str, Any] | None:
    """Recovers {"name": ..., "arguments"/"parameters": {...}} when the LAST
    field in the arguments object is free text containing raw quotes/newlines
    that break strict JSON parsing. Every tool whose schema has one large
    text field (run_python's code, write_file's content, email_send's body)
    declares that field last, so "take everything from the last field's
    opening quote to the end of the blob, literally" recovers exactly the
    payload the model meant to send.

    Field boundaries are found only by anchoring immediately after
    `params_start` or immediately after a PRECEDING field's own comma —
    never by scanning ahead into a value for something that merely looks
    like "key": "value". That distinction matters: free text is exactly the
    kind of content that contains lookalikes of its own (a Python dict
    literal, an embedded JSON fragment) and an unanchored scan mistakes those
    for the next real argument, truncating the recovered text at the first
    one. Anchoring rules that out structurally instead of by luck.

    Recovery result still goes through Pydantic validation before anything
    runs, so a wrong guess here surfaces as a normal "invalid arguments" tool
    result, not a security hole."""
    import json
    import re

    m_name = re.search(r'"name"\s*:\s*"([a-zA-Z_][a-zA-Z0-9_]*)"', text[start:])
    if not m_name:
        return None
    name = m_name.group(1)

    # Both the colon AND the key's closing quote are optional here.
    #
    # The colon being optional was already intended. What this missed is WHERE
    # the quote lands when a model drops the colon: it does not write
    # `"parameters" {`, it writes `"parameters {"` -- the brace ends up INSIDE
    # the quoted key and the closing quote lands after it. The old pattern
    # required `"parameters"` as a complete token before the brace, so it never
    # matched the real malformation and recovery returned None for every
    # email_send call that hit it.
    #
    # Not consuming that trailing quote is deliberate: in the malformed form it
    # is exactly the opening quote of the first argument, so leaving it makes
    # the remainder parse identically to the well-formed case below.
    m_params = re.search(r'"(?:arguments|parameters|args)\s*"?\s*:?\s*\{', text[start:])
    if not m_params:
        return None
    rest = text[start + m_params.end():]

    # A clean leading field: value has no unescaped quote AND is immediately
    # followed by a comma. Requiring the comma is what keeps this from ever
    # matching partway into a free-text value -- a genuine next field is
    # comma-separated; a lookalike inside a value usually isn't followed by
    # one at exactly that spot.
    clean_field = re.compile(r'\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,')
    final_field = re.compile(r'\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:\s*"')
    # List-typed args (email_send's `to`/`cc`/`bcc`) sometimes come back as a
    # JSON array that the model then wrapped in an extra pair of quotes --
    # "to": "["a@x.com"]" instead of "to": ["a@x.com"]. clean_field can't match
    # that (the bare quote right after "[" looks like the value's closing
    # quote), so it's tried FIRST and, when it parses, produces a real list
    # instead of a string Pydantic would reject outright.
    array_field = re.compile(r'\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:\s*"(\[[^\]]*\])"\s*,')

    arguments: dict[str, Any] = {}
    pos = 0
    while True:
        m = array_field.match(rest, pos)
        if m:
            try:
                arguments[m.group(1)] = json.loads(m.group(2))
            except json.JSONDecodeError:
                arguments[m.group(1)] = _unescape(m.group(2))
            pos = m.end()
            continue
        m = clean_field.match(rest, pos)
        if not m:
            break
        arguments[m.group(1)] = _unescape(m.group(2))
        pos = m.end()

    m_final = final_field.match(rest, pos)
    if not m_final:
        return {"name": name, "arguments": arguments} if arguments else None

    field = m_final.group(1)
    value_start = m_final.end()
    tail = rest.rstrip()
    end = len(tail)
    while end > value_start and tail[end - 1] == "}":
        end -= 1
    if end > value_start and tail[end - 1] == '"':
        end -= 1
    if end <= value_start:
        return None
    arguments[field] = _unescape(tail[value_start:end])

    return {"name": name, "arguments": arguments}


def _unescape(raw: str) -> str:
    """Undo the escapes a well-formed emitter WOULD have used (\\n, \\t, \\",
    \\\\), leaving any other literal character — including the raw quotes
    that broke strict parsing in the first place — exactly as written."""
    return (
        raw.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _with_capability_note(
    messages: list[dict[str, Any]], mode: TaskMode, tools: list[Any],
) -> list[dict[str, Any]]:
    """Tell the model, in the system prompt, exactly what it can do THIS TURN.

    THE BUG THIS FIXES. Mode scoping is enforced in the registry, so a tool
    outside the current mode simply does not exist in the model's world. That
    stops the tool from RUNNING — it does nothing to stop the model from saying
    it ran. Asked to send an email in Code mode, a small local model answered
    "Done. Email sent to …" and then "Opened Discord for you." Nothing was sent,
    nothing was opened, no tool was called: it pattern-matched a plausible reply
    and asserted it. Silent capability scoping plus a confident model is a
    machine for producing lies.

    The fix is to make the boundary VISIBLE. The model cannot infer what it is
    missing from an empty tool list — absence is not information — so it gets
    told: here is your complete set of actions, anything else belongs to another
    mode, say so rather than claiming it.

    Generated from the registry rather than written by hand, so it cannot drift
    out of date when a tool is added, moved between modes, or removed.
    """
    names = ", ".join(t.name for t in tools) if tools else "none"
    note = (
        f"CAPABILITIES — you are in {mode.value.upper()} mode and your ONLY available "
        f"actions this turn are: {names}.\n"
        "Arthur separates capabilities by mode on purpose, and you have no way to reach "
        "a tool that is not listed above. If the user asks for anything else — sending "
        "email, opening or controlling an app, searching the web, running code, editing "
        "files — you CANNOT do it here. Say so plainly in one sentence and name the mode "
        "that can (General, Research, Code, Email, Finance, Computer, Design); the user "
        "switches modes from the icons on the left.\n"
        "NEVER say an action is done, sent, opened, saved or created unless you actually "
        "called a tool above and saw it succeed. Claiming a completed action you did not "
        "perform is the worst thing you can do in this app."
    )
    if messages and messages[0].get("role") == "system":
        # Copy rather than mutate: `messages` belongs to the caller, and the
        # note is per-turn — appending in place would stack a fresh copy onto
        # the system prompt on every iteration of a long conversation.
        return [{**messages[0], "content": messages[0]["content"] + "\n\n" + note}, *messages[1:]]
    return [{"role": "system", "content": note}, *messages]


class AgentLoop:
    def __init__(
        self,
        llm: OllamaClient,
        registry: ToolRegistry,
        gateway: SecurityGateway,
        approvals: ApprovalBroker,
        max_iterations: int = 6,
    ):
        self._llm = llm
        self._registry = registry
        self._gateway = gateway
        self._approvals = approvals
        self._max_iterations = max_iterations

    async def run(
        self,
        model: str,
        messages: list[dict[str, Any]],
        mode: TaskMode,
        ctx: ToolContext,
        emit: Emit,
        max_iterations: int | None = None,
    ) -> str:
        """Streams via `emit`, returns the final assistant text.

        `max_iterations` overrides the constructor default for THIS run. The
        right ceiling is a property of the task, not of the loop: one email is
        two calls, editing a project is forty. Passing it per run keeps that
        judgement with the caller that knows the mode (see chat_service) rather
        than freezing one number for every mode at startup.
        """
        limit = max_iterations or self._max_iterations
        tools = self._registry.for_mode(mode)
        schemas = [t.to_ollama_schema() for t in tools] or None
        messages = _with_capability_note(messages, mode, tools)

        # TOOLS ARE DROPPED WHEN THE TURN CARRIES AN IMAGE.
        #
        # Vision and tool-calling are handled by different branches of a model's
        # chat template, and on small local models the combination is routinely
        # mishandled -- the template renders the tool block and drops the image
        # part, so the model reports it received no image. That is exactly the
        # symptom here: the bytes are verifiably base64 and on the last user
        # message, Ollama reports `vision` for the model, and it still answers
        # "I cannot see an image".
        #
        # Looking at a picture is not a task that needs tools, so giving them up
        # for that one turn costs nothing real. If the model wants a tool it can
        # ask in the next turn, which carries no image and gets the full set.
        has_images = any(m.get("images") for m in messages)
        if has_images and schemas:
            log.info("dropping %d tool schemas for this turn: it carries an image", len(schemas))
            schemas = None

        final_text_parts: list[str] = []

        for _iteration in range(limit):
            tokens: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            async for event in self._llm.chat_stream(model, messages, tools=schemas):
                if event["type"] == "token" and event["content"]:
                    tokens.append(event["content"])
                    await emit(events.TOKEN, {"content": event["content"]})
                elif event["type"] == "tool_calls":
                    tool_calls = event["calls"]

            text = "".join(tokens)
            if text:
                final_text_parts.append(text)

            if not tool_calls and tools:
                # Second chance: did the model write the call as text?
                recovered = recover_text_tool_call(text)
                if recovered and self._registry.get_granted(recovered["name"], mode):
                    await emit(events.STATUS, {
                        "text": f"Understood — running {recovered['name']} (the model wrote it as text).",
                    })
                    tool_calls = [recovered]

            if not tool_calls:
                return "".join(final_text_parts)

            # Record the assistant turn that requested the calls (required by
            # the chat template so the model sees its own call in history).
            messages.append({
                "role": "assistant",
                "content": text,
                "tool_calls": [
                    {"function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in tool_calls
                ],
            })

            for call in tool_calls:
                result_msg = await self._execute_one(call, mode, ctx, emit)
                messages.append(result_msg)

        # Hitting the cap in Code mode is not the same event as hitting it
        # elsewhere. Everywhere else the turn just ends; here it can end with a
        # PARTIALLY written changeset, which is indistinguishable from a
        # finished one in the review panel. Saying so is the difference between
        # the user reading the diff and the user trusting it.
        note = (" Some edits may be half-finished — read the diff before applying, "
                "or ask Arthur to carry on." if mode is TaskMode.CODE else "")
        await emit(events.STATUS, {
            "text": f"Stopped: reached the tool-use limit for one message.{note}",
        })
        await emit(events.TOOL_LIMIT, {"mode": mode.value})
        return "".join(final_text_parts)

    async def _execute_one(
        self, call: dict[str, Any], mode: TaskMode, ctx: ToolContext, emit: Emit
    ) -> dict[str, Any]:
        name, raw_args = call["name"], call.get("arguments") or {}

        def tool_msg(content: str) -> dict[str, Any]:
            return {"role": "tool", "tool_name": name, "content": content}

        tool = self._registry.get_granted(name, mode)
        if tool is None:
            await emit(events.TOOL_RESULT, {"name": name, "ok": False, "summary": "not available in this mode", "flagged": False})
            return tool_msg(f"Error: tool '{name}' is not available in {mode.value} mode.")

        try:
            args = tool.Args.model_validate(raw_args)
        except ValidationError as e:
            await emit(events.TOOL_RESULT, {"name": name, "ok": False, "summary": "invalid arguments", "flagged": False})
            return tool_msg(f"Error: invalid arguments for '{name}': {e.errors(include_url=False)}. Retry with corrected arguments.")

        if tool.risk is Risk.CONFIRM:
            approval = self._approvals.create(
                tool=name,
                summary=tool.approval_summary(args),
                args_preview=_preview(args.model_dump()),
                # Structured (unstringified) args, distinct from args_preview:
                # the dialog can seed an editable form from real values (a list
                # stays a list) instead of the comma-joined display string.
                args=args.model_dump(mode="json"),
            )
            await emit(events.APPROVAL_REQUIRED, {
                "id": approval.id, "tool": name, "summary": approval.summary,
                "args_preview": approval.args_preview, "args": approval.args,
            })
            resolution = await self._approvals.wait(approval.id)
            await emit(events.APPROVAL_RESOLVED, {"id": approval.id, "approved": resolution.approved})
            if not resolution.approved:
                return tool_msg(
                    f"The user declined to allow '{name}'. Do not retry it; "
                    "acknowledge and continue without this action."
                )
            if resolution.edited_args is not None:
                # The user rewrote the draft before sending it — e.g. reworded
                # an email or fixed a typo'd address. Re-run it through the
                # SAME validation gate the model's own args went through
                # (never a trusted bypass just because a human typed it).
                try:
                    args = tool.Args.model_validate(resolution.edited_args)
                except ValidationError as e:
                    await emit(events.TOOL_RESULT, {
                        "name": name, "ok": False, "summary": "your edit was invalid", "flagged": False,
                    })
                    return tool_msg(
                        f"The user edited the arguments for '{name}' before approving, but the edit "
                        f"was invalid: {e.errors(include_url=False)}. The action was NOT taken."
                    )

        await emit(events.TOOL_START, {"name": name, "summary": tool.approval_summary(args)})
        try:
            result = await tool.execute(args, ctx)
        except Exception as e:  # a tool bug must not kill the chat stream
            log.exception("tool %s failed", name)
            await emit(events.TOOL_RESULT, {"name": name, "ok": False, "summary": str(e)[:200], "flagged": False})
            return tool_msg(f"Error: '{name}' failed: {e}")

        content, flagged = result.content, False
        if result.external:
            # Untrusted bytes (web pages, emails) get scanned + spotlighted
            # before they may enter model context. Uniform for every tool.
            content, flagged = await self._gateway.scan_tool_output(
                result.source or name, content
            )

        await emit(events.TOOL_RESULT, {
            "name": name, "ok": result.ok,
            "summary": result.summary or ("done" if result.ok else "failed"),
            "detail": result.detail,
            "flagged": flagged,
        })
        msg = tool_msg(content)
        if result.images_b64:
            msg["images"] = result.images_b64  # multimodal models see screenshots
        return msg


def _preview(args: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    """Human-readable arg values for the approval dialog. Lists join with
    commas — str(['a@x.com']) would leak Python syntax ("['a@x.com']") into a
    dialog that non-technical users must be able to read at a glance."""
    out = {}
    for k, v in args.items():
        if isinstance(v, (list, tuple)):
            s = ", ".join(str(item) for item in v)
        else:
            s = str(v)
        out[k] = s if len(s) <= limit else s[:limit] + "…"
    return out
