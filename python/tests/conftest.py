"""Shared fixtures: a fully wired AppState over fakes + a temp database.

The graph built here is shaped exactly like core/deps.py builds production —
same classes, same wiring — with the network-edge pieces (LLM, embedder,
scanner) swapped for fakes. API tests then run against the real FastAPI app.
"""

from __future__ import annotations

import pytest

from agent.loop import AgentLoop
from agent.registry import ToolRegistry
from core.attachments import AttachmentStore
from core.chat_service import ChatService
from core.config import Settings
from core.conversations import ConversationStore
from core.db import Database
from core.deps import AppState
from core.personas import PersonaStore
from memory.service import MemoryService
from memory.vector_store import InMemoryVectorStore
from research.engine import ResearchEngine
from security.approvals import ApprovalBroker
from security.audit import AuditLog
from security.gateway import SecurityGateway
from tests.fakes import (
    ConfirmEchoTool, CrashTool, EchoTool, ExternalTool, FakeEmbedder, FakeLLM, FakeScanner,
)
from coding.changeset import ChangeSetStore
from tools.coding import (
    DeleteFileTool, EditFileTool, ListDirTool, ReadFileTool, WriteFileTool,
)
from tools.email_service import EmailRouter, SmtpImapBackend


class FakeVault:
    """In-memory stand-in for the OS credential vault."""

    def __init__(self):
        self.data = {}

    def set(self, name, value):
        self.data[name] = value

    def get(self, name):
        return self.data.get(name)

    def delete(self, name):
        self.data.pop(name, None)

    def status(self):
        return {k: True for k in self.data}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        auth_token="test-token-123",
        approval_timeout_s=0.05,  # timeouts resolve fast in tests
        scanner_backend="heuristic",
    )


@pytest.fixture
async def db(settings) -> Database:
    database = Database(settings.db_path)
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
async def app_state(settings, db, fake_llm, embedder, vault) -> AppState:
    audit = AuditLog(db)
    gateway = SecurityGateway(FakeScanner(), audit, settings)
    approvals = ApprovalBroker(timeout_s=settings.approval_timeout_s)
    memory = MemoryService(db, embedder, InMemoryVectorStore())
    personas = PersonaStore(db)
    await personas.ensure_default()
    conversations = ConversationStore(db)
    attachment_store = AttachmentStore(db, settings.data_dir)

    changesets = ChangeSetStore()

    registry = ToolRegistry()
    for tool in (EchoTool(), ConfirmEchoTool(), ExternalTool(), CrashTool(),
                 ReadFileTool(), ListDirTool(), WriteFileTool(), EditFileTool(),
                 DeleteFileTool()):
        registry.register(tool)

    agent = AgentLoop(fake_llm, registry, gateway, approvals, max_iterations=4)
    chat = ChatService(settings, fake_llm, conversations, personas, memory, gateway, agent,
                       attachments=attachment_store, changesets=changesets)

    email_router = EmailRouter(SmtpImapBackend(db, vault))
    sandbox = _NoSandbox()
    research = ResearchEngine(fake_llm, vault, sandbox, embedder, gateway)
    return AppState(
        settings=settings, db=db, llm=fake_llm, vault=vault, audit=audit, gateway=gateway,
        approvals=approvals, sandbox=sandbox, memory=memory, personas=personas,
        conversations=conversations, attachments=attachment_store,
        changesets=changesets, registry=registry, agent=agent, chat=chat,
        email_router=email_router, transcriber=None, byok=None,
        research=research,
    )


class _NoSandbox:
    async def is_available(self) -> bool:
        return False


class _NoGraph:
    def is_connected(self) -> bool:
        return False
