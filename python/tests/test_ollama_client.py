"""chat_json's timeout: a research run makes several of these calls back to
back with no other progress indicator in between (see research/engine.py). A
client that stalls used to hang the whole investigation forever; now it must
surface as OllamaUnavailableError within the requested window instead."""

from __future__ import annotations

import asyncio

import pytest

from core.errors import OllamaUnavailableError
from core.ollama_client import OllamaClient


class _StallingChat:
    """Stands in for ollama.AsyncClient: .chat() never resolves."""

    async def chat(self, **kwargs):
        await asyncio.sleep(3600)  # effectively forever, relative to the test


async def test_chat_json_times_out_instead_of_hanging_forever():
    client = OllamaClient(host="http://fake")
    client._client = _StallingChat()

    with pytest.raises(OllamaUnavailableError):
        await client.chat_json("m", [{"role": "user", "content": "hi"}], {"type": "object"}, timeout_s=0.05)


class _RecordingChat:
    """Captures the kwargs every call was made with."""

    def __init__(self, result=None):
        self.calls = []
        self._result = result

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


class TestContextWindow:
    """Ollama's default context window is 2048 tokens, and it is applied when
    the caller says nothing. A prompt past it is not rejected -- it comes back
    as an EMPTY generation.

    That silently broke Research mode completely: a section prompt carrying a
    dozen source passages runs to roughly 2900 tokens, so every section
    returned nothing and fell back to "Arthur could not write this section".
    The model looked incapable when it had never been asked. These tests exist
    because the failure is invisible -- nothing errors, output just stops
    appearing -- so a regression here would be found by a user, not by CI.
    """

    async def test_structured_calls_set_a_context_window(self):
        client = OllamaClient(host="http://fake")
        client._client = _RecordingChat(_Res('{"ok": true}'))
        await client.chat_json("m", [{"role": "user", "content": "hi"}], {"type": "object"})

        options = client._client.calls[0]["options"]
        assert options["num_ctx"] > 2048, "left at Ollama's default, prompts will silently truncate"

    async def test_streaming_calls_set_it_too(self):
        # Chat overflows more quietly than research: instead of returning
        # nothing, a long conversation loses its oldest turns and the model
        # starts contradicting itself.
        client = OllamaClient(host="http://fake")
        client._client = _RecordingChat(_EmptyStream())
        async for _ in client.chat_stream("m", [{"role": "user", "content": "hi"}]):
            pass

        assert client._client.calls[0]["options"]["num_ctx"] > 2048

    async def test_the_window_fits_a_real_research_prompt(self):
        # Measured against the actual thing that broke, not a round number:
        # 14 sources at 600 characters of passage each, plus the system prompt.
        from core.ollama_client import DEFAULT_NUM_CTX
        approx_tokens = (14 * 720 + 1800) // 4
        assert DEFAULT_NUM_CTX > approx_tokens * 1.5, "no headroom for the response"

    async def test_a_configured_floor_is_enforced(self):
        # A misconfiguration that sets this below Ollama's own default would
        # reintroduce the bug while looking deliberate.
        assert OllamaClient(host="http://fake", num_ctx=512)._num_ctx >= 2048
        assert OllamaClient(host="http://fake", num_ctx=0)._num_ctx >= 2048


class _EmptyStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _Msg:
    def __init__(self, content):
        self.content = content


class _Res:
    def __init__(self, content):
        self.message = _Msg(content)


class _EchoChat:
    """A normal, fast client -- confirms the timeout wrapper doesn't get in
    the way of a call that actually succeeds."""

    async def chat(self, **kwargs):
        return _Res('{"ok": true}')


async def test_chat_json_returns_normally_when_the_model_is_fast():
    client = OllamaClient(host="http://fake")
    client._client = _EchoChat()

    result = await client.chat_json("m", [{"role": "user", "content": "hi"}], {"type": "object"}, timeout_s=5.0)
    assert result == {"ok": True}
