"""Composition root — every service is constructed HERE, once, and wired by
constructor injection.

WHY this instead of modules importing each other's singletons: the dependency
graph stays visible in one file, there are no import-order surprises, and
tests can build the same graph with fakes (see tests/conftest.py). This is
plain dependency injection without a framework — the FastAPI-idiomatic way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.loop import AgentLoop
from agent.registry import ToolRegistry
from byok.router import BYOKRouter
from coding.changeset import ChangeSetStore
from coding.undo import UndoStore
from core.chat_service import ChatService
from core.config import Settings
from core.attachments import AttachmentStore
from core.conversations import ConversationStore
from core.db import Database
from core.ollama_client import OllamaClient
from core.personas import PersonaStore
from memory.embedder import OllamaEmbedder
from memory.service import MemoryService
from memory.vector_store import build_vector_store
from research.engine import ResearchEngine
from sandbox.runner import SandboxRunner
from security.approvals import ApprovalBroker
from security.audit import AuditLog
from security.gateway import SecurityGateway
from security.scanners import build_scanner
from security.vault import SecretsVault
from tools.coding import (
    DeleteFileTool, EditFileTool, ListDirTool, ReadFileTool, RunPythonTool, WriteFileTool,
)
from tools.computer import ClickTool, OpenAppTool, PressKeysTool, ScreenshotTool, TypeTextTool
from tools.email_service import (
    EmailListTool, EmailRouter, EmailSearchTool, EmailSendTool,
    SmtpImapBackend,
)
from tools.finance import StockHistoryTool, StockQuoteTool
from tools.research import QuickSearchTool, WebResearchTool
from tools.search import FindFilesTool, SearchFilesTool
from voice.transcriber import Transcriber

log = logging.getLogger(__name__)


@dataclass
class AppState:
    settings: Settings
    db: Database
    llm: OllamaClient
    vault: SecretsVault
    audit: AuditLog
    gateway: SecurityGateway
    approvals: ApprovalBroker
    sandbox: SandboxRunner
    memory: MemoryService
    personas: PersonaStore
    conversations: ConversationStore
    attachments: AttachmentStore
    changesets: ChangeSetStore
    undos: UndoStore
    registry: ToolRegistry
    agent: AgentLoop
    chat: ChatService
    email_router: EmailRouter
    transcriber: Transcriber
    byok: BYOKRouter
    research: ResearchEngine


async def build_state(settings: Settings) -> AppState:
    db = Database(settings.db_path)
    await db.connect()

    llm = OllamaClient(
        settings.ollama_host, keep_alive=settings.keep_alive, num_ctx=settings.num_ctx,
    )
    vault = SecretsVault()
    audit = AuditLog(db)
    scanner = build_scanner(settings.scanner_backend)
    gateway = SecurityGateway(scanner, audit, settings)
    approvals = ApprovalBroker(timeout_s=settings.approval_timeout_s)
    sandbox = SandboxRunner()

    embedder = OllamaEmbedder(llm, settings.embed_model)
    vstore = build_vector_store(settings.chroma_path)
    memory = MemoryService(db, embedder, vstore)
    restored = await memory.rebuild_index()
    if restored:
        log.info("restored %d memories into the vector index", restored)

    personas = PersonaStore(db)
    await personas.ensure_default()
    conversations = ConversationStore(db)
    attachment_store = AttachmentStore(db, settings.data_dir)
    # Deliberately process-lifetime and in-memory: unapplied edits should not
    # survive a restart. See the module docstring in coding/changeset.py.
    changesets = ChangeSetStore()
    # The mirror image, and on disk for the opposite reason: an APPLIED edit
    # already touched the user's files, so the way back has to outlive the
    # process. See coding/undo.py.
    undos = UndoStore(settings.undo_dir)

    # One backend: SMTP/IMAP with an app password. MS Graph was removed --
    # see the module docstring in tools/email_service.py for why.
    email_router = EmailRouter(SmtpImapBackend(db, vault))
    allow_unsandboxed = bool(await db.get_setting("allow_unsandboxed_network_tools", False))
    # Stored in the DB, mirrored onto Settings, which is what the chat turn
    # reads. Without this the toggle would only take effect after a restart.
    settings.code_review_before_apply = bool(await db.get_setting(
        "code_review_before_apply", settings.code_review_before_apply))

    registry = ToolRegistry()
    for tool in (
        WebResearchTool(vault, sandbox, embedder, allow_unsandboxed=allow_unsandboxed),
        QuickSearchTool(vault),
        StockQuoteTool(sandbox),
        StockHistoryTool(sandbox),
        ReadFileTool(), ListDirTool(), WriteFileTool(), EditFileTool(), DeleteFileTool(),
        SearchFilesTool(), FindFilesTool(), RunPythonTool(sandbox),
        OpenAppTool(), ScreenshotTool(), ClickTool(), TypeTextTool(), PressKeysTool(),
        EmailSendTool(email_router), EmailListTool(email_router), EmailSearchTool(email_router),
    ):
        registry.register(tool)

    agent = AgentLoop(llm, registry, gateway, approvals,
                      max_iterations=settings.max_agent_iterations)
    byok = BYOKRouter(vault)
    chat = ChatService(settings, llm, conversations, personas, memory, gateway, agent,
                       byok_router=byok, attachments=attachment_store,
                       changesets=changesets, undos=undos, audit=audit)
    # Research mode does NOT go through the agent loop: an investigation is a
    # fixed Python state machine, not a model deciding what to do next. It
    # reuses the same vault/sandbox/embedder/gateway so the trust boundaries are
    # identical to the chat-side research tool.
    research = ResearchEngine(llm, vault, sandbox, embedder, gateway,
                              allow_unsandboxed=allow_unsandboxed)

    return AppState(
        settings=settings, db=db, llm=llm, vault=vault, audit=audit, gateway=gateway,
        approvals=approvals, sandbox=sandbox, memory=memory, personas=personas,
        conversations=conversations, attachments=attachment_store,
        changesets=changesets, undos=undos, registry=registry, agent=agent, chat=chat,
        email_router=email_router, transcriber=Transcriber(), byok=byok,
        research=research,
    )
