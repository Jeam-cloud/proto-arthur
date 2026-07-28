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
    approval_timeout_s: float = 120.0  # unanswered confirmation = denied
    tool_output_max_chars: int = 16_000

    # --- security ---
    # auto: use LLM-Guard if importable, else regex heuristics. Tests inject fakes.
    scanner_backend: str = "auto"  # auto | llm_guard | heuristic | off
    injection_block_threshold: float = 0.75
    # If Docker is off, network tools are disabled rather than silently unsandboxed.
    # The user can consciously flip this in Settings (stored in DB, mirrored here).
    allow_unsandboxed_network_tools: bool = False

    # --- integrations ---
    # Multi-tenant Azure app registration for MS Graph. Ship your own client id;
    # it is public by design (PKCE flow needs no secret).
    ms_client_id: str = "REPLACE-WITH-YOUR-AZURE-APP-CLIENT-ID"

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


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.log_dir.mkdir(parents=True, exist_ok=True)
    return s
