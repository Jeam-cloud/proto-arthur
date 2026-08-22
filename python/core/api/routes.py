"""All API routes.

One file instead of ten: each handler is 3–15 lines because logic lives in
services — the handlers only translate HTTP <-> service calls. Splitting
15 trivial handlers across ten files hides more than it organizes at this
size; if a section grows past ~10 routes, promote it to its own module then.

THE CHAT STREAM (the one non-trivial handler) uses a queue bridge:
chat_service runs as a task and `emit`s events into an asyncio.Queue; the SSE
generator drains the queue to the wire. WHY not emit directly from the
service: sse-starlette needs an async generator, while the service wants a
callback — the queue decouples them, and client disconnects (user hit Stop)
cancel the generator, whose `finally` cancels the service task, which
resolves any pending approval to "denied". Nothing leaks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, UploadFile
from sse_starlette.sse import EventSourceResponse

from core import attachments as attachments_mod
from core import events
from core import model_kind
from core.api.auth import require_auth
from core.api.schemas import (
    ApprovalDecision, ArchiveRequest, ChatRequest, ConversationModelRequest, MemoryCreate,
    MemoryUpdate, PersonaBody,
    PullRequest, RenameRequest, ResearchExportRequest, ResearchFindSourcesRequest,
    ResearchPlanRequest, ResearchRunRequest, ResearchSynthesizeRequest, SecretBody, SettingsPatch,
    AttachPathsRequest, ChangesRequest, HoldingBody, HoldingPatch, NewConversation,
    UndoRequest, WatchlistRequest,
    WorkspaceRequest,
)
from core.code_apply import apply_changeset
from core import portfolio_io
from core.holdings import value_holdings
from research import citations as research_citations
from research import engine as research_engine
from research import export as research_export
from core.deps import AppState
from core.errors import ArthurError, BadRequestError, NotFoundError, VoiceError
from core.hardware import detect as detect_hardware

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])
public = APIRouter()  # /health only — Electron polls it before the token dance


def state(request: Request) -> AppState:
    return request.app.state.arthur


# ---------- health & system ----------

@public.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": "arthur"}


@router.get("/system/status")
async def system_status(request: Request) -> dict:
    s = state(request)
    ollama_up = await s.llm.is_up()
    models = []
    if ollama_up:
        try:
            models = await s.llm.list_models()
            # Every consumer of this list -- the model menu, the hub's Installed
            # tab, the sidebar footer -- needs to distinguish a model running
            # HERE from one running on Ollama's servers. Tagging once, at the
            # source, means no caller has to remember to check the name.
            for m in models:
                m["cloud"] = model_kind.is_cloud_model(m.get("name", ""))
        except ArthurError:
            ollama_up = False
    return {
        "ollama_up": ollama_up,
        "models": models,
        "docker_up": await s.sandbox.is_available(),
        "scanner_backend": s.gateway.backend_name,
        "memory_available": s.memory.available,
        "email_configured": await s.email_router.is_configured(),
        "secrets": s.vault.status(),
        "default_model": await s.db.get_setting("default_model", ""),
        "onboarded": bool(await s.db.get_setting("onboarded", False)),
        "workspace_root": await s.db.get_setting("workspace_root", None),
        # Optional parsers that are absent. Reported HERE, once, rather than
        # discovered one attachment at a time: a user who drops six PDFs into a
        # build without pypdf otherwise gets six identical errors that look like
        # a problem with their files.
        "missing_parsers": attachments_mod.missing_parsers(),
    }


@router.get("/system/hardware")
async def hardware() -> dict:
    return await asyncio.to_thread(detect_hardware)


@router.post("/system/onboarded")
async def mark_onboarded(request: Request) -> dict:
    await state(request).db.set_setting("onboarded", True)
    return {"ok": True}


# ---------- models ----------

@router.get("/models")
async def list_models(request: Request) -> list[dict]:
    return await state(request).llm.list_models()


@router.get("/models/recommendations")
async def model_recommendations(request: Request) -> dict:
    """Per-mode model recommendations ranked against this machine — drives the
    composer's model menu. Degrades gracefully when Ollama is down (installed
    flags simply read false; the menu can still show what WOULD fit)."""
    from core.hardware import detect as detect_hardware_full
    from core.model_recs import recommendations

    s = state(request)
    hw = await asyncio.to_thread(detect_hardware_full)
    gpu = hw.get("gpu")
    budget = gpu["vram_gb"] if gpu else max(hw["ram_gb"] - 4, 2)
    try:
        installed = [m["name"] for m in await s.llm.list_models()]
        ollama_up = True
    except ArthurError:
        installed, ollama_up = [], False
    return {
        "budget_gb": round(budget, 1),
        "ollama_up": ollama_up,
        "modes": recommendations(installed, budget),
    }


@router.get("/models/catalog")
async def model_catalog(request: Request, q: str = "", type: str = "all") -> dict:
    """Model hub's search+score table -- same budget math as
    /models/recommendations (kept identical on purpose, so a model's score
    here matches its fit dot in the composer), just applied to the full
    searchable catalog instead of the top 2-3 picks per mode.

    Also returns `hardware`: the raw detected specs the hub renders as chips,
    so the page needs one request instead of two."""
    from core.hardware import detect as detect_hardware_full
    from core.model_recs import catalog_search

    s = state(request)
    hw = await asyncio.to_thread(detect_hardware_full)
    gpu = hw.get("gpu")
    budget = gpu["vram_gb"] if gpu else max(hw["ram_gb"] - 4, 2)
    try:
        installed = [m["name"] for m in await s.llm.list_models()]
        ollama_up = True
    except ArthurError:
        installed, ollama_up = [], False
    return {
        "budget_gb": round(budget, 1),
        "ollama_up": ollama_up,
        "hardware": {
            "gpu": gpu["name"] if gpu else None,
            "vram_gb": gpu["vram_gb"] if gpu else None,
            "ram_gb": hw["ram_gb"],
            "cpu_count": hw["cpu_count"],
        },
        "results": catalog_search(installed, budget, q, type),
    }


@router.post("/models/pull")
async def pull_model(request: Request, body: PullRequest) -> EventSourceResponse:
    s = state(request)

    async def gen():
        try:
            async for progress in s.llm.pull(body.model):
                yield {"event": "progress", "data": _json(progress)}
            yield {"event": "done", "data": "{}"}
        except ArthurError as e:
            yield {"event": "error", "data": _json({"code": e.code, "message": e.message})}

    return EventSourceResponse(gen())


@router.delete("/models/{name:path}")
async def delete_model(request: Request, name: str) -> dict:
    """Uninstalls a model and frees its disk space. If it was the global
    default or assigned to a mode, we clear those settings too instead of
    leaving Arthur pointed at a model that no longer exists — that would
    otherwise surface as a confusing 404 the next time someone chats."""
    s = state(request)
    await s.llm.delete(name)

    cleared: list[str] = []
    default_model = await s.db.get_setting("default_model", "")
    if default_model == name:
        await s.db.set_setting("default_model", "")
        cleared.append("default")

    mode_models = await s.db.get_setting("mode_models", {}) or {}
    freed_modes = [mode for mode, m in mode_models.items() if m == name]
    if freed_modes:
        for mode in freed_modes:
            del mode_models[mode]
        await s.db.set_setting("mode_models", mode_models)
        cleared.extend(freed_modes)

    return {"ok": True, "cleared": cleared}


# ---------- chat ----------

@router.post("/chat/stream")
async def chat_stream(request: Request, body: ChatRequest) -> EventSourceResponse:
    s = state(request)

    # Is there anything to send? Checked FIRST, before model resolution.
    #
    # `ChatRequest.message` no longer requires text, because a message can be
    # carried entirely by its attachments. That check has to live here rather
    # than in the schema, since only the database knows what is staged.
    #
    # Ordering matters: pressing send on an empty composer with no model
    # configured used to answer "No model selected", which is true but not the
    # problem the user has. Say the nearer thing first.
    staged = await s.db.fetch_all(
        "SELECT * FROM attachments WHERE conversation_id=? AND message_id IS NULL ORDER BY created_at",
        (body.conversation_id,),
    )
    if not body.message.strip() and not staged:
        raise ArthurError("Type a message or attach a file first.", detail={})

    # Model resolution, most specific wins:
    #   1. explicit per-message override (the chat-header picker)
    #   2. the mode's assigned model (Settings → Models, e.g. finance -> qwen3:14b)
    #   3. the global default
    # Swapping is instant because the model is just a request parameter —
    # Ollama loads it on first use and keep_alive keeps recent ones warm.
    model = body.model
    if not model:
        mode_models = await s.db.get_setting("mode_models", {}) or {}
        model = mode_models.get(body.mode.value, "")
    if not model:
        model = await s.db.get_setting("default_model", "")
    if not model and body.provider == "local":
        raise ArthurError("No model selected. Pick one in the model menu.", detail={})

    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    # `staged` was read at the top of this function, before model resolution,
    # and is used unchanged here: full rows rather than the wire shape, because
    # the prompt builder needs `extracted_text` and `stored_path`, which
    # to_dict() deliberately withholds from the UI. They are claimed for the
    # message inside stream_reply, so a file dropped in the composer becomes
    # part of the transcript exactly once.
    #
    # Whether the model can see. Unknown counts as CAN -- refusing to send an
    # image because Ollama did not answer would be worse than sending it.
    caps = await s.llm.capabilities(model)
    can_see = (not caps) or ("vision" in caps)

    async def run() -> None:
        try:
            await s.chat.stream_reply(
                conversation_id=body.conversation_id,
                user_text=body.message,
                mode=body.mode,
                model=model,
                emit=emit,
                provider=body.provider,
                attachments=staged,
                vision=can_see,
                workspace_root=await _conversation_workspace(s, body.conversation_id),
                scanner_mode=await s.db.get_setting("scanner_mode", "standard"),
            )
        except ArthurError as e:
            await emit(events.ERROR, {"code": e.code, "message": e.message, **e.detail})
        except asyncio.CancelledError:
            # NOT an error, and NOT caught by `except Exception` — CancelledError
            # is a BaseException. This is the path a reply takes when the client
            # goes away mid-stream: the Stop button, a closed window, or the dev
            # server hot-reloading the renderer. It reaches the UI as a reply
            # that simply stops with no explanation, which is indistinguishable
            # from a crash unless it is logged HERE.
            log.info("chat stream cancelled (client disconnected or Stop pressed)")
            raise
        except Exception:
            log.exception("chat stream crashed")
            await emit(events.ERROR, {"code": "internal_error",
                                      "message": "Something went wrong generating this reply."})
        finally:
            await queue.put(None)  # sentinel: stream is finished

    task = asyncio.create_task(run())

    async def gen():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield {"event": event, "data": _json(data)}
        finally:
            task.cancel()  # client gone (Stop button / closed window) -> stop generating

    return EventSourceResponse(gen())


# ---------- research ----------

async def _research_model(s: AppState, requested: str) -> str:
    """Same resolution order as chat: explicit > mode-assigned > global default.
    Research deserves the mode-assigned model in particular -- people point this
    mode at their biggest reasoning model on purpose."""
    if requested:
        return requested
    mode_models = await s.db.get_setting("mode_models", {}) or {}
    return mode_models.get("research", "") or await s.db.get_setting("default_model", "")


async def _conversation_workspace(s: AppState, cid: str | None) -> str | None:
    """Which folder THIS conversation may touch.

    Resolution order, and the reasoning for it:

      1. The conversation's own `workspace_root`. Once a chat is bound to a
         folder, that binding wins forever -- a global setting changed later
         must never silently widen what an existing conversation can reach.
      2. `workspace_root` in settings, which now means "the last folder
         chosen" rather than "the one folder". A NEW chat inherits it, so
         per-conversation scoping does not turn into re-picking a folder every
         time you start a conversation.
      3. None, which every file tool treats as "no access" rather than "all
         access" (see _safe_path in tools/coding.py).

    Note the asymmetry in (1): inheritance happens once, at first use. After
    that the conversation owns its root.
    """
    if cid:
        try:
            row = await s.conversations.get(cid)
        except NotFoundError:
            row = None
        if row and row.get("workspace_root"):
            return row["workspace_root"]
    return await s.db.get_setting("workspace_root", None)


def _target_words(body: Any) -> int:
    """One length target from two controls the user may set either of.

    They are not combined: the SMALLER wins when both are set. If someone asks
    for 3000 words but caps the paper at 4 pages, the cap is the real
    constraint and writing 3000 words would break the promise the cap made.
    """
    words = getattr(body, "target_words", 0) or 0
    pages = getattr(body, "max_pages", 0) or 0
    from_pages = research_engine.words_for_pages(pages) if pages else 0
    if words and from_pages:
        return min(words, from_pages)
    return words or from_pages


@router.post("/research/plan")
async def research_plan(request: Request, body: ResearchPlanRequest) -> dict:
    """Decompose only. Deliberately NOT part of the run stream: the user edits
    this list before anything is searched, so it has to come back as a plain
    response they can sit on for as long as they like."""
    s = state(request)
    model = await _research_model(s, body.model)
    if not model:
        raise ArthurError("No model selected. Pick one in the model menu.", detail={})
    subs = await s.research.plan(body.question, body.depth, model)
    # Warn BEFORE the run, not after. A four-minute investigation that ends in
    # a thin paper because the model was too small is the worst possible time
    # to find that out, and the plan screen is the last moment the user can
    # still change the model for free.
    warning = ""
    if research_engine.model_is_small(model):
        warning = (
            f"{model} is a small model. Arthur will use a simplified writing mode so it can "
            "cope, but the paper will be shorter and less connected than a 8B+ model produces. "
            "Switching model in the composer, or using Quick depth, both help."
        )
    return {"sub_questions": subs, "depth": body.depth, "model": model, "warning": warning}


@router.post("/research/run")
async def research_run(request: Request, body: ResearchRunRequest) -> EventSourceResponse:
    """Same queue-bridge shape as /chat/stream (see this module's docstring):
    the engine emits into a queue, the generator drains it to the wire, and a
    client disconnect cancels the task -- which is exactly what the Stop button
    does. Stopping keeps whatever the UI already received, because every event
    was a complete object rather than a fragment."""
    s = state(request)
    model = await _research_model(s, body.model)
    if not model:
        raise ArthurError("No model selected. Pick one in the model menu.", detail={})

    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def run() -> None:
        try:
            await s.research.run(
                question=body.question,
                subs=body.sub_questions,
                depth=body.depth,
                source_kinds=body.sources,
                model=model,
                emit=emit,
                include_domains=body.include_domains,
                exclude_domains=body.exclude_domains,
                target_words=_target_words(body),
            )
        except ArthurError as e:
            await emit(events.ERROR, {"code": e.code, "message": e.message, **e.detail})
        except Exception:
            log.exception("research run crashed")
            await emit(events.ERROR, {"code": "internal_error",
                                      "message": "The investigation stopped unexpectedly. "
                                                 "Everything gathered so far has been kept."})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())

    async def gen():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield {"event": event, "data": _json(data)}
        finally:
            task.cancel()

    return EventSourceResponse(gen())


@router.post("/research/synthesize")
async def research_synthesize(request: Request, body: ResearchSynthesizeRequest) -> EventSourceResponse:
    """Write the report from sources the browser already has -- no searching.

    Exists for exactly the situation that motivated it: search finished (or
    was stopped) but the report never got written, and forcing a full re-run
    to get a report out of sources already sitting in the evidence panel
    would throw away real work. Same queue-bridge/cancel-on-disconnect shape
    as /research/run."""
    s = state(request)
    model = await _research_model(s, body.model)
    if not model:
        raise ArthurError("No model selected. Pick one in the model menu.", detail={})

    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def run() -> None:
        try:
            await s.research.synthesize_only(
                question=body.question, sources=body.sources, model=model, emit=emit,
                subs=body.sub_questions, target_words=_target_words(body),
            )
        except ArthurError as e:
            await emit(events.ERROR, {"code": e.code, "message": e.message, **e.detail})
        except Exception:
            log.exception("research synthesize crashed")
            await emit(events.ERROR, {"code": "internal_error",
                                      "message": "Writing the report failed. Your sources are still here."})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())

    async def gen():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield {"event": event, "data": _json(data)}
        finally:
            task.cancel()

    return EventSourceResponse(gen())


@router.post("/research/find-sources")
async def research_find_sources(request: Request, body: ResearchFindSourcesRequest) -> EventSourceResponse:
    """Search for something the user typed and stream back only what is NEW.

    No model involved: this is search + read + extract. It deliberately does
    not rewrite the paper (see ResearchEngine.find_more)."""
    s = state(request)
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def run() -> None:
        try:
            await s.research.find_more(
                query=body.query, existing=body.sources, source_kinds=body.kinds, emit=emit,
                include_domains=body.include_domains, exclude_domains=body.exclude_domains,
            )
        except ArthurError as e:
            await emit(events.ERROR, {"code": e.code, "message": e.message, **e.detail})
        except Exception:
            log.exception("find-sources crashed")
            await emit(events.ERROR, {"code": "internal_error",
                                      "message": "That search failed. Your existing sources are untouched."})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())

    async def gen():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield {"event": event, "data": _json(data)}
        finally:
            task.cancel()

    return EventSourceResponse(gen())


@router.post("/research/export")
async def export_research_paper(request: Request, body: ResearchExportRequest) -> Response:
    """Render the finished paper to .docx or .pdf and return the bytes.

    NAMED NOT-research_export ON PURPOSE. It used to be, which silently shadowed
    `from research import export as research_export` at module level — so by the
    time this function ran, every `research_export.to_pdf` inside it resolved to
    THIS FUNCTION and raised AttributeError. Export was broken for every user, in
    both formats, and it surfaced only as "Failed to fetch" in the browser. Ruff
    had been reporting it as F811 the whole time.

    Returns a file rather than writing to disk so the browser can hand it to
    the OS save dialog -- a research paper belongs wherever the person keeps
    documents, not in Arthur's data directory."""
    s = state(request)

    prebuilt = None
    if body.style == "custom" and body.custom_style.strip():
        # The only citation path that touches a model. Scoped to reference
        # metadata only -- see research/citations.py.
        model = await _research_model(s, body.model)
        if model:
            cited = {
                c for sec in body.paper.get("sections", [])
                for p in sec.get("paragraphs", []) for c in (p.get("citations") or [])
            }
            used = [src for src in body.sources if int(src.get("n") or 0) in cited]
            prebuilt = await research_citations.custom_reference_list(
                s.llm, model, used, body.custom_style.strip()
            )

    try:
        if body.fmt == "pdf":
            data = await asyncio.to_thread(
                research_export.to_pdf, body.paper, body.sources, body.style, prebuilt
            )
            media = "application/pdf"
        else:
            data = await asyncio.to_thread(
                research_export.to_docx, body.paper, body.sources, body.style, prebuilt
            )
            media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    except ImportError as e:
        # python-docx / reportlab missing: say which, instead of a 500.
        raise ArthurError(
            f"Export needs a library that isn't installed: {e}. "
            "Run pip install -r requirements.txt in the python folder.", detail={},
        ) from e

    filename = research_export.filename_for(body.paper, body.fmt)
    return Response(
        content=data, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- conversations ----------

@router.get("/conversations")
async def list_conversations(request: Request, archived: bool = False) -> list[dict]:
    return await state(request).conversations.list_all(archived=archived)


@router.post("/conversations")
async def create_conversation(request: Request, body: NewConversation | None = None) -> dict:
    """Start a chat in a given mode, optionally bound to a folder.

    The folder is remembered globally too, so it heads the recents list next
    time — but the conversation's own binding is what governs its access.
    """
    s = state(request)
    body = body or NewConversation()
    conv = await s.conversations.create(mode=body.mode, workspace_root=body.workspace_root)
    if body.workspace_root:
        await _remember_workspace(s, body.workspace_root)
    return conv


RECENT_ROOTS_KEY = "recent_workspace_roots"
MAX_RECENT_ROOTS = 8


async def _remember_workspace(s: AppState, root: str) -> None:
    """Most-recently-used list of project folders.

    WHY: picking a folder used to mean the OS dialog, every time, for every
    chat — which is precisely the friction that made everyone stay on one
    folder and gave up multi-project work. Switching between two projects
    should be one click.

    Move-to-front, de-duplicated, capped. Paths are NOT validated here: a
    folder on an unplugged drive should stay in the list (see WorkspaceRequest
    for the same reasoning), and containment is enforced at use.
    """
    recents = await s.db.get_setting(RECENT_ROOTS_KEY, []) or []
    recents = [r for r in recents if r != root][:MAX_RECENT_ROOTS - 1]
    await s.db.set_setting(RECENT_ROOTS_KEY, [root, *recents])
    await s.db.set_setting("workspace_root", root)


# ---------- finance ----------

WATCHLIST_KEY = "finance_watchlist"
# Symbol -> company name. Written once per symbol; see get_watchlist_data.
NAMES_KEY = "finance_symbol_names"


@router.get("/finance/watchlist")
async def get_watchlist(request: Request) -> dict:
    """The symbols only. Cheap, no upstream call, safe to hit on every mount."""
    symbols = await state(request).db.get_setting(WATCHLIST_KEY, []) or []
    return {"symbols": symbols}


@router.put("/finance/watchlist")
async def put_watchlist(request: Request, body: WatchlistRequest) -> dict:
    """Replace the list.

    APP-WIDE, not per-conversation. A watchlist is a standing view of what the
    user follows — unlike mode, folder and model, which belong to a single
    chat, it should be the same list whichever Finance conversation is open.
    """
    await state(request).db.set_setting(WATCHLIST_KEY, body.symbols)
    return {"ok": True, "symbols": body.symbols}


@router.get("/finance/watchlist/data")
async def get_watchlist_data(request: Request) -> dict:
    """Quotes and sparklines for the saved symbols, in one upstream round trip.

    `fetched_at` is returned so the UI can print how old the numbers are. It is
    NOT optional decoration: Yahoo's feed is ~15 minutes delayed and this
    response may additionally be served from a 15-minute cache, so a price can
    be half an hour old while looking current. Every surface that shows one has
    to be able to say when it was taken.

    An empty watchlist returns 200 with no rows rather than an error — nothing
    has gone wrong, there is simply nothing on it yet.
    """
    s = state(request)
    symbols = await s.db.get_setting(WATCHLIST_KEY, []) or []
    if not symbols:
        return {"rows": {}, "symbols": [], "fetched_at": time.time(), "ok": True}

    # Company names are cached FOREVER, deliberately. Looking one up is
    # yfinance's `.info` call — the slow, rate-limited one — and a company's
    # name does not change. Sending the ones we already know means the
    # expensive call runs once per symbol for the life of the install instead
    # of on every refresh, which is what made the first version of this route
    # time out and trip the circuit breaker.
    known = await s.db.get_setting(NAMES_KEY, {}) or {}
    result = await s.watchlist.fetch(symbols, names=known)
    if result.ok:
        rows = json.loads(result.content)
        learned = {
            sym: row["name"] for sym, row in rows.items()
            if isinstance(row, dict) and row.get("name") and row["name"] != sym
        }
        if any(known.get(k) != v for k, v in learned.items()):
            await s.db.set_setting(NAMES_KEY, {**known, **learned})
    if not result.ok:
        # Reported as a 200 with ok=false rather than a 5xx: the failure is
        # upstream and expected (rate limits, Yahoo changing shape, the circuit
        # breaker holding off), and the panel needs to render a retry state, not
        # an exception.
        return {"rows": {}, "symbols": symbols, "fetched_at": time.time(),
                "ok": False, "error": result.content}
    return {"rows": rows, "symbols": symbols,
            "fetched_at": time.time(), "ok": True}


@router.get("/finance/portfolio")
async def get_portfolio(request: Request) -> dict:
    """Holdings, valued.

    TWO SPEEDS IN ONE RESPONSE, and the split matters. The holdings themselves
    are local and instant; the prices are a container call against a delayed,
    rate-limited feed. So the rows always come back — with quantity and cost
    basis, which are facts the user typed — and the market columns are filled
    in where a quote arrived.

    That means an upstream outage costs you the VALUATION, not the portfolio.
    A screen that says "we couldn't load your holdings" when it merely couldn't
    price them is telling the user their data is gone.
    """
    s = state(request)
    holdings = await s.holdings.list_all()
    if not holdings:
        return {"holdings": [], "totals": {}, "fetched_at": time.time(), "ok": True}

    symbols = await s.holdings.symbols()
    known = await s.db.get_setting(NAMES_KEY, {}) or {}
    result = await s.watchlist.fetch(symbols, names=known)

    quotes = {}
    priced_ok = result.ok
    if priced_ok:
        quotes = json.loads(result.content)
        learned = {sym: r["name"] for sym, r in quotes.items()
                   if isinstance(r, dict) and r.get("name") and r["name"] != sym}
        if any(known.get(k) != v for k, v in learned.items()):
            await s.db.set_setting(NAMES_KEY, {**known, **learned})

    valued = value_holdings(holdings, quotes)
    return {
        **valued,
        "fetched_at": time.time(),
        "ok": priced_ok,
        "error": None if priced_ok else result.content,
    }


@router.get("/finance/resolve/{symbol}")
async def resolve_symbol(request: Request, symbol: str) -> dict:
    """What instrument is this ticker, actually?

    EXISTS BECAUSE TICKER COLLISIONS ARE INVISIBLE UNTIL THE MATH IS WRONG.
    A person who holds bitcoin types "BTC" and gets the Grayscale Bitcoin Mini
    Trust; "XRP" is the Bitwise XRP ETF. Both are real securities that quote
    fine, so nothing errors — the mistake only surfaces later as an impossible
    percentage, by which time it looks like Arthur miscalculated.

    Showing the resolved NAME and price while they are still typing the cost
    basis is the cheapest possible intervention: "XRP — Bitwise XRP ETF,
    $11.17" is either what they meant or obviously not.

    Deliberately the cheap fast_info path, not the full .info detail call —
    this fires on every symbol blur and only needs a name and a price.
    """
    sym = symbol.strip().upper()
    known = await state(request).db.get_setting(NAMES_KEY, {}) or {}
    result = await state(request).watchlist.fetch([sym], names=known)
    if not result.ok:
        # Not knowing is not an error here. The field stays usable and the form
        # simply cannot offer its confirmation.
        return {"ok": False, "symbol": sym, "error": result.content}
    row = (json.loads(result.content) or {}).get(sym) or {}
    if row.get("failed") or not row.get("price"):
        return {"ok": False, "symbol": sym, "unknown": True}
    return {"ok": True, "symbol": sym, "name": row.get("name") or sym,
            "price": row.get("price"), "currency": row.get("currency")}


@router.post("/finance/portfolio")
async def add_holding(request: Request, body: HoldingBody) -> dict:
    return await state(request).holdings.add(
        body.symbol, body.quantity, body.cost_basis, body.purchase_date,
        body.cost_currency,
    )


@router.patch("/finance/portfolio/{hid}")
async def patch_holding(request: Request, hid: str, body: HoldingPatch) -> dict:
    ok = await state(request).holdings.update(
        hid, symbol=body.symbol, quantity=body.quantity, cost_basis=body.cost_basis,
        purchase_date=body.purchase_date, cost_currency=body.cost_currency,
    )
    if not ok:
        raise BadRequestError("nothing to update")
    return {"ok": True}


@router.delete("/finance/portfolio/{hid}")
async def delete_holding(request: Request, hid: str) -> dict:
    # No soft delete and no undo window: unlike a conversation, this is
    # hand-entered data that cannot be re-fetched, so the UI confirms first
    # rather than offering to reverse afterwards.
    await state(request).holdings.remove(hid)
    return {"ok": True}


@router.get("/finance/portfolio/export")
async def export_portfolio(request: Request) -> Response:
    """The whole portfolio as a CSV download.

    Valued, not raw: the file carries the same figures that were on screen when
    the user clicked Export. The valuation columns are commentary for whoever
    opens the file — import ignores them, because a price from last Tuesday is
    not a fact about a holding.

    Best-effort pricing. If the quote fetch fails the file is still produced
    with the hand-entered columns intact, because those are the ones that
    cannot be recovered from anywhere else and are the entire point of a backup.
    """
    s = state(request)
    holdings = await s.holdings.list_all()
    quotes: dict = {}
    if holdings:
        known = await s.db.get_setting(NAMES_KEY, {}) or {}
        result = await s.watchlist.fetch(await s.holdings.symbols(), names=known)
        if result.ok:
            quotes = json.loads(result.content)
    rows = value_holdings(holdings, quotes)["holdings"]
    return Response(
        content=portfolio_io.export_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{portfolio_io.export_filename()}"'},
    )


@router.post("/finance/portfolio/import/preview")
async def preview_import(request: Request, file: UploadFile) -> dict:
    """Parse an uploaded CSV and report what it WOULD do. Writes nothing.

    Split from the apply step on purpose. Import is the one operation here that
    can damage data the user cannot get back, so it is always two steps: see
    the rows and the complaints, then decide.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on Windows still writes cp1252 by default in some locales.
        text = raw.decode("cp1252", errors="replace")
    parsed = portfolio_io.parse_csv(text)
    existing = await state(request).holdings.list_all()
    return {
        **parsed,
        "count": len(parsed["rows"]),
        "existing": len(existing),
    }


@router.post("/finance/portfolio/import")
async def apply_import(request: Request, file: UploadFile, replace: bool = False) -> dict:
    """Commit a parsed CSV.

    `replace=false` (the default) APPENDS. Replace is opt-in and irreversible,
    so the caller has to ask for it explicitly and the UI confirms first —
    defaulting to replace would let one misread click delete a portfolio that
    exists nowhere else.

    Rows that failed to parse are skipped and reported. A single typo must not
    cost the user the other twenty rows.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    parsed = portfolio_io.parse_csv(text)
    if not parsed["rows"]:
        raise BadRequestError(
            "Nothing importable in that file.",
            detail={"errors": parsed["errors"][:10]},
        )

    s = state(request)
    removed = 0
    if replace:
        for h in await s.holdings.list_all():
            await s.holdings.remove(h["id"])
            removed += 1
    for r in parsed["rows"]:
        await s.holdings.add(r["symbol"], r["quantity"], r["cost_basis"],
                             r["purchase_date"], r["cost_currency"])
    return {"ok": True, "added": len(parsed["rows"]), "removed": removed,
            "skipped": len(parsed["errors"]), "errors": parsed["errors"][:10]}


@router.get("/finance/symbol/{symbol}")
async def get_symbol_detail(request: Request, symbol: str, period: str = "1mo") -> dict:
    """The symbol page: quote, history and company profile.

    Separate from the watchlist route because the cost model is different —
    this one pays for yfinance's `.info` call, which the watchlist deliberately
    avoids. See detail() in finance_query.py.
    """
    s = state(request)
    sym = symbol.strip().upper()
    if not (1 <= len(sym) <= 12 and sym.replace(".", "").replace("-", "").replace("=", "").isalnum()):
        raise BadRequestError(f"Not a ticker: {symbol!r}")

    known = await s.db.get_setting(NAMES_KEY, {}) or {}
    result = await s.watchlist.detail(sym, period=period, names=known)
    if not result.ok:
        return {"symbol": sym, "row": None, "fetched_at": time.time(),
                "ok": False, "error": result.content}

    rows = json.loads(result.content)
    row = rows.get(sym) or {}
    name = row.get("name")
    if name and name != sym and known.get(sym) != name:
        await s.db.set_setting(NAMES_KEY, {**known, sym: name})
    return {"symbol": sym, "row": row, "fetched_at": time.time(), "ok": True}


@router.get("/finance/symbol/{symbol}/news")
async def get_symbol_news(request: Request, symbol: str) -> dict:
    """Recent coverage, from the web search Arthur already has.

    DELIBERATELY NOT yfinance. Its news endpoint has a long-standing bug
    returning articles unrelated to the requested ticker, and a symbol page
    confidently showing a competitor's headline is worse than showing none.
    Tavily is already wired for quick_search, the query is ours to control, and
    the results carry a real source and date.

    THE COMPANY NAME GOES IN THE QUERY, not just the ticker: bare symbols are
    ambiguous words ("ALL", "IT", "ON", "KEY"), and searching them returns
    everything but the company.
    """
    s = state(request)
    sym = symbol.strip().upper()
    known = await s.db.get_setting(NAMES_KEY, {}) or {}
    name = known.get(sym) or sym
    query = f"{name} ({sym}) stock news" if name != sym else f"{sym} stock news"

    api_key = s.vault.get("tavily")
    if not api_key:
        # Not an error state on this page: the rest of it works, and the empty
        # state can say where to add a key.
        return {"symbol": sym, "items": [], "ok": True, "unconfigured": True}

    try:
        from tavily import TavilyClient

        res = await asyncio.to_thread(
            lambda: TavilyClient(api_key=api_key).search(
                query, max_results=5, topic="news",
            )
        )
    except Exception as e:
        # Coverage is supplementary. The page is fully usable without it, so a
        # search failure reports itself and changes nothing else.
        log.info("symbol news search failed for %s: %s", sym, e)
        return {"symbol": sym, "items": [], "ok": False, "error": str(e)[:200]}

    items = []
    for r in (res.get("results") or [])[:5]:
        url = r.get("url") or ""
        # The domain is what makes a source checkable at a glance — "reuters.com"
        # tells you more than a 90-character headline URL.
        domain = url.split("//")[-1].split("/")[0].removeprefix("www.")
        items.append({
            "title": r.get("title") or url,
            "url": url,
            "domain": domain,
            "published": r.get("published_date"),
        })
    return {"symbol": sym, "items": items, "ok": True}


@router.get("/workspace/recents")
async def workspace_recents(request: Request) -> dict:
    s = state(request)
    roots = await s.db.get_setting(RECENT_ROOTS_KEY, []) or []
    # `exists` per entry so the menu can grey out a folder that has moved
    # rather than offering it and failing on the next tool call.
    return {"recents": [{"root": r, "exists": Path(r).is_dir()} for r in roots]}


@router.get("/conversations/{cid}/messages")
async def conversation_messages(request: Request, cid: str) -> list[dict]:
    await state(request).conversations.get(cid)  # 404 for unknown ids
    return await state(request).conversations.messages(cid)


@router.patch("/conversations/{cid}")
async def rename_conversation(request: Request, cid: str, body: RenameRequest) -> dict:
    await state(request).conversations.rename(cid, body.title)
    return {"ok": True}


@router.put("/conversations/{cid}/model")
async def set_conversation_model(
    request: Request, cid: str, body: ConversationModelRequest,
) -> dict:
    """Pin this conversation to a model (or "" to follow Settings again).

    Separate from PATCH /conversations, which renames: the two are unrelated
    edits and folding them together would mean every rename had to carry a
    model and vice versa.
    """
    s = state(request)
    await s.conversations.get(cid)  # 404 for unknown ids, same as the others
    await s.conversations.set_model(cid, body.model)
    return {"ok": True}


@router.delete("/conversations/{cid}")
async def delete_conversation(request: Request, cid: str) -> dict:
    await state(request).conversations.delete(cid)
    return {"ok": True}


@router.post("/conversations/{cid}/archive")
async def archive_conversation(request: Request, cid: str, body: ArchiveRequest) -> dict:
    await state(request).conversations.set_archived(cid, body.archived)
    return {"ok": True}


@router.post("/conversations/{cid}/clone")
async def clone_conversation(request: Request, cid: str) -> dict:
    return await state(request).conversations.clone(cid)


@router.get("/conversations/{cid}/workspace")
async def get_conversation_workspace(request: Request, cid: str) -> dict:
    """What folder this chat is bound to, and whether it is actually there.

    `exists` is returned separately from `root` because they answer different
    questions. A remembered path pointing at an unplugged drive should still
    show as the chosen folder -- clearing it would lose the binding for a
    project the user has not abandoned -- but the UI has to be able to say so
    rather than presenting a dead path as working.
    """
    s = state(request)
    root = await _conversation_workspace(s, cid)
    row = await s.conversations.get(cid)
    return {
        "root": root,
        # True only when the conversation set it itself; False means it is
        # currently inheriting the last-used folder and has not been bound.
        "bound": bool(row.get("workspace_root")),
        "exists": bool(root) and Path(root).is_dir(),
    }


@router.put("/conversations/{cid}/workspace")
async def set_conversation_workspace(request: Request, cid: str, body: WorkspaceRequest) -> dict:
    s = state(request)
    await s.conversations.set_workspace(cid, body.root)
    # Also remembered globally, which seeds the recents menu and the folder a
    # brand-new conversation inherits.
    if body.root:
        await _remember_workspace(s, body.root)
    return {"root": body.root, "bound": bool(body.root)}


# ---------- code mode: pending changes ----------
#
# The review gate. Every file the agent "wrote" this session is sitting in a
# ChangeSet; these three routes are the only way it reaches disk, and the only
# way it goes away. Nothing here is reachable in other modes because nothing in
# other modes ever stages anything.


@router.get("/conversations/{cid}/changes")
async def list_changes(request: Request, cid: str, diffs: bool = True) -> dict:
    """Pending edits for this chat, newest state first.

    `diffs=false` exists for the header badge, which only needs the counts. A
    twelve-file diff is a lot of bytes to ship on a poll that renders "3 files".
    """
    s = state(request)
    cs = s.changesets.peek(cid)
    if cs is None:
        return {"changes": [], "files": 0, "additions": 0, "deletions": 0}
    return {"changes": cs.summary(include_diff=diffs), **cs.totals()}


@router.post("/conversations/{cid}/changes/apply")
async def apply_changes(request: Request, cid: str, body: ChangesRequest) -> dict:
    """Write staged edits to disk. THE one destructive route in Code mode.

    `paths=null` means all — the common case, one click after reading the diff.
    Passing a subset lets the user take three of the agent's five edits, which
    matters because partial agreement is the normal outcome of a code review.

    Conflicts (the file changed underneath us) are reported, not forced; see
    ChangeSet.apply.
    """
    s = state(request)
    cs = s.changesets.peek(cid)
    if cs is None or cs.is_empty():
        return {"applied": [], "conflicts": [], "failed": [], "remaining": 0}
    # Shared with the auto-apply path in chat_service so the undo snapshot and
    # the receipt cannot exist in one and not the other -- see core/code_apply.py.
    return await apply_changeset(
        cs, conversation_id=cid, conversations=s.conversations,
        undos=s.undos, audit=s.audit, paths=body.paths,
    )


@router.post("/conversations/{cid}/changes/discard")
async def discard_changes(request: Request, cid: str, body: ChangesRequest) -> dict:
    s = state(request)
    cs = s.changesets.peek(cid)
    if cs is None:
        return {"discarded": [], "remaining": 0}
    discarded = cs.discard(body.paths)
    return {"discarded": discarded, "remaining": len(cs.paths())}


# ---------- code mode: undo ----------
#
# The other half of writing files directly. With edits landing automatically,
# these two routes ARE the safety model -- what the Apply button used to be,
# moved to the far side of the write.


@router.get("/conversations/{cid}/undo")
async def list_undos(request: Request, cid: str) -> dict:
    """What can still be put back for this chat, most recent first.

    Scoped to the conversation because that is how the user thinks about it
    ("undo what this chat just did"), and because two chats can be editing two
    different projects — offering one chat's undo inside the other would apply
    a snapshot to a folder it was never taken from.
    """
    s = state(request)
    entries = s.undos.list(cid)
    return {"undos": entries, "latest": entries[0] if entries else None}


@router.post("/conversations/{cid}/undo")
async def undo_apply(request: Request, cid: str, body: UndoRequest) -> dict:
    """Restore the files an apply replaced.

    `id` omitted means the most recent apply in this chat, which is the button
    in the receipt. Files the user has edited since are SKIPPED, not
    overwritten: undo reverses Arthur's write, and a file that no longer matches
    what Arthur wrote holds someone else's work. An undo that destroys is not an
    undo.
    """
    s = state(request)
    target = body.id or (s.undos.latest(cid) or {}).get("id")
    if not target:
        return {"restored": [], "skipped": [], "failed": [],
                "error": "There is nothing to undo in this chat."}

    # Belongs-to check: an id is a client-supplied string, and a snapshot taken
    # in another conversation was taken against another folder.
    if not any(u["id"] == target for u in s.undos.list(cid)):
        return {"restored": [], "skipped": [], "failed": [],
                "error": "That change is no longer undoable."}

    result = s.undos.undo(target)
    await s.audit.record(
        "code.changes_undone", "info",
        conversation_id=cid,
        restored=", ".join(result["restored"]),
        skipped=", ".join(result["skipped"]),
    )

    if result["restored"]:
        n = len(result["restored"])
        text = (f"Put back {result['restored'][0]}." if n == 1
                else f"Put back {n} files.")
        if result["skipped"]:
            text += (f" {len(result['skipped'])} left alone — "
                     "you have edited them since.")
        result["receipt"] = {
            "id": await s.conversations.add_message(cid, "receipt", text),
            "role": "receipt",
            "content": text,
        }
    return result


# ---------- attachments ----------

@router.get("/models/{model:path}/capabilities")
async def model_capabilities(request: Request, model: str) -> dict:
    """What the model can do, so the UI can warn BEFORE a message is sent.

    `known` distinguishes "Ollama says this model cannot see" from "Ollama did
    not answer". The UI must not warn on the second: a warning about a
    limitation that may not exist teaches people to dismiss warnings, which
    costs more than the one it was trying to prevent.

    `{model:path}` because tags contain slashes for namespaced models
    (`hf.co/user/model:q4`), which a plain path parameter would truncate.
    """
    caps = await state(request).llm.capabilities(model)
    return {
        "model": model,
        "capabilities": sorted(caps),
        "known": bool(caps),
        "vision": "vision" in caps,
        "tools": "tools" in caps,
    }


@router.post("/conversations/{cid}/attachments")
async def upload_attachments(
    request: Request, cid: str, files: list[UploadFile] | None = None,
) -> dict:
    """Files dropped into the composer, uploaded as bytes.

    Partial success is a real outcome and is reported as one: a drop of six
    files where the fourth is a 4GB video should attach five and say why the
    sixth did not, rather than failing the whole gesture.
    """
    s = state(request)
    await s.conversations.get(cid)  # 404 for unknown ids
    added, errors = [], []
    for upload in files or []:
        try:
            added.append(await s.attachments.add_bytes(cid, upload.filename or "file", await upload.read()))
        except Exception as e:
            errors.append({"filename": upload.filename, "error": str(e)})
    return {"attachments": added, "errors": errors}


@router.post("/conversations/{cid}/attachments/paths")
async def attach_paths(request: Request, cid: str, body: AttachPathsRequest) -> dict:
    """Attach by PATH -- the drag-and-drop route.

    A file dragged from the file manager arrives as a location, not bytes, so
    reading it here avoids pushing the whole file through the renderer and back
    over HTTP. Dropping a FOLDER expands it, bounded, skipping build output.
    """
    s = state(request)
    await s.conversations.get(cid)
    added, errors = [], []
    truncated_folders: list[str] = []

    for raw in body.paths:
        p = Path(raw)
        try:
            if p.is_dir():
                files, hit_cap = attachments_mod.expand_folder(p)
                if hit_cap:
                    truncated_folders.append(p.name)
                if not files:
                    errors.append({"filename": p.name, "error": "No readable files in this folder."})
                for f in files:
                    try:
                        added.append(await s.attachments.add_path(cid, str(f)))
                    except Exception as e:
                        errors.append({"filename": f.name, "error": str(e)})
            else:
                added.append(await s.attachments.add_path(cid, str(p)))
        except Exception as e:
            errors.append({"filename": p.name or raw, "error": str(e)})

    return {"attachments": added, "errors": errors, "truncated_folders": truncated_folders}


@router.get("/conversations/{cid}/attachments")
async def list_staged_attachments(request: Request, cid: str) -> list[dict]:
    return await state(request).attachments.staged(cid)


@router.delete("/attachments/{attachment_id}")
async def delete_attachment(request: Request, attachment_id: str) -> dict:
    await state(request).attachments.delete(attachment_id)
    return {"ok": True}


# ---------- workspace ----------

# Directories that are never worth showing and expensive to walk. Not a
# security control -- _safe_path is that -- purely signal-to-noise, so the tree
# shows the project rather than its build output.
_TREE_SKIP = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next",
    ".idea", ".vscode", ".DS_Store", "target", ".tox", ".cache",
}
# A hard ceiling on nodes returned. A user can always point Arthur at their
# home directory by mistake; walking it unbounded would hang the request and
# then hand the UI a tree nobody can read. Truncating and SAYING SO is the
# honest failure.
_TREE_MAX_NODES = 2000


@router.get("/workspace/tree")
async def workspace_tree(request: Request, conversation_id: str | None = None) -> dict:
    """The conversation's folder as a nested tree.

    Exists because a folder you cannot see is only half a feature: before this,
    the workspace was a path string in Settings, so there was no way to confirm
    Arthur was pointed at the right place or to reference a file without typing
    its path from memory.
    """
    s = state(request)
    root = await _conversation_workspace(s, conversation_id)
    if not root:
        return {"root": None, "tree": [], "truncated": False}
    base = Path(root)
    if not base.is_dir():
        return {"root": root, "tree": [], "truncated": False, "missing": True}

    budget = [_TREE_MAX_NODES]

    def walk(directory: Path, depth: int) -> list[dict]:
        if depth > 6 or budget[0] <= 0:
            return []
        try:
            entries = sorted(
                directory.iterdir(),
                # Folders first, then case-insensitive by name -- the ordering
                # every file browser uses, so the tree reads without thinking.
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return []  # unreadable directory is a gap in the tree, not an error
        out = []
        for entry in entries:
            if budget[0] <= 0:
                break
            name = entry.name
            if name in _TREE_SKIP or name.startswith("."):
                continue
            budget[0] -= 1
            if entry.is_dir():
                out.append({
                    "name": name,
                    "path": str(entry.relative_to(base)).replace("\\", "/"),
                    "dir": True,
                    "children": walk(entry, depth + 1),
                })
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                out.append({
                    "name": name,
                    "path": str(entry.relative_to(base)).replace("\\", "/"),
                    "dir": False,
                    "size": size,
                })
        return out

    tree = await asyncio.to_thread(walk, base, 0)
    return {"root": root, "tree": tree, "truncated": budget[0] <= 0}


# ---------- approvals ----------

@router.post("/approvals/{approval_id}")
async def resolve_approval(request: Request, approval_id: str, body: ApprovalDecision) -> dict:
    s = state(request)
    resolved = s.approvals.resolve(approval_id, body.approved, body.args)
    await s.audit.record(
        "approval_decision", "info" if body.approved else "warning",
        approval_id=approval_id, approved=body.approved, known=resolved,
    )
    return {"ok": resolved}


# ---------- memory ----------

@router.get("/memory")
async def list_memory(request: Request) -> list[dict]:
    return await state(request).memory.list_all()


@router.post("/memory")
async def add_memory(request: Request, body: MemoryCreate) -> dict:
    row = await state(request).memory.add(body.text, body.category)
    if row is None:
        raise ArthurError("Memory could not be saved — embedding model unavailable.",
                          detail={"code_hint": "ollama"})
    row.pop("embedding", None)
    return row


@router.patch("/memory/{mem_id}")
async def update_memory(request: Request, mem_id: str, body: MemoryUpdate) -> dict:
    s = state(request)
    if body.text is not None:
        row = await s.memory.update_text(mem_id, body.text)
        if row is None:
            raise NotFoundError("memory not found")
    if body.enabled is not None:
        await s.memory.set_enabled(mem_id, body.enabled)
    return {"ok": True}


@router.delete("/memory/{mem_id}")
async def delete_memory(request: Request, mem_id: str) -> dict:
    await state(request).memory.delete(mem_id)
    return {"ok": True}


# ---------- personas ----------

@router.get("/personas")
async def list_personas(request: Request) -> list[dict]:
    return await state(request).personas.list_all()


@router.post("/personas")
async def create_persona(request: Request, body: PersonaBody) -> dict:
    return await state(request).personas.create(body.name, body.system_prompt, body.few_shots)


@router.put("/personas/{pid}")
async def update_persona(request: Request, pid: str, body: PersonaBody) -> dict:
    await state(request).personas.update(pid, body.name, body.system_prompt, body.few_shots)
    return {"ok": True}


@router.post("/personas/{pid}/activate")
async def activate_persona(request: Request, pid: str) -> dict:
    await state(request).personas.activate(pid)
    return {"ok": True}


@router.delete("/personas/{pid}")
async def delete_persona(request: Request, pid: str) -> dict:
    ok = await state(request).personas.delete(pid)
    return {"ok": ok}


# ---------- settings & secrets ----------

@router.get("/settings")
async def get_settings_route(request: Request) -> dict:
    s = state(request)
    return {
        "default_model": await s.db.get_setting("default_model", ""),
        "workspace_root": await s.db.get_setting("workspace_root", None),
        "allow_unsandboxed_network_tools": await s.db.get_setting("allow_unsandboxed_network_tools", False),
        "code_review_before_apply": await s.db.get_setting(
            "code_review_before_apply", s.settings.code_review_before_apply),
        "memory_enabled": await s.db.get_setting("memory_enabled", True),
        "font_scale": await s.db.get_setting("font_scale", 1.0),
        "email_address": await s.db.get_setting("email_address", None),
        "email_smtp_host": await s.db.get_setting("email_smtp_host", None),
        "email_smtp_port": await s.db.get_setting("email_smtp_port", None),
        "email_imap_host": await s.db.get_setting("email_imap_host", None),
        "email_imap_port": await s.db.get_setting("email_imap_port", None),
        "mode_models": await s.db.get_setting("mode_models", {}),
        "scanner_mode": await s.db.get_setting("scanner_mode", "standard"),
    }


@router.patch("/settings")
async def patch_settings(request: Request, body: SettingsPatch) -> dict:
    s = state(request)
    for key, value in body.model_dump(exclude_none=True).items():
        await s.db.set_setting(key, value)
        # MIRRORED ONTO THE LIVE SETTINGS OBJECT, not just stored. The chat turn
        # reads this on every message to decide whether to write files or stage
        # them; leaving it in the DB alone would mean the toggle did nothing
        # until the app was restarted -- the worst way for a safety switch to
        # behave, because it looks like it worked.
        if key == "code_review_before_apply":
            s.settings.code_review_before_apply = bool(value)
        # security-relevant settings leave a trace in the event log
        if key == "allow_unsandboxed_network_tools" or (key == "scanner_mode" and value != "standard"):
            await s.audit.record("setting_changed", "warning", key=key, value=value)
    return {"ok": True}


@router.put("/secrets")
async def put_secret(request: Request, body: SecretBody) -> dict:
    state(request).vault.set(body.name, body.value)
    await state(request).audit.record("secret_stored", "info", name=body.name)
    return {"ok": True}


@router.delete("/secrets/{name}")
async def delete_secret(request: Request, name: str) -> dict:
    state(request).vault.delete(name)
    return {"ok": True}


# ---------- security ----------

@router.get("/security/events")
async def security_events(request: Request, limit: int = 100, offset: int = 0) -> list[dict]:
    return await state(request).audit.recent(min(limit, 500), offset)


@router.delete("/security/events")
async def purge_security_events(request: Request) -> dict:
    await state(request).audit.purge()
    return {"ok": True}


# ---------- integrations ----------

@router.post("/integrations/email/test")
async def test_email(request: Request) -> dict:
    """Verify saved SMTP credentials by logging in (no email sent)."""
    s = state(request)
    smtp_backend = s.email_router._smtp
    try:
        await smtp_backend.verify()
        await s.audit.record("email_verified", "info")
        return {"ok": True}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}


@router.delete("/integrations/email")
async def disconnect_email(request: Request) -> dict:
    """Remove SMTP email credentials completely: password out of the OS vault,
    address + any custom hosts out of settings."""
    s = state(request)
    s.vault.delete("email_password")
    for key in ("email_address", "email_smtp_host", "email_smtp_port",
                "email_imap_host", "email_imap_port"):
        await s.db.set_setting(key, None)
    await s.audit.record("email_disconnected", "info")
    return {"ok": True}


# (/integrations/ms/login and /logout removed with MS Graph.)


# ---------- voice ----------

@router.post("/voice/transcribe")
async def transcribe(request: Request, audio: UploadFile) -> dict:
    data = await audio.read()
    if len(data) > 25_000_000:
        raise VoiceError("Recording too large (25MB max).")
    text = await state(request).transcriber.transcribe(data)
    return {"text": text}


def _json(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)
