"""BYOK (bring-your-own-key): route one request to a hosted model, opt-in.

Raw httpx against each provider's HTTP API instead of their SDKs — two
streaming-parse functions we fully control versus two heavyweight SDK
dependencies in the installer.

SCOPE DECISION: BYOK requests get NO TOOLS. Cloud models are for "I want a
better-written answer", not "let a remote model drive my mouse" — keeping
tool execution local-only means the trust story stays one sentence long.
Memory recall still happens locally BEFORE the request, so the cloud sees
only the assembled prompt. Keys live in the OS vault and are read here,
per-request, never cached in module state, never logged (logging layer
redacts sk-... shapes as a second net).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from core import events
from core.errors import IntegrationNotConfiguredError
from security.vault import SecretsVault

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5",
}


class BYOKRouter:
    def __init__(self, vault: SecretsVault, model_overrides: dict[str, str] | None = None):
        self._vault = vault
        self._models = {**DEFAULT_MODELS, **(model_overrides or {})}

    async def stream_chat(self, provider: str, messages: list[dict], emit: Emit) -> str:
        key = self._vault.get(f"byok_{provider}")
        if not key:
            raise IntegrationNotConfiguredError(
                f"No API key stored for {provider}. Add one in Settings → Integrations."
            )
        # Tool messages never reach the cloud — belt to the "no tools" suspenders.
        clean = [m for m in messages if m.get("role") in ("system", "user", "assistant")]
        if provider == "openai":
            return await self._openai(key, clean, emit)
        if provider == "anthropic":
            return await self._anthropic(key, clean, emit)
        raise IntegrationNotConfiguredError(f"Unknown BYOK provider: {provider}")

    async def _openai(self, key: str, messages: list[dict], emit: Emit) -> str:
        parts: list[str] = []
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": self._models["openai"], "messages": messages, "stream": True},
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode()[:300]
                    raise IntegrationNotConfiguredError(f"OpenAI error {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    delta = (json.loads(line[6:])["choices"][0].get("delta") or {}).get("content")
                    if delta:
                        parts.append(delta)
                        await emit(events.TOKEN, {"content": delta})
        return "".join(parts)

    async def _anthropic(self, key: str, messages: list[dict], emit: Emit) -> str:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] in ("user", "assistant")]
        parts: list[str] = []
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={"model": self._models["anthropic"], "system": system,
                      "messages": turns, "max_tokens": 4096, "stream": True},
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode()[:300]
                    raise IntegrationNotConfiguredError(f"Anthropic error {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = json.loads(line[6:])
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta", {}).get("text", "")
                        if delta:
                            parts.append(delta)
                            await emit(events.TOKEN, {"content": delta})
        return "".join(parts)
