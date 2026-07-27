"""The investigation engine: plan -> search -> read -> gap-fill -> synthesise.

THE CENTRAL DESIGN DECISION -- Python drives, the model fills in blanks.

The obvious way to build "deep research" is to hand a model a search tool and
let it decide what to do next until it feels done. That works on a frontier
model and fails on a 7B one running on a laptop: it loops, it forgets step two
by step five, and it stops early because stopping is always the locally
plausible next token.

So the control flow here is an ordinary Python state machine. The model is
called at exactly four points, each time for one small, bounded, SCHEMA-
CONSTRAINED job:

  1. decompose the question into sub-questions      (list[str])
  2. reformulate a query that came back thin        (str)
  3. spot disagreements between extracted passages  (list of pairs)
  4. write the report from the passages we chose    (list of blocks)

Everything else -- what to search, when to stop, what counts as thin, which
source is independent of which -- is deterministic code. That is why this works
on a small model, and it is also why the UI can draw an honest progress bar:
Python knows the plan up front, so "3 of 6 sub-questions" is a fact rather than
a guess.

TRUST BOUNDARY, unchanged from tools/research.py: search keys stay in this
process, page fetching happens inside the Docker sandbox with no credentials,
and every extracted passage goes through the security gateway before the model
sees it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core import events
from memory.vector_store import cosine
from research import providers
from research.providers import SearchHit, root_domain

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

RESEARCH_IMAGE = "arthur-research:2"  # :2 adds pypdf — see sandbox/images/research.Dockerfile

# Depth is a budget, not a quality setting. Everything downstream (how many
# providers, how many pages, whether a gap pass runs) is derived from here so
# the estimate shown in the UI and the work actually done can never drift.
DEPTHS: dict[str, dict[str, Any]] = {
    "quick":      {"subs": 3, "per_lane": 3, "passages": 4,  "gap_pass": False},
    "standard":   {"subs": 4, "per_lane": 5, "passages": 6,  "gap_pass": True},
    "exhaustive": {"subs": 6, "per_lane": 7, "passages": 8,  "gap_pass": True},
}

# A lane with fewer than this many readable sources is "thin" and earns a
# second pass. Two is the threshold because one source cannot corroborate
# itself -- the same rule the confidence marker uses later.
THIN_BELOW = 2

MAX_PARALLEL_LANES = 2  # more just queues on Ollama and makes the UI lie


# ---------- schemas handed to the model (see OllamaClient.chat_json) ----------

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sub_questions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 6,
        }
    },
    "required": ["sub_questions"],
}

QUERY_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}

CONTRADICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                    "note": {"type": "string"},
                },
                "required": ["a", "b", "note"],
            },
        }
    },
    "required": ["conflicts"],
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["h", "p", "q"]},
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["type", "text", "citations"],
            },
            "minItems": 2,
            "maxItems": 14,
        }
    },
    "required": ["blocks"],
}


class ResearchEngine:
    def __init__(self, llm, vault, sandbox, embedder, gateway, allow_unsandboxed: bool = False):
        self._llm = llm
        self._vault = vault
        self._sandbox = sandbox
        self._embedder = embedder
        self._gateway = gateway
        self._allow_unsandboxed = allow_unsandboxed

    # ---------------- step 1: plan ----------------

    async def plan(self, question: str, depth: str, model: str) -> list[str]:
        """Decompose into sub-questions the user can edit before anything runs.

        Editable-before-run is the whole point: it is cheaper to fix a bad plan
        in two seconds than to watch four minutes of searching go the wrong way,
        and it is how a 7B model's weak planning stops being a dead end.
        """
        n = DEPTHS.get(depth, DEPTHS["standard"])["subs"]
        msgs = [
            {"role": "system", "content":
                "You break research questions into independent sub-questions. Each sub-question "
                "must be answerable by searching, must stand alone (no 'it' or 'they' referring to "
                "another line), and must not repeat another sub-question. Write them as statements "
                "of what to find out, without question marks."},
            {"role": "user", "content":
                f"Break this into exactly {n} sub-questions:\n\n{question}"},
        ]
        try:
            data = await self._llm.chat_json(model, msgs, PLAN_SCHEMA)
        except Exception as e:
            log.warning("plan generation failed: %s", e)
            data = None

        subs = [s.strip() for s in ((data or {}).get("sub_questions") or []) if s.strip()]
        if not subs:
            # Never dead-end the user on a model hiccup: one lane on the raw
            # question still produces a usable investigation.
            subs = [question.strip()]
        return subs[:n]

    # ---------------- step 2: run ----------------

    async def run(
        self,
        question: str,
        subs: list[str],
        depth: str,
        source_kinds: list[str],
        model: str,
        emit: Emit,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> None:
        cfg = DEPTHS.get(depth, DEPTHS["standard"])
        tavily_key = self._vault.get("tavily") or ""
        wants_web = any(k in source_kinds for k in ("web", "news", "docs"))
        if wants_web and not tavily_key and "academic" not in source_kinds:
            await emit(events.ERROR, {"code": "tavily_missing",
                                      "message": "Add a Tavily API key in Settings -> Integrations."})
            return

        started = time.monotonic()
        lanes = [{"id": f"sq{i}", "text": t, "state": "queued", "read": 0,
                  "of": 0, "srcs": 0, "pass": 1} for i, t in enumerate(subs)]
        for lane in lanes:
            await emit(events.RESEARCH_LANE, dict(lane))

        # Every source found in the whole run, in discovery order. `n` is the
        # citation number the report will use, so it is assigned once, here, and
        # never renumbered -- a citation that changes number mid-run is a bug the
        # user would experience as the evidence panel scrolling to the wrong card.
        collected: list[dict] = []
        first_n_by_domain: dict[str, int] = {}  # domain -> citation number of its FIRST hit
        seen_urls: set[str] = set()
        lock = asyncio.Lock()

        async def add_source(hit: SearchHit, lane_idx: int, passage: str) -> dict | None:
            async with lock:
                key = (hit.doi or hit.url).lower()
                if key in seen_urls:
                    return None
                seen_urls.add(key)

                dom = hit.domain or root_domain(hit.url)
                first_from_domain = first_n_by_domain.get(dom)
                n = len(collected) + 1

                src = {
                    "id": f"e{n}",
                    "n": n,
                    "kind": hit.kind,
                    "provider": hit.provider,
                    "title": hit.title,
                    "url": hit.url,
                    "domain": dom,
                    "date": hit.date,
                    "type": hit.type,
                    "authors": hit.authors,
                    "year": hit.year,
                    "venue": hit.venue,
                    "cites": hit.cites,
                    "doi": hit.doi,
                    "pdf_url": hit.pdf_url,
                    "is_pdf": hit.is_pdf,
                    "pages": hit.pages,
                    "sub": lane_idx,
                    "used": False,          # set during synthesis
                    "passage": passage,
                    "contradicts": "",
                    "contra_note": "",
                    # Second and later hits from a publisher we already have are
                    # marked as reprints rather than counted as fresh evidence.
                    "dup_of": "" if first_from_domain is None else f"e{first_from_domain}",
                }
                if first_from_domain is None:
                    first_n_by_domain[dom] = n
                collected.append(src)
                return src

        sem = asyncio.Semaphore(MAX_PARALLEL_LANES)

        async def work_lane(idx: int, query: str, pass_no: int) -> None:
            lane = lanes[idx]
            lane["pass"] = pass_no
            async with sem:
                lane["state"] = "searching"
                await emit(events.RESEARCH_LANE, dict(lane))

                hits = await providers.gather(
                    query, source_kinds, tavily_key,
                    per_provider=cfg["per_lane"],
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                )
                hits = [h for h in hits if (h.doi or h.url).lower() not in seen_urls]
                hits = hits[: cfg["per_lane"]]

                if not hits:
                    lane["state"] = "blocked"
                    await emit(events.RESEARCH_LANE, dict(lane))
                    return

                lane["state"] = "reading"
                lane["read"], lane["of"] = 0, len(hits)
                await emit(events.RESEARCH_LANE, dict(lane))

                read_hits = await self._read(hits)
                lane["read"] = sum(1 for h in read_hits if h.text)
                await emit(events.RESEARCH_LANE, dict(lane))

                kept = 0
                for hit in read_hits:
                    body = hit.text or hit.snippet
                    if not body.strip():
                        continue
                    passage = await self._best_passage(query, body, cfg["passages"])
                    safe = await self._scan(passage, hit.url)
                    src = await add_source(hit, idx, safe)
                    if src:
                        kept += 1
                        await emit(events.RESEARCH_SOURCE, src)

                lane["srcs"] = kept
                lane["state"] = "done" if kept >= THIN_BELOW else ("thin" if kept else "blocked")
                await emit(events.RESEARCH_LANE, dict(lane))

        await asyncio.gather(*(work_lane(i, lane["text"], 1) for i, lane in enumerate(lanes)))

        # ---------------- step 3: gap-fill ----------------
        # A first pass that came back thin is not a failure, it is information:
        # the query was wrong for the index. Reformulate it once and go again.
        # Bounded to ONE extra pass on purpose -- unbounded retry is how agents
        # burn ten minutes discovering the same nothing.
        thin = [i for i, lane in enumerate(lanes) if lane["state"] == "thin"]
        if cfg["gap_pass"] and thin:
            await emit(events.RESEARCH_GAP, {
                "ids": [lanes[i]["id"] for i in thin],
                "note": f"{len(thin)} sub-question{'s' if len(thin) > 1 else ''} came back thin. Searching again with reworded queries.",
            })
            requeries = await asyncio.gather(
                *(self._requery(question, lanes[i]["text"], model) for i in thin)
            )
            await asyncio.gather(
                *(work_lane(i, q, 2) for i, q in zip(thin, requeries, strict=True))
            )

        if not collected:
            await emit(events.ERROR, {"code": "zero_results",
                                      "message": "Every sub-question returned empty."})
            return

        # ---------------- step 4: contradictions ----------------
        await emit(events.STATUS, {"text": "Comparing sources against each other"})
        for a, b, note in await self._find_conflicts(collected, model):
            collected[a - 1]["contradicts"] = collected[b - 1]["id"]
            collected[a - 1]["contra_note"] = note
            collected[b - 1]["contradicts"] = collected[a - 1]["id"]
            collected[b - 1]["contra_note"] = note
            await emit(events.RESEARCH_SOURCE, collected[a - 1])
            await emit(events.RESEARCH_SOURCE, collected[b - 1])

        # ---------------- step 5: synthesise ----------------
        await emit(events.STATUS, {"text": "Writing the report"})
        blocks = await self._synthesise(question, collected, model)
        for src in collected:
            if src["used"]:
                await emit(events.RESEARCH_SOURCE, src)
        for block in blocks:
            await emit(events.RESEARCH_BLOCK, block)

        await emit(events.DONE, {
            "sources": len(collected),
            "independent": len({s["domain"] for s in collected}),
            "elapsed_s": round(time.monotonic() - started, 1),
        })

    # ---------------- reading ----------------

    async def _read(self, hits: list[SearchHit]) -> list[SearchHit]:
        """Fill in `.text` for each hit by fetching the page in the sandbox.

        Papers get a free pass: OpenAlex/arXiv/Crossref already handed us an
        abstract, so even if the PDF is paywalled or Docker is off, the hit is
        still usable evidence rather than a blocked lane.
        """
        payload = json.dumps({"urls": [h.url for h in hits if h.url]})
        rows: list[dict] = []
        try:
            await self._sandbox.ensure_image(RESEARCH_IMAGE, "research.Dockerfile")
            res = await self._sandbox.run(
                RESEARCH_IMAGE, [], stdin_data=payload,
                network="bridge", timeout_s=90, mem_limit="768m",
            )
            rows = [json.loads(line) for line in res.stdout.splitlines() if line.strip()]
        except Exception as e:
            log.info("sandboxed fetch unavailable (%s)", e)
            if self._allow_unsandboxed:
                rows = await self._fetch_unsandboxed([h.url for h in hits if h.url])

        by_url = {r.get("url", ""): r for r in rows}
        for hit in hits:
            row = by_url.get(hit.url) or {}
            hit.text = (row.get("text") or "")[:40_000]
            hit.error = row.get("error") or ""
            hit.is_pdf = bool(row.get("is_pdf"))
            hit.pages = int(row.get("pages") or 0)
            if not hit.text and hit.snippet:
                hit.text = hit.snippet  # abstract / search snippet beats nothing
        return hits

    async def _fetch_unsandboxed(self, urls: list[str]) -> list[dict]:
        """Opt-in degraded path when Docker is off (Settings toggle). Same
        trade-off already documented in SECURITY.md for tools/research.py."""
        import httpx

        out = []
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            for url in urls[:8]:
                row = {"url": url, "title": url, "text": "", "error": None, "is_pdf": False, "pages": 0}
                try:
                    resp = await client.get(url)
                    ctype = resp.headers.get("content-type", "")
                    if "pdf" in ctype or url.lower().endswith(".pdf"):
                        row["is_pdf"] = True
                        row["text"], row["pages"] = _pdf_text(resp.content)
                    else:
                        try:
                            import trafilatura
                            row["text"] = (trafilatura.extract(resp.text) or "")[:40_000]
                        except ImportError:
                            row["text"] = resp.text[:20_000]
                except Exception as e:
                    row["error"] = str(e)[:200]
                out.append(row)
        return out

    async def _best_passage(self, query: str, body: str, k: int) -> str:
        """Same chunk -> embed -> cosine retrieve as tools/research.py, kept to
        the top few chunks. A whole page is 100k characters of mostly nothing;
        the model's context is the scarcest resource in this app."""
        from tools.research import chunk_text

        chunks = chunk_text(body)[:24]
        if not chunks:
            return ""
        if len(chunks) == 1:
            return chunks[0]
        try:
            vectors = await self._embedder.embed(chunks)
            [qvec] = await self._embedder.embed([query])
            ranked = sorted(zip(chunks, vectors, strict=True),
                            key=lambda pair: cosine(qvec, pair[1]), reverse=True)
            return "\n\n".join(c for c, _ in ranked[:k])[:3000]
        except Exception:
            return "\n\n".join(chunks[:k])[:3000]  # embeddings down -> first chunks

    async def _scan(self, text: str, source: str) -> str:
        """Extracted page text is untrusted input -- a page can contain
        "ignore previous instructions". Same gateway the chat path uses."""
        try:
            return await self._gateway.scan_model_output(text)
        except Exception:
            return text

    # ---------------- model-assisted steps ----------------

    async def _requery(self, question: str, sub: str, model: str) -> str:
        msgs = [
            {"role": "system", "content":
                "You rewrite failed search queries. The previous wording returned almost nothing. "
                "Produce ONE new query: different vocabulary, more specific nouns, no boolean "
                "operators, no quotes, under 15 words."},
            {"role": "user", "content": f"Overall topic: {question}\nQuery that failed: {sub}"},
        ]
        try:
            data = await self._llm.chat_json(model, msgs, QUERY_SCHEMA)
            q = ((data or {}).get("query") or "").strip()
            return q or sub
        except Exception:
            return sub

    async def _find_conflicts(self, sources: list[dict], model: str) -> list[tuple[int, int, str]]:
        """Ask which passages disagree -- and show BOTH when they do.

        The tempting alternative is to let the writing step quietly pick the
        source it likes. That produces a confident report that hides the single
        most useful fact available: that the record is not settled.
        """
        numbered = "\n\n".join(
            f"[{s['n']}] ({s['domain'] or s['venue']}) {s['passage'][:700]}"
            for s in sources[:14]
        )
        msgs = [
            {"role": "system", "content":
                "You compare source passages and report only DIRECT factual disagreements: two "
                "sources stating incompatible things about the same fact. Different topics, "
                "different emphasis, or one source simply being silent are NOT disagreements. "
                "Return an empty list if there are none. Reference sources by their number."},
            {"role": "user", "content": numbered},
        ]
        try:
            data = await self._llm.chat_json(model, msgs, CONTRADICTION_SCHEMA)
        except Exception as e:
            log.info("conflict detection skipped: %s", e)
            return []

        valid = {s["n"] for s in sources}
        out: list[tuple[int, int, str]] = []
        for c in ((data or {}).get("conflicts") or [])[:4]:
            a, b = c.get("a"), c.get("b")
            if a in valid and b in valid and a != b:
                out.append((a, b, (c.get("note") or "").strip()[:220]))
        return out

    async def _synthesise(self, question: str, sources: list[dict], model: str) -> list[dict]:
        """Write the report, then compute confidence OURSELVES.

        The model is never asked how confident it is. Self-reported confidence
        from a small model is close to noise, and a report full of hedging it
        invented would be worse than no marking at all. Instead confidence is a
        deterministic function of the citations the model actually attached:

            2+ citations from DIFFERENT publishers -> supported (unmarked)
            exactly 1, or 2 from the same publisher -> thin
            none at all                             -> unverified

        That rule is legible, reproducible, and cannot flatter itself.
        """
        numbered = "\n\n".join(
            f"[{s['n']}] {s['title']} ({s['domain'] or s['venue']})\n{s['passage'][:900]}"
            for s in sources[:14]
        )
        msgs = [
            {"role": "system", "content":
                "You write short research reports from source passages. Rules: start with one "
                "heading block ('h'). Place a marker like [3] in the sentence itself, immediately "
                "after the claim that source supports, and ALSO list every number you used in "
                "`citations`. If sources disagree, write a 'q' block that states both positions "
                "and cites both. Never state anything the passages do not support. Plain "
                "sentences, no markdown syntax, no bullet characters."},
            {"role": "user", "content": f"Question: {question}\n\nSources:\n{numbered}"},
        ]
        try:
            data = await self._llm.chat_json(model, msgs, REPORT_SCHEMA, temperature=0.2)
        except Exception as e:
            log.warning("synthesis failed: %s", e)
            data = None

        raw = (data or {}).get("blocks") or []
        if not raw:
            raw = [{"type": "h", "text": question[:120], "citations": []}] + [
                {"type": "p", "text": s["passage"][:400], "citations": [s["n"]]}
                for s in sources[:4]
            ]

        by_n = {s["n"]: s for s in sources}
        blocks: list[dict] = []
        import re

        for i, b in enumerate(raw):
            # Union of what the model DECLARED and what it actually wrote inline.
            # Small models routinely do one and forget the other; taking both
            # means a [4] in the prose always turns into a clickable pill, and a
            # declared source always counts toward the confidence rule.
            inline = {int(m) for m in re.findall(r"\[(\d+)\]", b.get("text") or "")}
            cites = sorted({c for c in [*(b.get("citations") or []), *inline] if c in by_n})
            for c in cites:
                by_n[c]["used"] = True
            domains = {by_n[c]["domain"] or by_n[c]["venue"] for c in cites}
            conf = "ok" if len(domains) >= 2 else ("thin" if cites else "unverified")
            if b.get("type") == "h":
                conf = "ok"  # a title makes no factual claim
            blocks.append({
                "id": f"b{i + 1}",
                "type": b.get("type") if b.get("type") in ("h", "p", "q") else "p",
                "text": (b.get("text") or "").strip(),
                "citations": cites,
                "conf": conf,
                "ai": True,
                "fresh": True,
            })
        return blocks


def _pdf_text(data: bytes) -> tuple[str, int]:
    """Papers are PDFs. A research tool that cannot read a PDF can only ever
    read what other people wrote ABOUT the research. Returns (text, real
    page count read) -- same contract as the sandboxed fetch_pages.py so the
    "Np read" badge means the same thing on both paths."""
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        read = reader.pages[:40]
        pages = [(p.extract_text() or "") for p in read]
        return "\n\n".join(pages)[:40_000], len(read)
    except Exception as e:
        log.info("pdf extraction failed: %s", e)
        return "", 0
