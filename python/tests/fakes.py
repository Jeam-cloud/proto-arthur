"""Test doubles.

WHY fakes instead of unittest.mock everywhere: fakes implement the same
interface as the real thing with predictable behavior, so tests read like
scenarios ("the model calls echo, then answers") instead of mock plumbing.
Every fake here matches a Protocol/duck-type used by production code — if a
real interface changes shape, these break loudly at test time.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from security.scanners import ScanResult
from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult


class FakeLLM:
    """Scripted model. Each turn: {"tokens": [...], "tool_calls": [...]}.
    When the script runs out it politely emits nothing — which also covers the
    background title/extraction calls chat_service makes after DONE."""

    def __init__(self, turns: list[dict[str, Any]] | None = None):
        self.turns = list(turns or [])
        self.calls: list[dict] = []  # every request, for assertions
        self.json_turns: list[Any] = []  # scripted chat_json replies
        # What this fake claims it can do. Default is EMPTY, matching a real
        # Ollama that could not answer -- and callers must treat unknown as
        # "capable", so the default exercises the permissive path.
        self.caps: set[str] = set()

    async def chat_stream(self, model, messages, tools=None) -> AsyncIterator[dict]:
        self.calls.append({"model": model, "messages": list(messages), "tools": tools})
        turn = self.turns.pop(0) if self.turns else {"tokens": []}
        for t in turn.get("tokens", []):
            yield {"type": "token", "content": t}
        if turn.get("tool_calls"):
            yield {"type": "tool_calls", "calls": turn["tool_calls"]}
        yield {"type": "done", "eval_count": 1}

    async def chat_json(self, model, messages, schema, **kwargs):
        """Scripted constrained-decoding replies.

        Pops from `json_turns`. Returning None when the script runs dry mirrors
        the real client's failure mode (a model that could not produce the
        shape), which every caller must already survive.
        """
        self.calls.append({"model": model, "messages": list(messages), "schema": schema})
        return self.json_turns.pop(0) if self.json_turns else None

    async def is_up(self) -> bool:
        return True

    async def capabilities(self, model: str) -> set[str]:
        return set(self.caps)

    async def parameter_size_b(self, model: str) -> float | None:
        return None

    async def list_models(self) -> list[dict]:
        return [{"name": "fake-model", "size_bytes": 1, "family": "fake", "parameter_size": "1B"}]

    async def embed(self, texts: list[str], model: str = "") -> list[list[float]]:
        return [FakeEmbedder.vector_for(t) for t in texts]


class FakeEmbedder:
    """Deterministic 16-dim vectors from a text hash; `alias()` forces two
    texts onto the SAME vector to exercise the dedupe path on demand."""

    def __init__(self):
        self._aliases: dict[str, str] = {}
        self.fail = False  # flip to simulate Ollama being down

    def alias(self, text_a: str, text_b: str) -> None:
        self._aliases[text_b] = text_a

    @staticmethod
    def vector_for(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in digest[:16]]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise ConnectionError("embedder down")
        return [self.vector_for(self._aliases.get(t, t)) for t in texts]


class FakeScanner:
    """Flags anything containing the magic word INJECTION."""

    name = "fake"

    def scan(self, text: str) -> ScanResult:
        bad = "INJECTION" in text
        return ScanResult(risk=0.9 if bad else 0.0, flagged=bad,
                          reasons=["magic word"] if bad else [], backend=self.name)


class EchoArgs(BaseModel):
    text: str = Field(max_length=100)


class EchoTool(Tool):
    name = "echo"
    description = "Echo text back."
    Args = EchoArgs
    risk = Risk.SAFE
    modes = {TaskMode.GENERAL, TaskMode.RESEARCH}

    def __init__(self):
        self.executions: list[str] = []

    async def execute(self, args: EchoArgs, ctx: ToolContext) -> ToolResult:
        self.executions.append(args.text)
        return ToolResult(ok=True, content=f"echo: {args.text}", summary="echoed")


class ConfirmEchoTool(EchoTool):
    name = "confirm_echo"
    risk = Risk.CONFIRM

    def __init__(self):
        super().__init__()
        self.modes = {TaskMode.GENERAL}


class ExternalTool(EchoTool):
    """Returns untrusted content — exercises the gateway spotlight path."""

    name = "external_fetch"

    async def execute(self, args: EchoArgs, ctx: ToolContext) -> ToolResult:
        self.executions.append(args.text)
        return ToolResult(ok=True, content=args.text, external=True, source="test_source")


class CrashTool(EchoTool):
    name = "crash"

    async def execute(self, args: EchoArgs, ctx: ToolContext) -> ToolResult:
        raise RuntimeError("boom")


class WriteFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace folder")
    content: str = Field(max_length=400_000)


class WriteFileTool(Tool):
    """Stand-in for tools.coding.WriteFileTool: same name and Args shape (path
    + whole-file content), no real disk/changeset I/O — used to test that the
    agent loop can force this specific call after a model only PRINTS a file."""

    name = "write_file"
    description = "Write a file."
    Args = WriteFileArgs
    risk = Risk.SAFE
    modes = {TaskMode.CODE}

    def __init__(self):
        self.writes: list[tuple[str, str]] = []

    async def execute(self, args: WriteFileArgs, ctx: ToolContext) -> ToolResult:
        self.writes.append((args.path, args.content))
        return ToolResult(ok=True, content=f"staged {args.path}", summary=f"wrote {args.path}")


class CollectingEmit:
    """Callable emit() that records every SSE event for assertions."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, event: str, data: dict) -> None:
        self.events.append((event, data))

    def of(self, name: str) -> list[dict]:
        return [d for e, d in self.events if e == name]
