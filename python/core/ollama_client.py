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

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import ollama

from core.errors import EmptyGenerationError, ModelNotFoundError, OllamaUnavailableError

log = logging.getLogger(__name__)

# THE SINGLE MOST CONSEQUENTIAL NUMBER IN THIS FILE.
#
# Ollama's default context window is 2048 tokens. Not the model's context
# window -- llama3.1 advertises 128K and qwen2.5-coder 32K -- but the window
# Ollama actually allocates when the caller does not say otherwise. Anything
# past it is silently dropped, and a chat request whose prompt overflows comes
# back with an EMPTY generation rather than an error.
#
# That is precisely how Research mode failed: a section prompt carrying a dozen
# source passages measures around 2900 tokens, overflowed 2048 every single
# time, and returned nothing. Every section fell back to "Arthur could not
# write this section", which read as the model being too weak when the model
# had never actually been asked. There is a comment further down this file that
# says "an empty generation (model hit its context limit) still has to be
# handled" -- the handling was right, the cause was fixable and nobody had.
#
# WHY one value for the whole app instead of sizing per request: Ollama keeps a
# model resident with the options it was loaded under, so varying num_ctx
# between calls evicts and reloads the model -- seconds of stall on every
# alternation. One value means one load.
#
# WHY 8192 and not larger: the KV cache is allocated up front and scales
# linearly with this number. At 8K an 8B model costs roughly a gigabyte of
# extra memory, which the 8GB tier can absorb; at 32K it cannot. 8K comfortably
# fits every prompt this app constructs, with the research synthesis call --
# the largest by far -- using about a third of it.
DEFAULT_NUM_CTX = 8192


