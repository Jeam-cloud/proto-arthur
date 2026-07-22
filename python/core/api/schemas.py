"""Request bodies. Pydantic rejects bad payloads at the door with a 422 —
handlers never see them, which is the point of validating at the boundary."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from tools.base import TaskMode


class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=32_000)
    mode: TaskMode = TaskMode.GENERAL
    model: str = Field(default="", max_length=100)     # empty -> server default
    provider: str = Field(default="local", pattern="^(local|openai|anthropic)$")


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


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


class SecretBody(BaseModel):
    name: str = Field(pattern="^(tavily|byok_openai|byok_anthropic|byok_gemini|email_password)$")
    value: str = Field(min_length=8, max_length=500)


class SettingsPatch(BaseModel):
    """Whitelist of user-tunable settings — arbitrary keys are rejected, so a
    compromised renderer can't scribble into config the backend trusts."""

    default_model: str | None = Field(default=None, max_length=100)
    workspace_root: str | None = Field(default=None, max_length=500)
    allow_unsandboxed_network_tools: bool | None = None
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
