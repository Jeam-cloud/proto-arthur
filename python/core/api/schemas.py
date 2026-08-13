"""Request bodies. Pydantic rejects bad payloads at the door with a 422 —
handlers never see them, which is the point of validating at the boundary."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from tools.base import TaskMode


class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=8, max_length=64)
    # Empty is ALLOWED, because a message can be carried entirely by its
    # attachments -- dropping a screenshot and pressing send with no words is a
    # complete request. `min_length=1` here rejected that with a 422 the UI
    # could only report as "Stream failed (422)".
    #
    # Emptiness is not unchecked, it has just moved: the route refuses a request
    # with neither text NOR attachments, which is the condition that actually
    # matters and which a schema cannot see.
    message: str = Field(default="", max_length=32_000)
    mode: TaskMode = TaskMode.GENERAL
    model: str = Field(default="", max_length=100)     # empty -> server default
    provider: str = Field(default="local", pattern="^(local|openai|anthropic)$")


class ResearchPlanRequest(BaseModel):
    question: str = Field(min_length=8, max_length=1000)
    depth: str = Field(default="standard", pattern="^(quick|standard|exhaustive)$")
    model: str = Field(default="", max_length=100)


class ResearchRunRequest(BaseModel):
    question: str = Field(min_length=8, max_length=1000)
    # Sub-questions arrive from the client because the user edited them on the
    # plan screen. The server does NOT regenerate them: silently rewriting what
    # someone just approved is the fastest way to make a review step feel fake.
    sub_questions: list[str] = Field(min_length=1, max_length=8)
    depth: str = Field(default="standard", pattern="^(quick|standard|exhaustive)$")
    sources: list[str] = Field(default_factory=lambda: ["web", "academic"], max_length=6)
    model: str = Field(default="", max_length=100)
    include_domains: list[str] = Field(default_factory=list, max_length=20)
    exclude_domains: list[str] = Field(default_factory=list, max_length=20)
    # Whole-paper length target in words; 0 means "no target, let the model
    # decide", which is the default and the previous behaviour. The upper
    # bound is not arbitrary: past roughly 6000 words a small local model is
    # padding, not writing, and the run time stops being worth it.
    target_words: int = Field(default=0, ge=0, le=6000)
    # A page cap the client converts to words (see engine.words_for_pages).
    # Sent as well as target_words so the server can honour whichever the user
    # actually set without the client having to decide which wins.
    max_pages: int = Field(default=0, ge=0, le=40)

    @field_validator("sub_questions")
    @classmethod
    def _trim(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip()[:400] for s in v if s and s.strip()]
        if not cleaned:
            raise ValueError("at least one sub-question is required")
        return cleaned


class ResearchSynthesizeRequest(BaseModel):
    """Writes the report from sources the CLIENT already has, without
    searching again -- see ResearchEngine.synthesize_only for why this needs
    to be its own request rather than reusing /research/run.

    `sources` are round-tripped: they're exactly the dicts the run stream
    already sent the browser as `research_source` events, sent back verbatim.
    Deliberately untyped (`list[dict]`) rather than re-declaring every evidence
    field here -- the shape is owned by research/engine.py in one place, and
    duplicating it into a second schema is how the two quietly drift apart.
    """
    question: str = Field(min_length=8, max_length=1000)
    sources: list[dict] = Field(min_length=1, max_length=200)
    model: str = Field(default="", max_length=100)
    # The approved sub-questions become the paper's section headings. Optional
    # because the post-stop path may no longer have them, in which case the
    # engine recovers an outline from the sources' lane grouping instead.
    sub_questions: list[str] = Field(default_factory=list, max_length=8)
    target_words: int = Field(default=0, ge=0, le=6000)
    max_pages: int = Field(default=0, ge=0, le=40)


class ResearchFindSourcesRequest(BaseModel):
    """The 'Find more sources' box: the user types what they want more of."""
    query: str = Field(min_length=3, max_length=400)
    sources: list[dict] = Field(default_factory=list, max_length=200)
    kinds: list[str] = Field(default_factory=lambda: ["web", "academic"], max_length=6)
    include_domains: list[str] = Field(default_factory=list, max_length=20)
    exclude_domains: list[str] = Field(default_factory=list, max_length=20)


class ResearchExportRequest(BaseModel):
    """Renders an already-written paper. Nothing is generated here, so no
    model is involved unless the citation style is `custom`."""
    paper: dict
    sources: list[dict] = Field(default_factory=list, max_length=200)
    fmt: str = Field(default="docx", pattern="^(docx|pdf)$")
    style: str = Field(default="apa", max_length=20)
    custom_style: str = Field(default="", max_length=600)
    model: str = Field(default="", max_length=100)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ArchiveRequest(BaseModel):
    archived: bool = True


class ConversationModelRequest(BaseModel):
    """The model this conversation should use from now on.

    Empty string means "Auto" (defer to the mode's assigned model, then the
    global default) and is a deliberate choice, not a missing value — which is
    why it is `str` with a default of "" rather than `str | None`.
    """
    model: str = Field(default="", max_length=120)


class AttachPathsRequest(BaseModel):
    """Absolute paths of dropped files or folders.

    Capped at 20 ENTRIES, not 20 files: one of those entries can be a folder
    that expands to many (bounded separately by MAX_FOLDER_FILES). The cap here
    is only to stop a pathological drop from arriving as one enormous request.
    """
    paths: list[str] = Field(min_length=1, max_length=20)


class WorkspaceRequest(BaseModel):
    """The folder a conversation may read and write.

    `None` clears the binding rather than denying access -- an unbound
    conversation falls back to the last-used folder (see
    _conversation_workspace in routes.py). The path is NOT validated here on
    purpose: containment is enforced when a tool touches a file, not when the
    folder is chosen, so an unplugged drive is remembered rather than lost.
    """
    root: str | None = Field(default=None, max_length=500)


class NewConversation(BaseModel):
    """Mode and folder, decided when the chat is created.

    Both are optional so `POST /conversations` with no body still works — that
    is what every non-Code entry point does, and it produces a General chat
    exactly as before.

    `mode` is a plain string validated against TaskMode at use, not an enum
    here: the API should reject an unknown mode the same way the registry does
    (by granting no tools), not with a 422 that reads like a bug in the app.
    """
    mode: str = Field(default="general", max_length=20)
    workspace_root: str | None = Field(default=None, max_length=500)


class ChangesRequest(BaseModel):
    """Which staged files to apply or discard.

    `None` means "all", which is the one-click case after reading the diff.
    A list is the partial case: taking three of the agent's five edits is a
    normal code-review outcome, not an edge case.

    Paths are validated again inside ChangeSet against the workspace root, so
    a hostile list here can only ever name files that are already staged.
    """
    paths: list[str] | None = Field(default=None, max_length=200)


class UndoRequest(BaseModel):
    """Which apply to reverse.

    `None` means the most recent one in this conversation — the Undo button on
    the receipt, which is the only case that matters in practice. An explicit
    id exists so a user who has scrolled back can reverse an older apply, and
    it is checked against that conversation's own snapshots before it is used.
    """
    id: str | None = Field(default=None, max_length=64)


class MemoryCreate(BaseModel):
    text: str = Field(min_length=3, max_length=500)
    category: str = Field(default="other", pattern="^(profile|preference|project|other)$")


class MemoryUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=3, max_length=500)
    enabled: bool | None = None


class PersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    system_prompt: str = Field(min_length=1, max_length=8000)
    few_shots: list[dict] = Field(default_factory=list, max_length=8)


class ApprovalDecision(BaseModel):
    approved: bool
    # Present only when the user edited the draft before approving (e.g.
    # reworded an email). Left untyped here on purpose: the shape depends on
    # which tool's approval this resolves, and the REAL validation gate is
    # tool.Args.model_validate() in agent/loop.py -- this schema only needs to
    # keep the payload a plain JSON object, not police its contents.
    args: dict | None = None


class SecretBody(BaseModel):
    name: str = Field(pattern="^(tavily|byok_openai|byok_anthropic|byok_gemini|email_password)$")
    value: str = Field(min_length=8, max_length=500)


class SettingsPatch(BaseModel):
    """Whitelist of user-tunable settings — arbitrary keys are rejected, so a
    compromised renderer can't scribble into config the backend trusts."""

    default_model: str | None = Field(default=None, max_length=100)
    workspace_root: str | None = Field(default=None, max_length=500)
    allow_unsandboxed_network_tools: bool | None = None
    # Code mode: stage edits for review instead of writing them and offering
    # Undo. Off by default -- see `code_review_before_apply` in core/config.py.
    code_review_before_apply: bool | None = None
    memory_enabled: bool | None = None
    font_scale: float | None = Field(default=None, ge=0.8, le=1.5)
    # SMTP/IMAP email (hosts optional — presets fill them from the address domain)
    email_address: str | None = Field(default=None, max_length=200)
    email_smtp_host: str | None = Field(default=None, max_length=200)
    email_smtp_port: int | None = Field(default=None, ge=1, le=65535)
    email_imap_host: str | None = Field(default=None, max_length=200)
    email_imap_port: int | None = Field(default=None, ge=1, le=65535)
    # Input-scanner strictness: standard blocks, relaxed warns-only, off skips.
    # Tool-output scanning and approval dialogs are unaffected by design.
    scanner_mode: str | None = Field(default=None, pattern="^(standard|relaxed|off)$")
    # Per-mode model assignments, e.g. {"finance": "qwen3:14b"}. Empty string
    # value = "use the default". Unknown mode keys are rejected.
    mode_models: dict[str, str] | None = None

    @field_validator("mode_models")
    @classmethod
    def valid_modes(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        valid = {m.value for m in TaskMode}
        bad = set(v) - valid
        if bad:
            raise ValueError(f"unknown modes: {sorted(bad)}")
        return {k: val[:100] for k, val in v.items()}


class PullRequest(BaseModel):
    model: str = Field(min_length=2, max_length=100)
