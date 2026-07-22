"""Chat orchestration — the request's journey from keystroke to stored reply.

Order of operations per message (each step justified):
 1. gateway.scan_user_input      — cheapest place to stop an injection
 2. persist the user message     — even if generation fails, the user's text
                                   is never lost (refresh-safe)
 3. memory recall                — BEFORE building the prompt, so relevant
                                   facts are in context
 4. assemble messages            — persona + spotlight rules + memory block +
                                   few-shots + trimmed history
 5. agent loop (streams tokens)  — tools only for the chosen mode
 6. scan + persist the reply     — redact secrets before storage/display
 7. background: title generation + fact extraction (never block the stream)

WHY background tasks get their own error handling: an exception in a fire-and-
forget task otherwise disappears silently (or worse, kills the task group).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent.loop import AgentLoop
from core import events
from core.config import Settings
from core.conversations import ConversationStore
from core.errors import ArthurError
from core.ollama_client import OllamaClient
from core.personas import PersonaStore
from memory.extractor import build_extraction_messages, parse_facts
from memory.service import MemoryService
from security.gateway import SecurityGateway
from security.spotlight import SPOTLIGHT_SYSTEM_NOTE
from tools.base import TaskMode, ToolContext

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

TITLE_PROMPT = (
    "Write a 2-5 word title for a conversation that starts with this message. "
    "Reply with ONLY the title, no quotes:\n\n{message}"
)

# Per-mode operating instructions appended to the system prompt. Small local
# models NEED this: without it they invent human assumptions ("I need your
# login credentials to send email") because nothing told them the app already
# handles auth, or they ask permission in prose instead of calling the tool
# (the app's approval dialog IS the permission step). Only the CURRENT mode's
# note is included — context is precious on 4k-8k window models.
MODE_GUIDANCE = {
    TaskMode.EMAIL: (
        "Email is CONNECTED and working in this app. Credentials are managed by the "
        "app itself — NEVER ask the user for passwords, login details, or which email "
        "provider they use. To send an email: call the email_send tool directly with a "
        "polished subject and body drafted from the user's request. The app shows the "
        "user a confirmation dialog with the full draft before anything actually sends, "
        "so do not ask for permission in text and do not say you are unable to send — "
        "just make the tool call."
    ),
    TaskMode.COMPUTER: (
        "You control this computer through the provided tools (open_app, screenshot, "
        "mouse_click, type_text, press_keys). The app asks the user to confirm each "
        "risky action — so call the tool directly instead of asking permission in text. "
        "Take a screenshot first when you need to see the screen."
    ),
    TaskMode.RESEARCH: (
        "Use web_research for anything needing current or external information — do not "
        "answer from memory when the user asks you to look something up. Cite sources as [n]."
    ),
    TaskMode.CODE: (
        "You can read/write files in the user's workspace folder and run Python in an "
        "isolated sandbox via the tools. Writes and code execution are confirmed by the "
        "app — call the tools directly."
    ),
    TaskMode.FINANCE: (
        "Use stock_quote and stock_history for market data — never invent prices. "
        "Data is ~15min delayed; say so when precision matters."
    ),
}


class ChatService:
    def __init__(
        self,
        settings: Settings,
        llm: OllamaClient,
        conversations: ConversationStore,
        personas: PersonaStore,
        memory: MemoryService,
        gateway: SecurityGateway,
        agent: AgentLoop,
        byok_router=None,  # optional BYOKRouter — cloud requests bypass tools by design
    ):
        self._settings = settings
        self._llm = llm
        self._conversations = conversations
        self._personas = personas
        self._memory = memory
        self._gateway = gateway
        self._agent = agent
        self._byok = byok_router
        self._background: set[asyncio.Task] = set()

    async def stream_reply(
        self,
        conversation_id: str,
        user_text: str,
        mode: TaskMode,
        model: str,
        emit: Emit,
        provider: str = "local",
        workspace_root: str | None = None,
        services: dict[str, Any] | None = None,
        scanner_mode: str = "standard",
    ) -> None:
        conv = await self._conversations.get(conversation_id)

        # 1. input gate (raises SecurityBlockError -> surfaced as SSE error by the route)
        await self._gateway.scan_user_input(user_text, mode=scanner_mode)

        # 2. persist user turn immediately
        await self._conversations.add_message(conversation_id, "user", user_text)

        # 3. memory recall (fails soft — chat works with memory down)
        memories = await self._memory.recall(user_text)
        if memories:
            await emit(events.MEMORY_USED, {
                "items": [{"id": m["id"], "text": m["text"]} for m in memories]
            })

        # 4. prompt assembly
        persona = await self._personas.active()
        messages = self._build_messages(persona, memories, user_text,
                                        await self._conversations.history_for_model(conversation_id),
                                        mode=mode)

        # 5. generate
        if provider != "local" and self._byok is not None:
            final_text = await self._byok.stream_chat(provider, messages, emit)
        else:
            ctx = ToolContext(conversation_id=conversation_id,
                              workspace_root=workspace_root, services=services)
            final_text = await self._agent.run(model, messages, mode, ctx, emit)

        # 6. sanitize + persist assistant turn
        final_text = await self._gateway.scan_model_output(final_text)
        message_id = await self._conversations.add_message(
            conversation_id, "assistant", final_text, model=model, provider=provider
        )
        await emit(events.DONE, {"message_id": message_id, "conversation_id": conversation_id})

        # 7. background work
        if conv["title"] == "New chat":
            self._spawn(self._generate_title(conversation_id, user_text, model, emit))
        self._spawn(self._extract_memories(conversation_id, user_text, model))

    def _build_messages(self, persona, memories, user_text, history,
                        mode: TaskMode = TaskMode.GENERAL) -> list[dict]:
        system = persona["system_prompt"] + "\n\n" + SPOTLIGHT_SYSTEM_NOTE
        guidance = MODE_GUIDANCE.get(mode)
        if guidance:
            system += "\n\n" + guidance
        block = self._memory.format_context_block(memories)
        if block:
            system += "\n\n" + block
        messages: list[dict] = [{"role": "system", "content": system}]
        for shot in persona.get("few_shots", []):
            if shot.get("user") and shot.get("assistant"):
                messages.append({"role": "user", "content": shot["user"]})
                messages.append({"role": "assistant", "content": shot["assistant"]})
        messages.extend(history)  # history already includes nothing from this turn
        messages.append({"role": "user", "content": user_text})
        return messages

    def _spawn(self, coro) -> None:
        """Tracked fire-and-forget: keeps a strong reference (otherwise the GC
        can cancel a running task — a classic asyncio gotcha) and logs failures."""
        task = asyncio.create_task(coro)
        self._background.add(task)

        def _finish(t: asyncio.Task) -> None:
            self._background.discard(t)
            if not t.cancelled() and t.exception():
                log.warning("background task failed: %r", t.exception())

        task.add_done_callback(_finish)

    async def _generate_title(self, cid: str, first_message: str, model: str, emit: Emit) -> None:
        try:
            chunks = []
            async for ev in self._llm.chat_stream(
                model, [{"role": "user", "content": TITLE_PROMPT.format(message=first_message[:500])}]
            ):
                if ev["type"] == "token":
                    chunks.append(ev["content"])
            title = "".join(chunks).strip().strip('"').splitlines()[0][:60] or "New chat"
            await self._conversations.rename(cid, title)
            await emit(events.TITLE, {"conversation_id": cid, "title": title})
        except ArthurError:
            pass  # cosmetic feature — never bother the user about it

    async def _extract_memories(self, cid: str, user_text: str, model: str) -> None:
        """Extraction reads ONLY the user's text — see memory/extractor.py
        for the poisoning rationale."""
        if len(user_text) < 20:
            return
        try:
            chunks = []
            async for ev in self._llm.chat_stream(model, build_extraction_messages(user_text)):
                if ev["type"] == "token":
                    chunks.append(ev["content"])
            for fact in parse_facts("".join(chunks)):
                await self._memory.add(fact["fact"], fact["category"], source_conversation_id=cid)
        except ArthurError as e:
            log.debug("memory extraction skipped: %s", e)
