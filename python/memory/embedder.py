"""Embedding provider abstraction.

WHY a Protocol: memory logic is identical whether vectors come from Ollama's
nomic-embed-text or a deterministic fake in tests. Depending on the interface
(not on Ollama) is what makes `pytest` run in milliseconds with no model.
"""

from __future__ import annotations

from typing import Protocol

from core.ollama_client import OllamaClient


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, client: OllamaClient, model: str):
        self._client = client
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._client.embed(texts, model=self._model)
