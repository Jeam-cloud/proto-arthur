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
