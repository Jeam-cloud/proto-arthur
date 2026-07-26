"""Thin wrapper around the official `ollama` client.

WHY wrap it at all:
1. Error taxonomy — connection failures become OllamaUnavailableError and
   missing models become ModelNotFoundError, so routes and UI react precisely.
2. Normalized stream events — the agent loop consumes simple dicts
   ({"type": "token"|"tool_calls"|"done"}) and never touches ollama's types.
   That makes the loop testable with a fake client (see tests/fakes.py).
3. Tool-support fallback — many small models reject the `tools` parameter.
   We retry once without tools instead of surfacing a crash mid-chat.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import ollama

from core.errors import ModelNotFoundError, OllamaUnavailableError

log = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, host: str, keep_alive: str = "10m"):
        self._client = ollama.AsyncClient(host=host)
        self._keep_alive = keep_alive

    async def is_up(self) -> bool:
        try:
            await self._client.list()
            return True
        except Exception:
            return False

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            res = await self._client.list()
        except (httpx.ConnectError, ConnectionError) as e:
            raise OllamaUnavailableError() from e
        return [
            {
                "name": m.model,
                "size_bytes": m.size or 0,
                "family": (m.details.family if m.details else None),
                "parameter_size": (m.details.parameter_size if m.details else None),
            }
            for m in res.models
        ]

    async def delete(self, model: str) -> None:
        """Uninstall a model, freeing its disk space. Ollama 404s if the name
        isn't actually installed, which we surface as ModelNotFoundError so
        the route can turn it into a clean 404 instead of a 500."""
        try:
            await self._client.delete(model)
        except (httpx.ConnectError, ConnectionError) as e:
            raise OllamaUnavailableError() from e
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise ModelNotFoundError(f"'{model}' isn't installed") from e
            raise

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """Yields {status, completed, total} progress dicts for the download UI."""
        try:
            async for chunk in await self._client.pull(model, stream=True):
                yield {
                    "status": chunk.status or "",
                    "completed": chunk.completed or 0,
                    "total": chunk.total or 0,
                }
        except (httpx.ConnectError, ConnectionError) as e:
            raise OllamaUnavailableError() from e
        except ollama.ResponseError as e:
            raise ModelNotFoundError(f"Could not pull '{model}': {e.error}") from e

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        try:
            res = await self._client.embed(model=model, input=texts)
            return [list(v) for v in res.embeddings]
        except (httpx.ConnectError, ConnectionError) as e:
            raise OllamaUnavailableError() from e
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise ModelNotFoundError(f"Embedding model '{model}' is not installed") from e
            raise

    async def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> Any:
        """Generate output that is GUARANTEED to match `schema`.

        This is not "ask nicely for JSON and hope". Ollama compiles the schema
        into a grammar and, at every token, zeroes the probability of any token
        that could not legally come next. Invalid JSON is unreachable, not just
        discouraged -- so there is no parse-retry loop and no salvage code path
        (contrast agent/loop.py, which has to clean up after free-text tool
        calls precisely because nothing constrained them).

        WHY this matters for research: every step of an investigation hands its
        output to Python, not to a human. A 3B model that writes lovely prose
        but occasionally emits a trailing comma would break the whole pipeline.
        Constrained decoding is what makes small local models usable as
        machinery instead of just as writers.

        temperature=0 by default: these calls are structure extraction, and we
        want the same input to produce the same plan twice.
        """
        try:
            res = await self._client.chat(
                model=model,
                messages=messages,
                format=schema,
                stream=False,
                keep_alive=self._keep_alive,
                options={"temperature": temperature},
            )
        except (httpx.ConnectError, ConnectionError) as e:
            raise OllamaUnavailableError() from e
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise ModelNotFoundError(f"Model '{model}' is not installed") from e
            raise

        import json

        content = (res.message.content or "").strip()
        # The grammar guarantees well-formed JSON, but an empty generation (model
        # hit its context limit) still has to be handled.
        return json.loads(content) if content else None

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Normalized stream:
        {"type":"token","content":str}
        {"type":"tool_calls","calls":[{"name","arguments"}]}
        {"type":"done","eval_count":int}
        """
        try:
            async for event in self._stream_once(model, messages, tools):
                yield event
        except ollama.ResponseError as e:
            if tools and "does not support tools" in str(e.error).lower():
                # Graceful degradation: answer without tools rather than erroring.
                log.warning("model %s lacks tool support; retrying without tools", model)
                yield {"type": "token", "content": ""}
                async for event in self._stream_once(model, messages, None):
                    yield event
            elif e.status_code == 404:
                raise ModelNotFoundError(f"Model '{model}' is not installed") from e
            else:
                raise
        except (httpx.ConnectError, ConnectionError) as e:
            raise OllamaUnavailableError() from e

    async def _stream_once(self, model, messages, tools) -> AsyncIterator[dict[str, Any]]:
        stream = await self._client.chat(
            model=model,
            messages=messages,
            tools=tools or None,
            stream=True,
            keep_alive=self._keep_alive,
        )
        pending_calls: list[dict[str, Any]] = []
        eval_count = 0
        async for chunk in stream:
            msg = chunk.message
            if msg and msg.content:
                yield {"type": "token", "content": msg.content}
            if msg and msg.tool_calls:
                for tc in msg.tool_calls:
                    pending_calls.append(
                        {"name": tc.function.name, "arguments": dict(tc.function.arguments or {})}
                    )
            if chunk.done:
                eval_count = chunk.eval_count or 0
        if pending_calls:
            yield {"type": "tool_calls", "calls": pending_calls}
        yield {"type": "done", "eval_count": eval_count}
