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
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, UploadFile
from sse_starlette.sse import EventSourceResponse

from core import events
from core.api.auth import require_auth
from core.api.schemas import (
    ApprovalDecision, ArchiveRequest, ChatRequest, MemoryCreate, MemoryUpdate, PersonaBody,
    PullRequest, RenameRequest, SecretBody, SettingsPatch,
)
from core.deps import AppState
from core.errors import ArthurError, NotFoundError, VoiceError
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
        except ArthurError:
            ollama_up = False
    return {
        "ollama_up": ollama_up,
        "models": models,
        "docker_up": await s.sandbox.is_available(),
        "scanner_backend": s.gateway.backend_name,
        "memory_available": s.memory.available,
        "ms_connected": s.graph.is_connected(),
        "email_configured": await s.email_router.is_configured(),
        "secrets": s.vault.status(),
        "default_model": await s.db.get_setting("default_model", ""),
        "onboarded": bool(await s.db.get_setting("onboarded", False)),
        "workspace_root": await s.db.get_setting("workspace_root", None),
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

    async def run() -> None:
        try:
            await s.chat.stream_reply(
                conversation_id=body.conversation_id,
                user_text=body.message,
                mode=body.mode,
                model=model,
                emit=emit,
                provider=body.provider,
                workspace_root=await s.db.get_setting("workspace_root", None),
                scanner_mode=await s.db.get_setting("scanner_mode", "standard"),
            )
        except ArthurError as e:
            await emit(events.ERROR, {"code": e.code, "message": e.message, **e.detail})
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


# ---------- conversations ----------

@router.get("/conversations")
async def list_conversations(request: Request, archived: bool = False) -> list[dict]:
    return await state(request).conversations.list_all(archived=archived)


@router.post("/conversations")
async def create_conversation(request: Request) -> dict:
    return await state(request).conversations.create()


@router.get("/conversations/{cid}/messages")
async def conversation_messages(request: Request, cid: str) -> list[dict]:
    await state(request).conversations.get(cid)  # 404 for unknown ids
    return await state(request).conversations.messages(cid)


@router.patch("/conversations/{cid}")
async def rename_conversation(request: Request, cid: str, body: RenameRequest) -> dict:
    await state(request).conversations.rename(cid, body.title)
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


# ---------- approvals ----------

@router.post("/approvals/{approval_id}")
async def resolve_approval(request: Request, approval_id: str, body: ApprovalDecision) -> dict:
    s = state(request)
    resolved = s.approvals.resolve(approval_id, body.approved)
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


@router.post("/integrations/ms/login")
async def ms_login(request: Request) -> dict:
    result = await state(request).graph.login_interactive()
    await state(request).audit.record("ms_connected", "info", username=result["username"])
    return result


@router.post("/integrations/ms/logout")
async def ms_logout(request: Request) -> dict:
    state(request).graph.logout()
    return {"ok": True}


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
