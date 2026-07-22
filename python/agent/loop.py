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
        start = text.find('{"', idx)
        if start == -1:
            # also tolerate a space after the brace
            start = text.find('{ "', idx)
            if start == -1:
                return None
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
    ) -> str:
        """Streams via `emit`, returns the final assistant text."""
        tools = self._registry.for_mode(mode)
        schemas = [t.to_ollama_schema() for t in tools] or None
        final_text_parts: list[str] = []

        for iteration in range(self._max_iterations):
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

        await emit(events.STATUS, {"text": "Stopped: reached the tool-use limit for one message."})
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
            )
            await emit(events.APPROVAL_REQUIRED, {
                "id": approval.id, "tool": name,
                "summary": approval.summary, "args_preview": approval.args_preview,
            })
            approved = await self._approvals.wait(approval.id)
            await emit(events.APPROVAL_RESOLVED, {"id": approval.id, "approved": approved})
            if not approved:
                return tool_msg(
                    f"The user declined to allow '{name}'. Do not retry it; "
                    "acknowledge and continue without this action."
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
