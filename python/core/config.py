"""Runtime configuration.

WHY pydantic-settings: every value is type-validated at boot, and everything
can be overridden with an ARTHUR_* environment variable. Electron passes the
three values that must be coordinated between the two processes (port, auth
token, data dir) through the subprocess environment — never through argv,
which is visible to every process on the machine via the process list.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARTHUR_", env_file=".env", extra="ignore")

    # --- transport (set by Electron in production) ---
    host: str = "127.0.0.1"  # loopback ONLY — binding 0.0.0.0 would expose the API to the LAN
    port: int = 8756
    # Bearer token required on every request except /health. Random default so a
    # dev run without Electron is still never an open server.
    auth_token: str = secrets.token_urlsafe(32)

    # --- storage ---
    data_dir: Path = Path(user_data_dir("Arthur", appauthor=False))

    # --- Ollama ---
    ollama_host: str = "http://127.0.0.1:11434"
    embed_model: str = "nomic-embed-text"
    keep_alive: str = "10m"  # keep model warm between messages; big UX win on consumer GPUs
    # Context window Ollama allocates. MUST be set explicitly -- Ollama's own
    # default is 2048, which every research synthesis prompt overflows, and an
    # overflowed prompt returns an empty generation rather than an error. See
    # DEFAULT_NUM_CTX in core/ollama_client.py for the full reasoning and the
    # memory trade-off. Lower it only on a machine that cannot spare the KV
    # cache; raising it past the model's own context does nothing.
    num_ctx: int = 8192

    # --- agent guardrails ---
    max_agent_iterations: int = 6      # hard cap: a confused local model can't loop forever
    # Code mode gets its own, far higher cap.
    #
    # WHY the default of 6 is right everywhere else and wrong here: in Email or
    # Finance a turn means one action -- send this, quote that -- so a model
    # still calling tools on the seventh round is looping, and stopping it is
    # the correct outcome. Code mode's whole premise is the opposite. "Read the
    # files, change five of them, check your work" is a dozen calls before
    # anything useful exists, and stopping halfway is the WORST outcome
    # available: it leaves a half-written changeset that looks exactly like a
    # finished one in the review panel.
    #
    # The runaway risk the low cap defends against is real (the target models
    # are small), so this is raised per-mode rather than globally, and the Stop
    # button remains the actual backstop -- a human watching is a better limiter
    # than a number that cannot tell progress from a loop.
    max_agent_iterations_code: int = 40

    # WHERE THE REVIEW GATE SITS IN CODE MODE.
    #
    # False (default) = Arthur writes the files when the turn ends and shows a
    # receipt with an Undo button. True = the old behaviour, where edits sit in
    # the review panel until the user clicks Apply.
    #
    # The default flipped because the gate was paying for itself in the wrong
    # currency: every good edit cost a click to catch the rare bad one, and a
    # prompt answered forty times a day stops being read. The protection did not
    # go away, it moved to the other side of the write -- see coding/undo.py.
    # Kept as a setting because "nothing lands without my say-so" is a
    # legitimate way to want to work, and it costs one branch to honour it.
    code_review_before_apply: bool = False
    approval_timeout_s: float = 120.0  # unanswered confirmation = denied
    tool_output_max_chars: int = 16_000

    # --- security ---
    # auto: use LLM-Guard if importable, else regex heuristics. Tests inject fakes.
    scanner_backend: str = "auto"  # auto | llm_guard | heuristic | off
    injection_block_threshold: float = 0.75
    # If Docker is off, network tools are disabled rather than silently unsandboxed.
    # The user can consciously flip this in Settings (stored in DB, mirrored here).
    allow_unsandboxed_network_tools: bool = False

    # (ms_client_id removed with MS Graph. It was a placeholder that had to be
    # filled before shipping and never was, which is what made the Graph path
    # unreachable in practice.)

    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "arthur.db"

    @property
    def chroma_path(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def undo_dir(self) -> Path:
        """Snapshots of files as they were before each apply.

        In the data dir, never in the user's project: an undo file inside the
        folder being edited would show up in their diffs, their search results
        and their commits — Arthur's safety net is not their code.
        """
        return self.data_dir / "undo"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.log_dir.mkdir(parents=True, exist_ok=True)
    return s
