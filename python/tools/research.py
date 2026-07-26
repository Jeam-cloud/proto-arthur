"""Deep research: Tavily search -> sandboxed fetch -> chunk -> embed ->
retrieve -> hand the best chunks to the model with citation labels.

Trust boundaries (the part that matters):
  * Tavily API call happens IN the backend — the key comes from the OS vault
    and NEVER enters the container.
  * Page fetching happens IN the container — the part that touches arbitrary
    servers runs with no credentials, no filesystem, capped resources.
  * Every extracted page passes through the security gateway (scan +
    spotlight) via `external=True` before the model sees a byte of it.

WHY chunk-then-retrieve instead of pasting whole pages: five pages ≈ 100k+
characters — beyond a small model's context and mostly irrelevant. Embedding
chunks and keeping only the ones semantically closest to the question is the
RAG pattern from the tracker (fetch → chunk → embed → retrieve → summarise).
"""

from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel, Field

from memory.embedder import Embedder
from memory.vector_store import cosine
from security.vault import SecretsVault
from sandbox.runner import SandboxRunner
from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult

# Tag bumped to :2 when PDF extraction (pypdf) was added to the image.
# ensure_image only checks whether the TAG exists, so changing the Dockerfile
# without changing the tag would silently keep serving the old image.
RESEARCH_IMAGE = "arthur-research:2"


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    """Overlapping character windows. Overlap keeps sentences that straddle a
    boundary retrievable from both sides — cheap insurance against cutting the
    one relevant sentence in half."""
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks


class WebResearchArgs(BaseModel):
    query: str = Field(min_length=3, max_length=400, description="The research question")