class OllamaClient:
    def __init__(self, host: str, keep_alive: str = "10m", num_ctx: int = DEFAULT_NUM_CTX):
        self._client = ollama.AsyncClient(host=host)
        self._keep_alive = keep_alive
        self._num_ctx = max(2048, int(num_ctx or DEFAULT_NUM_CTX))
        self._size_cache: dict[str, float | None] = {}
        self._caps_cache: dict[str, set[str]] = {}

    def _options(self, **extra: Any) -> dict[str, Any]:
        """Options every generation call shares. num_ctx belongs here rather
        than at each call site precisely because it must not vary between
        them -- see DEFAULT_NUM_CTX."""
        return {"num_ctx": self._num_ctx, **extra}

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

    async def capabilities(self, model: str) -> set[str]:
        """What this model can actually do, per Ollama: e.g. {"completion",
        "tools", "vision", "thinking"}.

        WHY ask instead of infer from the name: the app needs to warn a user
        that the model they picked cannot see an image they just attached, and
        getting that wrong is worse than not warning. Guessing from names is
        exactly how `gemma4:latest` got misclassified as a large model -- the
        name is a marketing string, the capability list is a fact.

        Returns an empty set when Ollama cannot say. Callers must treat that as
        "unknown", NOT as "cannot" -- warning about a limitation that may not
        exist trains people to ignore warnings.
        """
        if model in self._caps_cache:
            return self._caps_cache[model]
        caps: set[str] = set()
        try:
            res = await self._client.show(model)
            raw = getattr(res, "capabilities", None) or []
            caps = {str(c).lower() for c in raw}
        except Exception as e:
            log.info("could not read capabilities for %s: %s", model, e)
        self._caps_cache[model] = caps
        return caps

    async def parameter_size_b(self, model: str) -> float | None:
        """How many billion parameters this model actually has, per Ollama.

        Exists because the model NAME is not a reliable size signal. `:latest`
        carries no size at all, and Gemma 4 writes its edge sizes as `e2b`/`e4b`
        for "effective" parameters -- so name-parsing classified a 2.3B edge
        model as a large one and handed it the hardest code path in the app.
        Ollama already knows the real number; asking is better than guessing.

        Cached: the answer cannot change while a model is installed, and this
        is called once per section of a research paper.

        Returns None when Ollama cannot say, which callers must treat as
        "unknown" and fall back to the name -- never as "small".
        """
        if model in self._size_cache:
            return self._size_cache[model]
        size: float | None = None
        try:
            res = await self._client.show(model)
            raw = ((getattr(res, "details", None) and res.details.parameter_size) or "").strip()
            # Ollama reports strings like "8.0B", "4.5B", "596.99M".
            m = re.fullmatch(r"([\d.]+)\s*([BM])", raw, re.I)
            if m:
                size = float(m.group(1))
                if m.group(2).upper() == "M":
                    size /= 1000.0
        except Exception as e:
            log.info("could not read parameter size for %s: %s", model, e)
        self._size_cache[model] = size
        return size

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
        timeout_s: float = 150.0,
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

        timeout_s bounds the call. WHY this exists at all: a research run makes
        several of these back to back (plan, per-lane requery, conflict check,
        synthesis) with NO other progress indicator in between -- a model that
        stalls (a busy GPU, a schema the runtime chokes on) previously hung the
        whole investigation forever with the UI stuck at "100%, Stop still
        showing" and no way out. Every caller in research/engine.py already
        catches generic Exception and falls back gracefully, so timing out
        here turns a silent hang into a handled failure, not a crash.
        """
        try:
            res = await asyncio.wait_for(
                self._client.chat(
                    model=model,
                    messages=messages,
                    format=schema,
                    stream=False,
                    keep_alive=self._keep_alive,
                    options=self._options(temperature=temperature),
                ),
                timeout=timeout_s,
            )
        except (httpx.ConnectError, ConnectionError) as e:
            raise OllamaUnavailableError() from e
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise ModelNotFoundError(f"Model '{model}' is not installed") from e
            raise
        # `asyncio.TimeoutError`, NOT the builtin `TimeoutError`.
        #
        # They are the same class from Python 3.11 onward, and DIFFERENT
        # classes on 3.10 and earlier, where asyncio.TimeoutError aliases
        # concurrent.futures.TimeoutError instead. Catching the builtin alone
        # therefore worked on the developer's machine and let the timeout
        # escape unhandled on 3.10 -- so a stalled model surfaced as a raw
        # asyncio.TimeoutError instead of the OllamaUnavailableError every
        # caller in research/engine.py is written to expect. Naming the asyncio
        # one covers both, because on 3.11+ it IS the builtin.
        except asyncio.TimeoutError as e:
            log.warning("chat_json timed out after %.0fs for model %s", timeout_s, model)
            raise OllamaUnavailableError(f"'{model}' did not respond within {timeout_s:.0f}s") from e

        import json

        content = (res.message.content or "").strip()

        # WHY the prompt size is inspected on every call.
        #
        # An empty generation has two very different causes that are impossible
        # to tell apart from the outside: the prompt overflowed the context
        # window, or the model is simply not capable of satisfying the schema.
        # The first is a configuration bug and takes one setting to fix; the
        # second means the user needs a bigger model. Reporting both as "the
        # model returned nothing usable" made the fixable one look like the
        # hopeless one for weeks.
        #
        # Ollama reports how many tokens the prompt actually consumed. Compare
        # it to the window we asked for and the answer stops being a guess.
        # Carried on the EXCEPTION, not on the client instance. Research runs
        # two lanes concurrently, so `self.last_whatever` would be overwritten
        # by whichever call finished second and the diagnosis would be attached
        # to the wrong failure.
        prompt_tokens = int(getattr(res, "prompt_eval_count", 0) or 0)
        # 0.9 rather than 1.0: llama.cpp reserves part of the window for the
        # reply, so a prompt sitting at 90% has already lost tokens off the
        # front -- it does not have to reach the ceiling to have been truncated.
        context_full = bool(prompt_tokens) and prompt_tokens >= self._num_ctx * 0.9
        if context_full:
            log.warning(
                "prompt used %d tokens of a %d window for %s -- it was truncated",
                prompt_tokens, self._num_ctx, model,
            )

        # The grammar guarantees well-formed JSON, but an empty generation still
        # has to be handled. It now RAISES rather than returning None so the
        # reason travels with it; every caller already sits inside a
        # `try/except Exception` and falls back exactly as before.
        if not content:
            raise EmptyGenerationError(model, prompt_tokens, self._num_ctx, context_full)
        return json.loads(content)

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
            # Same window as the structured path. Chat overflows the 2048
            # default more quietly than research did -- rather than returning
            # nothing, a long conversation silently loses its oldest turns and
            # the model starts contradicting things it said ten messages ago.
            # That is the "it forgets what we were talking about" complaint,
            # and it is a configuration bug, not a model limitation.
            options=self._options(),
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
