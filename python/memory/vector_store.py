"""Vector index behind a small interface.

Production: ChromaDB (embedded, persistent). Fallback + tests: a pure-Python
in-memory index rebuilt from the SQLite `memories.embedding` column.

TWO deliberate choices worth knowing:

1. SQLite stays the source of truth; Chroma is a derived index. Vector DBs
   corrupt / change format across versions far more often than SQLite. If the
   Chroma dir is ever damaged, Arthur rebuilds it from SQLite on boot and the
   user loses nothing. This "canonical store + derived index" split is the
   standard pattern for durable RAG systems.

2. `anonymized_telemetry=False` — Chroma phones home to PostHog BY DEFAULT.
   In a privacy-first product that default is a broken promise; turning it
   off in code (not docs) is non-negotiable.

WHY we pass embeddings explicitly instead of Chroma's embedding_function:
Chroma's default silently downloads an ONNX MiniLM model (~80MB) on first
use. We already run nomic-embed-text via Ollama — one embedding model, one
download, consistent vectors everywhere.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class VectorStore(Protocol):
    def add(self, id: str, embedding: list[float], text: str) -> None: ...
    def update(self, id: str, embedding: list[float], text: str) -> None: ...
    def delete(self, id: str) -> None: ...
    def query(self, embedding: list[float], k: int) -> list[tuple[str, float]]:
        """Returns [(id, similarity 0..1)] best-first."""
        ...
    def count(self) -> int: ...


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class InMemoryVectorStore:
    """Exact cosine search. Fine into the thousands of memories a person
    accumulates; also the test double."""

    def __init__(self):
        self._items: dict[str, tuple[list[float], str]] = {}

    def add(self, id: str, embedding: list[float], text: str) -> None:
        self._items[id] = (embedding, text)

    def update(self, id: str, embedding: list[float], text: str) -> None:
        self._items[id] = (embedding, text)

    def delete(self, id: str) -> None:
        self._items.pop(id, None)

    def query(self, embedding: list[float], k: int) -> list[tuple[str, float]]:
        scored = [(id, cosine(embedding, emb)) for id, (emb, _) in self._items.items()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def count(self) -> int:
        return len(self._items)


class ChromaVectorStore:
    def __init__(self, path: Path):
        import chromadb

        self._client = chromadb.PersistentClient(
            path=str(path),
            settings=chromadb.Settings(anonymized_telemetry=False, allow_reset=False),
        )
        self._col = self._client.get_or_create_collection(
            "memories", metadata={"hnsw:space": "cosine"}
        )

    def add(self, id: str, embedding: list[float], text: str) -> None:
        self._col.add(ids=[id], embeddings=[embedding], documents=[text])

    def update(self, id: str, embedding: list[float], text: str) -> None:
        self._col.upsert(ids=[id], embeddings=[embedding], documents=[text])

    def delete(self, id: str) -> None:
        self._col.delete(ids=[id])

    def query(self, embedding: list[float], k: int) -> list[tuple[str, float]]:
        if self._col.count() == 0:
            return []
        res = self._col.query(query_embeddings=[embedding], n_results=min(k, self._col.count()))
        ids = res["ids"][0]
        # Chroma returns cosine DISTANCE (0 = identical); convert to similarity.
        sims = [1.0 - d for d in res["distances"][0]]
        return list(zip(ids, sims))

    def count(self) -> int:
        return self._col.count()


def build_vector_store(path: Path) -> VectorStore:
    try:
        return ChromaVectorStore(path)
    except Exception as e:
        log.warning("ChromaDB unavailable (%s); using in-memory index rebuilt from SQLite", e)
        return InMemoryVectorStore()
