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
import re
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

# How many sources any one section is written from.
#
# Was 8, and that number alone accounted for most of "only 2 of 40 sources got
# used": a section could not cite what it was never shown. Fourteen is chosen
# against the context budget rather than plucked -- at ~600 chars of passage
# each that is roughly 2k tokens of evidence per call, which an 8B model still
# handles well, and it is enough breadth for a paragraph to genuinely compare
# several studies instead of paraphrasing one.
#
# This only became safe once providers.gather started ranking by relevance. A
# wider window over an unranked list would have meant MORE off-topic material
# reaching the writer, not less.
SOURCES_PER_SECTION = 14


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

# One section at a time, NOT one call for the whole paper. Two reasons, both
# load-bearing on a 7B model:
#   * Context. A section only needs the sources from its own sub-question's
#     lane, so each call sees ~4 passages instead of ~14. Small models degrade
#     sharply as context fills; this keeps every call in the range where they
#     are actually good.
#   * Progressive rendering. Sections stream into the paper as they finish,
#     so a four-minute write shows visible progress instead of a blank page.
SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "heading": {"type": "string"},
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "citations"],
            },
            "minItems": 1,
            # Raised from 6 when word targets arrived. A 1200-word thematic
            # section is about eleven paragraphs, so a cap of 6 would have made
            # the target physically unreachable -- the schema would have
            # silently overruled the instruction and the user would have seen a
            # length control that did nothing.
            "maxItems": 14,
        },
        # Overridden per call by _section_schema() -- see there for why the
        # minimum has to move rather than sit at 1.
        # A table, only when the section genuinely compares the same handful of
        # attributes across several things. Optional in the schema on purpose:
        # forcing one would guarantee tables full of invented rows, and a
        # literature review with a fabricated comparison table is worse than
        # one with none. Every row's last cell is a source number, so a table
        # is as traceable as a sentence.
        "table": {
            "type": "object",
            "properties": {
                "caption": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"},
                            "minItems": 2, "maxItems": 5},
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "minItems": 2, "maxItems": 8,
                },
                "row_sources": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["caption", "columns", "rows", "row_sources"],
        },
    },
    "required": ["heading", "paragraphs"],
}

# The same job, asked in the simplest shape that can still carry citations.
# A 3B model handed SECTION_SCHEMA reliably returns the right STRUCTURE with
# empty strings inside it -- it satisfies the grammar and says nothing. Fewer
# nested objects and no optional table leaves it with less to track, and the
# citations come back as inline [n] markers in one flat string, which small
# models are much better at than populating a parallel array.
SIMPLE_SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "heading": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["heading", "body"],
}

# Rough parameter count below which the simplified path is used. Not a hard
# science -- it is the point where, in practice, nested-schema compliance
# starts failing more often than it succeeds.
def _section_schema(min_paras: int) -> dict:
    """SECTION_SCHEMA with the paragraph floor moved.

    WHY the floor has to move at all: the schema said minItems 1, so a model
    that produced a single paragraph was VALID, and a thematic section made of
    one paragraph is what produced the "why is there a header after every
    paragraph" complaint -- the paper was structurally a list of headings with
    a sentence under each, not a paper. A literature review's body sections
    carry several paragraphs each; saying so in the grammar is more reliable
    than asking for it in the prompt, because the grammar is enforced and the
    prompt is a suggestion.

    Intro and conclusion keep a lower floor: they are meant to be short, and
    demanding three paragraphs of conclusion produces padding.
    """
    import copy

    schema = copy.deepcopy(SECTION_SCHEMA)
    schema["properties"]["paragraphs"]["minItems"] = max(1, min_paras)
    return schema


SMALL_MODEL_B = 8.0