class WebResearchTool(Tool):
    name = "web_research"
    description = (
        "Search the web and read the most relevant pages to answer a question. "
        "Returns extracted source passages with [n] citation labels. Use for anything "
        "needing current or external information."
    )
    Args = WebResearchArgs
    risk = Risk.SAFE  # read-only; the sandbox and gateway carry the risk
    modes = {TaskMode.RESEARCH}

    def __init__(self, vault: SecretsVault, sandbox: SandboxRunner, embedder: Embedder,
                 allow_unsandboxed: bool = False):
        self._vault = vault
        self._sandbox = sandbox
        self._embedder = embedder
        self._allow_unsandboxed = allow_unsandboxed

    def approval_summary(self, args: WebResearchArgs) -> str:
        return f'Research the web for: "{args.query}"'

    async def execute(self, args: WebResearchArgs, ctx: ToolContext) -> ToolResult:
        api_key = self._vault.get("tavily")
        if not api_key:
            return ToolResult(ok=False, content="Research is not configured: add a Tavily API key in Settings → Integrations.", summary="Tavily key missing")

        # 1. search (backend-side, key never leaves this process)
        try:
            from tavily import TavilyClient

            search = await asyncio.to_thread(
                lambda: TavilyClient(api_key=api_key).search(args.query, max_results=5)
            )
        except Exception as e:
            return ToolResult(ok=False, content=f"Web search failed (offline or invalid key): {e}", summary="search failed")

        results = search.get("results", [])
        if not results:
            return ToolResult(ok=True, content="No search results found.", summary="0 results")

        # 2. fetch pages in the sandbox
        urls = [r["url"] for r in results]
        pages = await self._fetch(urls)
        if not pages:  # Docker off and unsandboxed fetch not allowed
            snippets = "\n\n".join(f"[{i+1}] {r['title']}\n{r.get('content','')}\nURL: {r['url']}" for i, r in enumerate(results))
            return ToolResult(
                ok=True, external=True, source="tavily_snippets",
                content="(Docker is off — full pages were not fetched; search snippets only.)\n\n" + snippets,
                summary=f"{len(results)} snippets (sandbox off)",
            )

        # 3. chunk + embed + retrieve
        labeled_chunks: list[tuple[int, str]] = []
        sources: list[dict] = []
        for i, page in enumerate(pages):
            if page.get("error") or not page.get("text"):
                continue
            n = len(sources) + 1
            sources.append({"n": n, "title": page["title"], "url": page["url"]})
            for c in chunk_text(page["text"])[:20]:
                labeled_chunks.append((n, c))

        if not labeled_chunks:
            return ToolResult(ok=True, content="Pages could not be read (blocked or empty).", summary="no readable pages")

        try:
            vectors = await self._embedder.embed([c for _, c in labeled_chunks])
            [qvec] = await self._embedder.embed([args.query])
            scored = sorted(
                zip(labeled_chunks, vectors),
                key=lambda pair: cosine(qvec, pair[1]),
                reverse=True,
            )[:8]
            picked = [lc for lc, _ in scored]
        except Exception:
            picked = labeled_chunks[:8]  # embeddings down -> first chunks beat nothing

        body = "\n\n".join(f"[{n}] {chunk}" for n, chunk in picked)
        source_list = "\n".join(f"[{s['n']}] {s['title']} — {s['url']}" for s in sources)
        return ToolResult(
            ok=True,
            external=True,  # gateway will scan + spotlight this
            source="web_research",
            content=(
                f"Source passages (cite as [n]):\n\n{body}\n\nSOURCES:\n{source_list}\n\n"
                "Answer the user's question from these passages and cite sources like [1]."
            ),
            summary=f"read {len(sources)} pages, kept {len(picked)} passages",
        )

    async def _fetch(self, urls: list[str]) -> list[dict]:
        payload = json.dumps({"urls": urls})
        try:
            await self._sandbox.ensure_image(RESEARCH_IMAGE, "research.Dockerfile")
            res = await self._sandbox.run(
                RESEARCH_IMAGE, [], stdin_data=payload,
                network="bridge",  # fetching is the job; still no creds inside
                timeout_s=60, mem_limit="768m",
            )
            return [json.loads(line) for line in res.stdout.splitlines() if line.strip()]
        except Exception:
            if not self._allow_unsandboxed:
                return []
            return await self._fetch_unsandboxed(urls)

    async def _fetch_unsandboxed(self, urls: list[str]) -> list[dict]:
        """Opt-in degraded path (Settings toggle). In-process fetch, extraction
        by trafilatura if installed. Flagged in SECURITY.md as a trade-off."""
        import httpx

        out = []
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            for url in urls[:5]:
                row = {"url": url, "title": url, "text": "", "error": None}
                try:
                    resp = await client.get(url)
                    try:
                        import trafilatura

                        row["text"] = (trafilatura.extract(resp.text) or "")[:40_000]
                    except ImportError:
                        row["text"] = resp.text[:20_000]
                except Exception as e:
                    row["error"] = str(e)[:200]
                out.append(row)
        return out


class QuickSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)


class QuickSearchTool(Tool):
    name = "quick_search"
    description = "Fast web search returning result titles and snippets only (no page reading). Use for simple current-fact lookups."
    Args = QuickSearchArgs
    risk = Risk.SAFE
    modes = {TaskMode.RESEARCH, TaskMode.FINANCE}

    def __init__(self, vault: SecretsVault):
        self._vault = vault

    def approval_summary(self, args: QuickSearchArgs) -> str:
        return f'Search the web for "{args.query}"'

    async def execute(self, args: QuickSearchArgs, ctx: ToolContext) -> ToolResult:
        api_key = self._vault.get("tavily")
        if not api_key:
            return ToolResult(ok=False, content="Search is not configured: add a Tavily API key in Settings → Integrations.", summary="Tavily key missing")
        try:
            from tavily import TavilyClient

            res = await asyncio.to_thread(
                lambda: TavilyClient(api_key=api_key).search(args.query, max_results=5)
            )
        except Exception as e:
            return ToolResult(ok=False, content=f"Search failed: {e}", summary="search failed")
        lines = [
            f"- {r['title']}: {r.get('content', '')[:300]} ({r['url']})"
            for r in res.get("results", [])
        ]
        return ToolResult(
            ok=True, external=True, source="tavily",
            content="Search results:\n" + "\n".join(lines) if lines else "No results.",
            summary=f"{len(lines)} results",
        )
