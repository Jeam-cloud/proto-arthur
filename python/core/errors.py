"""One error hierarchy for the whole backend.

WHY: FastAPI turns any ArthurError into a consistent JSON body
{"error": {"code", "message"}} via a single exception handler in app.py.
The UI switches on `code` to show the right recovery action ("Start Ollama",
"Install Docker", ...) instead of parsing prose. Adding an error means adding
a subclass — not another try/except at every call site.
"""

from __future__ import annotations


class ArthurError(Exception):
    code = "internal_error"
    http_status = 500

    def __init__(self, message: str = "", *, detail: dict | None = None):
        super().__init__(message or self.__class__.__doc__ or self.code)
        self.message = message or (self.__class__.__doc__ or self.code)
        self.detail = detail or {}


class OllamaUnavailableError(ArthurError):
    """Ollama is not running. Start Ollama and try again."""

    code = "ollama_unavailable"
    http_status = 503


class ModelNotFoundError(ArthurError):
    """The requested model is not installed in Ollama."""

    code = "model_not_found"
    http_status = 404


class SecurityBlockError(ArthurError):
    """The security gateway blocked this content."""

    code = "security_blocked"
    http_status = 400


class DockerUnavailableError(ArthurError):
    """Docker is not running, so sandboxed tools are disabled."""

    code = "docker_unavailable"
    http_status = 503


class PathTraversalError(ArthurError):
    """Path escapes the configured workspace folder."""

    code = "path_traversal"
    http_status = 400


class ToolNotAvailableError(ArthurError):
    """This tool is not granted for the current task mode."""

    code = "tool_not_available"
    http_status = 403


class IntegrationNotConfiguredError(ArthurError):
    """The integration needs to be connected in Settings first."""

    code = "integration_not_configured"
    http_status = 400


class OfflineError(ArthurError):
    """No internet connection available for this feature."""

    code = "offline"
    http_status = 503


class VoiceError(ArthurError):
    """Speech-to-text failed."""

    code = "voice_error"
    http_status = 500


class NotFoundError(ArthurError):
    """Resource not found."""

    code = "not_found"
    http_status = 404