TITLE_ABSTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "abstract": {"type": "string"},
    },
    "required": ["title", "abstract"],
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
        target_words: int = 0,
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
                pub = hit.publisher or dom
                # Reprint collapsing applies to WEB pages only. Five outlets
                # running one wire story is one voice; two papers in the same
                # journal are two independent pieces of research and must never
                # be folded together. Papers are already deduped by DOI upstream.
                first_from_domain = first_n_by_domain.get(pub) if hit.kind != "paper" else None
                n = len(collected) + 1

                src = {
                    "id": f"e{n}",
                    "n": n,
                    "kind": hit.kind,
                    "provider": hit.provider,
                    "title": hit.title,
                    "url": hit.url,
                    "domain": dom,
                    "publisher": pub,   # independence key -- see providers.SearchHit
                    "date": hit.date,
                    "type": hit.type,
                    "authors": hit.authors,        # display string, evidence card
                    "authors_list": hit.authors_list,  # structured, citation formatter
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
                if first_from_domain is None and hit.kind != "paper":
                    first_n_by_domain[pub] = n
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

        await self._finish(question, collected, model, emit, started, subs,
                           target_words=target_words)

    async def synthesize_only(
        self, question: str, sources: list[dict], model: str, emit: Emit,
        subs: list[str] | None = None, target_words: int = 0,
    ) -> None:
        """Steps 4+5 (contradictions, then the report) run WITHOUT searching
        again, over sources the caller already has.

        WHY this exists as its own entry point: search is the expensive,
        re-runnable part of an investigation; contradiction-checking and
        writing are comparatively quick and, unlike search, produce something
        the person is actually waiting on. If a run gets interrupted (Stop
        pressed, a slow model call) after search finished but before the
        report was written, re-running the WHOLE investigation to get a
        report out of sources already sitting in the evidence panel would be
        wasteful and confusing -- the UI's "Write the report now" action
        (stores/research.js) calls this directly with the evidence it already
        has, instead.
        """
        started = time.monotonic()
        if not sources:
            await emit(events.ERROR, {"code": "zero_results",
                                      "message": "There is nothing to write a report from yet."})
            return
        await self._finish(question, sources, model, emit, started, subs or [],
                           target_words=target_words)

    async def find_more(
        self, query: str, existing: list[dict], source_kinds: list[str], emit: Emit,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> None:
        """Search for what the user asked for, add whatever is genuinely new.

        Two rules make this safe to press repeatedly:

        1. CITATION NUMBERS CONTINUE, they never restart. New sources are
           numbered from max(existing n) + 1, so `[7]` in a paragraph written
           an hour ago still points at the same source it always did. Renumbering
           would silently rewrite every citation in the paper.
        2. IT DOES NOT TOUCH THE PAPER. Sources arrive in the panel and the UI
           offers a rewrite; it does not rewrite on its own. By this point the
           person may have edited the text themselves, and quietly regenerating
           over their edits to incorporate a source they merely went looking
           for would be the wrong trade every time.
        """
        tavily_key = self._vault.get("tavily") or ""
        await emit(events.STATUS, {"text": f"Searching for: {query}"})

        hits = await providers.gather(
            query, source_kinds or ["web", "academic"], tavily_key,
            per_provider=5, include_domains=include_domains, exclude_domains=exclude_domains,
        )
        seen = {(s.get("doi") or s.get("url") or "").lower() for s in existing}
        hits = [h for h in hits if (h.doi or h.url).lower() not in seen][:6]
        if not hits:
            await emit(events.STATUS, {"text": "No new sources found for that search."})
            await emit(events.DONE, {"added": 0})
            return

        await emit(events.STATUS, {"text": f"Reading {len(hits)} new sources"})
        read_hits = await self._read(hits)

        next_n = max((int(s.get("n") or 0) for s in existing), default=0) + 1
        domains = {(s.get("domain") or "") for s in existing}
        added = 0
        for hit in read_hits:
            body = hit.text or hit.snippet
            if not body.strip():
                continue
            passage = await self._best_passage(query, body, 6)
            safe = await self._scan(passage, hit.url)
            dom = hit.domain or root_domain(hit.url)
            src = {
                "id": f"e{next_n}", "n": next_n, "kind": hit.kind, "provider": hit.provider,
                "title": hit.title, "url": hit.url, "domain": dom, "date": hit.date,
                "type": hit.type, "authors": hit.authors, "authors_list": hit.authors_list,
                "year": hit.year, "venue": hit.venue, "cites": hit.cites, "doi": hit.doi,
                "pdf_url": hit.pdf_url, "is_pdf": hit.is_pdf, "pages": hit.pages,
                "sub": None,  # not from a planned lane -- it was asked for directly
                "used": False, "passage": safe, "contradicts": "", "contra_note": "",
                "dup_of": "" if dom not in domains else "",
                "added_manually": True,
            }
            domains.add(dom)
            await emit(events.RESEARCH_SOURCE, src)
            next_n += 1
            added += 1

        await emit(events.DONE, {"added": added})

    async def _finish(
        self, question: str, collected: list[dict], model: str, emit: Emit, started: float,
        subs: list[str] | None = None, target_words: int = 0,
    ) -> None:
        # ---------------- step 4: contradictions ----------------
        await emit(events.STATUS, {"text": "Comparing sources against each other"})
        for a, b, note in await self._find_conflicts(collected, model):
            collected[a - 1]["contradicts"] = collected[b - 1]["id"]
            collected[a - 1]["contra_note"] = note
            collected[b - 1]["contradicts"] = collected[a - 1]["id"]
            collected[b - 1]["contra_note"] = note
            await emit(events.RESEARCH_SOURCE, collected[a - 1])
            await emit(events.RESEARCH_SOURCE, collected[b - 1])

        # ---------------- step 5: write the paper ----------------
        # `phase` is what lets the UI tell "still writing" apart from
        # "finished". Without it the report screen showed a source count the
        # moment the FIRST section arrived, which reads as a completed paper,
        # so a run that was quietly still working for another two minutes
        # looked like a run that had produced one paragraph and given up. The
        # text is for the human; the phase is for the client.
        await emit(events.STATUS, {"text": "Writing the paper", "phase": "writing"})
        # Without an approved plan (the "write from what we already have" path
        # after a stop), recover the section structure from which lane each
        # source came back on -- the sub-questions are gone but their grouping
        # survives on the sources themselves.
        outline = subs or _outline_from_sources(collected, question)
        await self._write_paper(question, collected, outline, model, emit,
                                target_words=target_words)
        for src in collected:
            if src["used"]:
                await emit(events.RESEARCH_SOURCE, src)

        await emit(events.STATUS, {"text": "", "phase": "done"})
        await emit(events.DONE, {
            "sources": len(collected),
            "independent": len({s.get("publisher") or s.get("domain") for s in collected}),
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

    # ---------------- the paper ----------------

    async def _write_paper(
        self, question: str, sources: list[dict], subs: list[str], model: str, emit: Emit,
        target_words: int = 0,
    ) -> None:
        """Build a literature review, one section at a time.

        THE STRUCTURE COMES FROM THE PLAN THE USER ALREADY APPROVED. Each
        sub-question they reviewed on the plan screen becomes one thematic
        section of the paper, in the order they left them. That is not a
        shortcut -- it is the payoff for making them review the plan: the
        outline was agreed before a single search ran, so the finished paper
        cannot wander off into a shape they never asked for.

        Section order: Introduction, one per sub-question, Discussion,
        Conclusion. The abstract is written LAST, from the finished sections,
        for the same reason people write it last: you cannot summarise a paper
        you have not written yet.
        """
        by_n = {s["n"]: s for s in sources}
        sections: list[dict] = []

        # A provisional title, emitted BEFORE any section is written.
        #
        # WHY up front, when the real title is written last from the finished
        # body: `paper` stays null on the client until this event arrives (see
        # ResearchPaper.jsx), so a write that was cut short -- stopped, timed
        # out, disconnected -- left the user staring at a document headed
        # "Untitled paper". The title step is the LAST thing this function
        # does, which is exactly the thing a truncated run never reaches. A
        # placeholder derived from the question costs one emit and guarantees
        # the paper always has a name; the real one overwrites it below.
        await emit(events.RESEARCH_PAPER, {
            "title": _title_case(question[:90]),
            "abstract": "",
            "question": question,
            "sections": [],  # empty => the client keeps whatever it has streamed
        })

        # How long each section should be. Split across Introduction + one per
        # sub-question + Discussion + Conclusion, with the short sections given
        # less than the thematic ones because that is how papers are actually
        # shaped -- an even split produces a conclusion as long as a findings
        # section, which reads wrong.
        budget = _word_budget(target_words, len(subs))

        async def add_section(heading: str, paragraphs: list[dict], kind: str) -> None:
            sec = {
                "id": f"s{len(sections) + 1}",
                "kind": kind,           # intro | theme | discussion | conclusion
                "heading": heading,
                "paragraphs": paragraphs,
                "order": len(sections),
            }
            sections.append(sec)
            await emit(events.RESEARCH_SECTION, sec)
            # Re-emit every source this section just cited, so the evidence
            # panel's USED/NOT USED badge is right AS THE PAPER IS WRITTEN.
            #
            # It used to update only after the whole paper finished (see the
            # loop at the end of _finish), which meant the badges were wrong for
            # the entire write -- and permanently wrong for any run that was
            # stopped or interrupted, because that loop was never reached. A
            # source that the paper visibly cites while still displaying
            # "NOT USED" is the panel contradicting the document.
            cited_now = {c for p in paragraphs for c in (p.get("citations") or [])}
            for n in sorted(cited_now):
                src = by_n.get(n)
                if src:
                    await emit(events.RESEARCH_SOURCE, src)

        # `_write_section` already swallows failures from the MODEL CALL itself
        # (see its own try/except around chat_json) and falls back to a bare
        # cited extract when the model returns nothing usable. This wrapper
        # covers the other failure mode: something raising from THIS function
        # for a reason that has nothing to do with the model (a bad value
        # slipping through, an embedder hiccup, anything unforeseen). Without
        # it, one bad section aborted the whole rest of the paper -- the loop
        # below never reached Discussion, Conclusion, or the title/abstract
        # step, which is exactly what produced a paper with only an
        # Introduction and a title that fell back to "Untitled paper" client
        # side (paper stays null until RESEARCH_PAPER is emitted -- see
        # ResearchPaper.jsx). Every section is now guaranteed to produce
        # SOMETHING, so the paper this function emits is always complete.
        async def write_or_fallback(
            *, heading: str, brief: str, sources: list[dict], words: int = 0, min_paras: int = 1,
        ) -> tuple[list[dict], str]:
            try:
                return await self._write_section(
                    model=model, heading=heading, brief=brief, sources=sources, by_n=by_n,
                    words=words, min_paras=min_paras,
                )
            except Exception as e:
                log.warning("section '%s' raised unexpectedly: %s", heading, e)
                # Same reasoning as the fallback inside _write_section: no
                # verbatim extract standing in for prose the app did not write.
                return [{
                    "id": "p1",
                    "text": (
                        "Arthur could not write this section — the writing step failed part way "
                        "through. Nothing gathered for it has been lost; the sources are still in "
                        "the panel."
                    ),
                    "citations": [],
                    "conf": "unverified",
                    "kind": "notice",
                }], ""

        # --- Introduction: framing only, drawn from the question and the plan.
        # Fixed headings ignore whatever the model proposes -- "Introduction"
        # is not a creative decision.
        intro, _ = await write_or_fallback(
            heading="Introduction",
            brief=(
                f"Write the introduction to a literature review answering: {question}\n\n"
                f"The review will examine, in order:\n"
                + "\n".join(f"- {s}" for s in subs)
                + "\n\nState what the question is, why it is contested or worth reviewing, and "
                "what the review will cover. Do not state findings yet."
            ),
            sources=sources[:SOURCES_PER_SECTION],
            words=budget["intro"],
            min_paras=2,
        )
        await add_section("Introduction", intro, "intro")

        # --- One section per sub-question, each seeing ONLY its own lane's
        # sources. This is the context discipline that makes small models work.
        for idx, sub in enumerate(subs):
            # A lane's OWN sources first, then the best of the rest to fill out
            # the window. Strict lane isolation was the right call when the
            # window was eight and models were small, but it also meant a lane
            # that found three sources could only ever cite three, while
            # thirty-odd relevant sources sat unused in the panel. Own-lane
            # material still leads, so the section is still ABOUT its
            # sub-question -- it just is not starved.
            lane_sources = [s for s in sources if s.get("sub") == idx]
            others = [s for s in sources if s.get("sub") != idx]
            pool = (lane_sources + others)[:SOURCES_PER_SECTION]
            written, proposed = await write_or_fallback(
                heading=sub,
                brief=(
                    f"Write one section of a literature review. The overall question is: {question}\n\n"
                    f"This section covers: {sub}\n\n"
                    "Report what the sources actually establish, note where they agree, and say "
                    "plainly where they disagree. Draw on as many of the passages as genuinely "
                    "bear on this theme, not just the first few.\n\n"
                    "Give the section a short noun-phrase heading of at most six words -- a topic, "
                    "not a sentence. Do not repeat the sub-question."
                ),
                sources=pool,
                words=budget["theme"],
                # Body sections of a literature review carry several paragraphs.
                # One paragraph under a heading is what made the paper read as a
                # list of headings rather than a document.
                min_paras=3,
            )
            # Thematic sections DO take the model's heading when it gave one:
            # "Licensing and commercial use" reads better than the raw
            # sub-question it came from. The fallback now SHORTENS the
            # sub-question instead of printing it whole -- a heading reading
            # "Differences in attention span and focus maintenance between ADHD
            # and neurotypical individuals" is a sentence, and a paper whose
            # headings are sentences does not look like a paper.
            await add_section(_short_heading(proposed or sub), written, "theme")

        # --- Discussion: the only section allowed to reason ACROSS sections,
        # and the natural home for contradictions the pipeline already found.
        conflicts = [s for s in sources if s.get("contradicts")]
        discussion, _ = await write_or_fallback(
            heading="Discussion",
            brief=(
                f"Write the discussion section of a literature review on: {question}\n\n"
                "Draw the themes together. Say what the evidence supports overall, what remains "
                "unsettled, and where the sources are thin. "
                + ("Sources disagree on some points -- name those disagreements explicitly "
                   "rather than resolving them." if conflicts else "")
            ),
            # Conflicts LEAD the discussion's evidence but no longer replace it.
            # `conflicts or sources` meant that when the contradiction check
            # found two disagreeing sources, the entire discussion section was
            # written from those two and nothing else -- the most interesting
            # finding crowding out the synthesis it was supposed to sit inside.
            sources=_lead_with(conflicts, sources)[:SOURCES_PER_SECTION],
            words=budget["discussion"],
            min_paras=2,
        )
        await add_section("Discussion", discussion, "discussion")

        # --- Conclusion: no new citations, by construction.
        conclusion, _ = await write_or_fallback(
            heading="Conclusion",
            brief=(
                f"Write a short conclusion to a literature review on: {question}\n\n"
                "State the overall picture and what would need to be established next. "
                "Introduce no new claims."
            ),
            sources=sources[:4],
            words=budget["conclusion"],
        )
        await add_section("Conclusion", conclusion, "conclusion")

        # --- Title + abstract, written from the finished body. Already has its
        # own internal try/except (falls back to a title cased from the
        # question), so this call alone was never the failure -- it just used
        # to never be REACHED when an earlier section blew up the loop.
        body = "\n\n".join(
            f"{sec['heading']}\n" + "\n".join(p["text"] for p in sec["paragraphs"])
            for sec in sections
        )
        title, abstract = await self._write_title_abstract(question, body, model)
        await emit(events.RESEARCH_PAPER, {
            "title": title,
            "abstract": abstract,
            "question": question,
            "sections": sections,
        })

    async def _write_section(
        self, model: str, heading: str, brief: str, sources: list[dict], by_n: dict[int, dict],
        words: int = 0, min_paras: int = 1,
    ) -> tuple[list[dict], str]:
        """One section. Returns (paragraphs, heading the model proposed).

        The heading comes back separately rather than riding along inside the
        first paragraph: paragraphs are sent verbatim to the UI, and a private
        key smuggled into one of them would leak into the wire format.

        Paragraphs carry validated citations and a confidence level computed
        the same deterministic way as before:

            2+ citations from DIFFERENT publishers -> supported (unmarked)
            exactly 1, or several from one publisher -> thin
            none at all                              -> unverified

        The model is still never asked how confident it is. Self-reported
        confidence from a small model is close to noise; this rule is
        arithmetic over citations that actually resolve.
        """
        # SOURCES_PER_SECTION, not 8.
        #
        # The old window was eight, which on a forty-source investigation meant
        # thirty-two sources could never be cited by anything -- they were
        # gathered, shown in the evidence panel, and then structurally excluded
        # from the paper. That is the "only 2 of 40 sources used" complaint, and
        # most of it was this number rather than the model's behaviour.
        #
        # The window can be this wide now because `gather` ranks by relevance
        # (see providers.gather), so the first fourteen are the fourteen most
        # on-topic rather than the first fourteen a provider happened to return.
        # Passages are trimmed harder to pay for the extra breadth: more sources
        # at 600 chars beats fewer at 900, because a literature review needs to
        # see what several sources say about one thing in order to synthesise
        # rather than summarise.
        window = sources[:SOURCES_PER_SECTION]
        numbered = "\n\n".join(
            f"[{s['n']}] {s['title']} ({s.get('domain') or s.get('venue')})\n{s.get('passage', '')[:600]}"
            for s in window
        )
        # A length instruction in WORDS, and only when the user asked for one.
        #
        # WHY a range rather than an exact number: no language model can count
        # its own output, so "write exactly 400 words" is an instruction it
        # cannot follow and will silently ignore -- and an ignored instruction
        # costs context for nothing. A range it can aim at ("roughly 350-450")
        # measurably shifts length, which is all that is actually being asked
        # for. The real enforcement, if any, happens at export.
        length_rule = ""
        if words:
            length_rule = (
                # round(), not int(): 400 * 1.15 is 459.99999999999994 in binary
                # floating point, so int() truncated the upper bound to 459 and
                # the range silently drifted below what was asked for.
                f" Aim for roughly {round(words * 0.85)}-{round(words * 1.15)} words in this section: "
                f"about {max(1, round(words / 110))} full paragraphs. Do not pad to reach the "
                "length -- if the passages do not support more, write less."
            )

        msgs = [
            {"role": "system", "content":
                "You write sections of academic literature reviews. Rules: continuous prose in "
                "full paragraphs -- no bullet points, no markdown, no headings inside the text. "
                "Place a marker like [3] in the sentence itself, directly after the claim that "
                "source supports, and ALSO list every number used in `citations`. Never state "
                "anything the passages do not support. Do not describe the search process or "
                "refer to 'the sources' as objects; write about the subject matter.\n\n"
                # SYNTHESIS, NOT SUMMARY. This is the difference between a
                # literature review and an annotated bibliography, and it is
                # the thing a small model gets wrong by default: handed six
                # passages it will write six sentences, one per passage, in the
                # order it received them. Standard guidance for a thematic
                # review is explicit that body sections must integrate multiple
                # sources into one discussion and name where they agree,
                # disagree and fall silent -- so that is stated as a rule here
                # rather than hoped for.
                "SYNTHESISE, DO NOT SUMMARISE. Never walk through the sources one at a time. "
                "Each paragraph should make ONE point about the subject and bring together every "
                "source that bears on it, so a single sentence often carries two or three "
                "markers like [2][5]. Say explicitly where sources agree, where they disagree, "
                "and where the evidence is silent. Never write a paragraph that is about a "
                "single study.\n\n"
                "NEVER COPY A PASSAGE. The passages are evidence to write FROM, not text to "
                "reproduce. Reproducing an abstract verbatim is plagiarism even with a citation "
                "attached. Write the claim in your own words and cite it.\n\n"
                "Open the section by connecting it to the overall question, and close it with "
                "what the evidence in it establishes.\n\n"
                "Include a `table` ONLY when the passages give you the same few attributes for "
                "three or more distinct things (models, studies, jurisdictions). A table must "
                "compare facts that are actually in the passages -- never fill a cell by "
                "inference. `row_sources` gives the source number backing each row, in row "
                "order. If the material is not genuinely tabular, omit `table` entirely."},
            {"role": "user", "content": f"{brief}{length_rule}\n\nSources:\n{numbered}"},
        ]
        simple = model_is_small(model)
        try:
            data = await self._llm.chat_json(
                model, msgs,
                SIMPLE_SECTION_SCHEMA if simple else _section_schema(min_paras),
                temperature=0.2,
            )
        except Exception as e:
            log.warning("section '%s' failed: %s", heading, e)
            data = None

        if simple:
            # One flat string comes back; split it into paragraphs ourselves.
            # Citations are recovered from the inline [n] markers below, which
            # is why the union of declared-and-inline exists in the first place.
            body = ((data or {}).get("body") or "").strip()
            raw = [{"text": chunk.strip(), "citations": []}
                   for chunk in re.split(r"\n\s*\n", body) if chunk.strip()]
        else:
            raw = (data or {}).get("paragraphs") or []

        out: list[dict] = []
        for i, p in enumerate(raw):
            text = (p.get("text") or "").strip()
            if not text:
                continue
            # Union of DECLARED and INLINE citations. Small models routinely do
            # one and forget the other; taking both means a [4] in the prose
            # always resolves to a real source, and a declared source always
            # counts toward the confidence rule.
            inline = {int(m) for m in re.findall(r"\[(\d+)\]", text)}
            cites = sorted({c for c in [*(p.get("citations") or []), *inline] if c in by_n})
            for c in cites:
                by_n[c]["used"] = True
            publishers = {by_n[c].get("publisher") or by_n[c].get("domain") for c in cites}
            conf = "ok" if len(publishers) >= 2 else ("thin" if cites else "unverified")
            # No authorship flag: Arthur writes every paragraph in the paper,
            # so a per-paragraph "written by AI" marker would be true of all of
            # them and therefore tell the reader nothing.
            out.append({
                "id": f"p{i + 1}",
                "text": text,
                "citations": cites,
                "conf": conf,
            })

        # The fallback lives HERE, after filtering, not before it. A small
        # model that returns the right JSON shape with empty strings inside is
        # the common failure -- commoner than returning nothing at all -- and
        # checking `raw` alone let that through as a heading with no body,
        # which is what produced the empty "Introduction" bug. Judge the
        # output that survived validation, not the output that arrived.
        if not out and sources:
            # THE FALLBACK NO LONGER PASTES THE SOURCE'S OWN WORDS.
            #
            # It used to drop `passage[:600]` into the paper as body prose. That
            # is how a review of ADHD ended up opening with the verbatim
            # executive summary of a global asthma strategy, presented in the
            # paper's own voice: the reader had no way to tell they were reading
            # someone else's paragraph. Emitting an unmarked verbatim extract as
            # if the app had written it is manufacturing plagiarism, and it also
            # disguised a total model failure as a successful section.
            #
            # Saying plainly that the section could not be written is worse
            # output and a far better answer.
            best = sources[0]
            out = [{
                "id": "p1",
                "text": (
                    "Arthur could not write this section. The model returned nothing usable for "
                    f"it, so no claim is made here. The evidence gathered for this part of the "
                    f"question is still in the sources panel, starting with “{best.get('title') or 'an untitled source'}”. "
                    "Rewriting with a larger model, or narrowing this sub-question, usually fixes it."
                ),
                "citations": [],
                "conf": "unverified",
                # Marks this as an app message rather than written prose, so the
                # UI and the exporters can treat it differently from a claim.
                "kind": "notice",
            }]
            log.info("section '%s' produced nothing usable; emitted a failure notice", heading)

        table = _validate_table((data or {}).get("table"), by_n)
        if table:
            out.append({"id": f"p{len(out) + 1}", "kind": "table", "conf": "ok", **table})

        # The model's own heading for the section, if it produced a usable one.
        proposed = (data or {}).get("heading") if isinstance(data, dict) else None
        proposed = (proposed or "").strip()
        return out, (proposed if 3 < len(proposed) < 90 else "")

    async def _write_title_abstract(self, question: str, body: str, model: str) -> tuple[str, str]:
        msgs = [
            {"role": "system", "content":
                "You write the title and abstract for a finished literature review. The abstract "
                "is one paragraph, 120-200 words, covering scope, what the literature establishes, "
                "and what remains unsettled. No citations in the abstract. The title is a noun "
                "phrase, not a question, under 20 words."},
            {"role": "user", "content": f"Question reviewed: {question}\n\nThe paper:\n{body[:6000]}"},
        ]
        try:
            data = await self._llm.chat_json(model, msgs, TITLE_ABSTRACT_SCHEMA, temperature=0.3)
        except Exception as e:
            log.warning("title/abstract failed: %s", e)
            data = None
        title = ((data or {}).get("title") or "").strip() or _title_case(question)
        abstract = ((data or {}).get("abstract") or "").strip()
        return title[:200], abstract


def model_is_small(model: str) -> bool:
    """Guess parameter count from the model NAME, e.g. "llama3.1:8b" -> 8.

    Reading the name is crude, but it is the only signal available without a
    round trip to Ollama, and model names in this ecosystem are near-universally
    honest about size because that is the thing people pick on. When there is
    no size in the name we assume the model is capable -- degrading a good
    model's output on a bad guess is worse than letting a small one try and
    fall back.
    """
    m = re.search(r"[:\-_ ](\d+(?:\.\d+)?)\s*b\b", (model or "").lower())
    if not m:
        return False
    try:
        return float(m.group(1)) < SMALL_MODEL_B
    except ValueError:
        return False


def _validate_table(raw: dict | None, by_n: dict[int, dict]) -> dict | None:
    """Accept a model-proposed table only if it is structurally sound AND
    every row is backed by a real source.

    A comparison table is the most authoritative-looking thing a paper can
    contain, which is exactly why it gets the strictest validation in this
    file. A ragged table, or one row citing a source that does not exist, is
    dropped whole rather than rendered with a hole in it -- half a table
    invites the reader to trust the half that is left.
    """
    if not isinstance(raw, dict):
        return None
    cols = [str(c).strip() for c in (raw.get("columns") or []) if str(c).strip()]
    rows = raw.get("rows") or []
    row_sources = raw.get("row_sources") or []
    if len(cols) < 2 or len(rows) < 2:
        return None

    kept_rows, kept_sources = [], []
    for i, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        # Ragged rows are a model error, not something to paper over by padding.
        if len(row) != len(cols):
            continue
        n = row_sources[i] if i < len(row_sources) else None
        if n not in by_n:
            continue
        kept_rows.append([str(c).strip() for c in row])
        kept_sources.append(n)
        by_n[n]["used"] = True

    if len(kept_rows) < 2:
        return None
    return {
        "caption": (raw.get("caption") or "").strip()[:200],
        "columns": cols,
        "rows": kept_rows,
        "row_sources": kept_sources,
        "citations": sorted(set(kept_sources)),
    }


def _outline_from_sources(sources: list[dict], question: str) -> list[str]:
    """Recover a section outline when the approved sub-questions are not to
    hand (the post-stop "write from what we have" path).

    Every source records which lane found it, so the lanes -- and therefore
    the shape of the investigation -- can be reconstructed even though their
    wording is lost. Falls back to a single section when there is no grouping
    to recover, which is still a valid literature review, just a short one.
    """
    lanes = sorted({s.get("sub") for s in sources if s.get("sub") is not None})
    if not lanes:
        return [question]
    return [f"Findings from line of inquiry {i + 1}" for i in range(len(lanes))]


# Rough words-per-page for a double-spaced 12pt Times page with 1in margins.
# This is the standard figure every university style guide quotes, and it is
# what the exporter actually produces, so a page cap converted with it lands
# close in the real file rather than being a number the app made up.
WORDS_PER_PAGE = 275


def words_for_pages(pages: int) -> int:
    """Page cap -> word target. Reserves one page for the reference list,
    because a bibliography is part of what fills the page count the user
    asked for and pretending otherwise would overshoot every time."""
    return max(WORDS_PER_PAGE, (max(1, pages) - 1) * WORDS_PER_PAGE)


def _word_budget(total: int, n_themes: int) -> dict[str, int]:
    """Split a whole-paper word target across the sections.

    The weights are not an even split on purpose. A literature review is
    mostly its thematic sections; the introduction and conclusion are framing
    and are meant to be short. An even split produces a conclusion the length
    of a findings section, which is the most obvious sign of a paper written
    to a word count rather than to a point.

    A total of 0 means "no target" and disables the instruction entirely --
    the model is then left alone, which is the previous behaviour and stays
    the default.
    """
    if total <= 0:
        return {"intro": 0, "theme": 0, "discussion": 0, "conclusion": 0}
    themes = max(1, n_themes)
    # intro 12%, conclusion 8%, discussion 20%, the rest shared by the themes.
    theme_share = max(0.0, 1.0 - 0.12 - 0.08 - 0.20)
    return {
        "intro": max(80, round(total * 0.12)),
        "conclusion": max(60, round(total * 0.08)),
        "discussion": max(100, round(total * 0.20)),
        "theme": max(100, round(total * theme_share / themes)),
    }


def _lead_with(first: list[dict], rest: list[dict]) -> list[dict]:
    """`first` at the front, then everything in `rest` not already there.

    Keyed on the source `n` rather than on dict equality: two source records
    can differ by a mutable field like `used` and still be the same source, and
    `dict in list` would then keep both.
    """
    seen = {s["n"] for s in first}
    return [*first, *(s for s in rest if s["n"] not in seen)]


def _short_heading(s: str, max_words: int = 6) -> str:
    """A section heading, not a sentence.

    Sub-questions are written as full statements of what to find out, which is
    right for planning and wrong for a heading: "Differences in attention span
    and focus maintenance between ADHD and neurotypical individuals" is a line
    of prose sitting where a two-word topic belongs, and a paper whose headings
    are sentences does not read as a paper.

    The rule is deliberately blunt and deterministic -- drop the leading
    scaffolding phrases that turn a topic into a question, cut at the first
    joint ("between", "in relation to"), then cap the length. No model call:
    this runs on the fallback path, which is reached precisely when the model
    is already failing.
    """
    text = (s or "").strip().rstrip("?.")
    if not text:
        return "Untitled section"

    # Leading scaffolding. "Differences in X" is about X.
    text = re.sub(
        r"^(?:the\s+)?(?:differences?|comparisons?|relationships?|associations?|effects?|"
        r"impacts?|influence|role|extent|degree|ways?|evidence|research|studies|literature)\s+"
        r"(?:in|of|on|between|among|for|to|regarding|concerning|about)\s+",
        "", text, flags=re.I,
    )
    # "How does caffeine affect sleep" -> "caffeine affect sleep". The
    # auxiliary has to go with the question word; leaving it behind produces
    # "Does caffeine affect sleep", which is still a question.
    # NOTE the alternation order and the \b. Python's regex alternation is
    # first-match-wins, not longest-match-wins, so listing `do` before `does`
    # made "How does caffeine..." match "How do" and leave behind "es
    # caffeine...". Longest first, and a word boundary so it cannot happen
    # again if someone adds a prefix later.
    text = re.sub(
        r"^(?:how|what|why|when|whether|which)\s+"
        r"(?:does|did|do|were|was|is|are|could|should|would|can|will|have|has|had)?\b\s*",
        "", text, flags=re.I,
    )

    # Cut at the first joint that introduces a second clause.
    text = re.split(
        r"\s+(?:between|among|compared\s+with|compared\s+to|in\s+relation\s+to|"
        r"with\s+respect\s+to|versus|vs\.?)\s+",
        text, maxsplit=1, flags=re.I,
    )[0]

    words = text.split()
    if len(words) > max_words:
        words = words[:max_words]
    out = " ".join(words).strip(" ,;:-")
    if not out:
        out = (s or "").strip()[:60]
    return out[:1].upper() + out[1:]


def _title_case(s: str) -> str:
    """Fallback heading from a sub-question. Deliberately gentle: capitalise
    the first letter and drop a trailing question mark, rather than title-casing
    every word (which mangles acronyms and proper nouns)."""
    s = (s or "").strip().rstrip("?")
    return s[:1].upper() + s[1:] if s else "Untitled section"


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
