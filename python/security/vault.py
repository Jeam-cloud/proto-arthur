"""API-key storage in the OS credential vault.

WHY keyring: on Windows this is the Credential Locker (DPAPI-encrypted, tied
to the user account), Keychain on macOS. Keys never live in a config file, a
DB row, or an env var a child process could inherit — sandboxed tool
containers receive data, never credentials. The one exception is Tavily,
whose key is used by the backend itself (in-process HTTP call), not passed
into the research container.

The API returns only `configured: true/false` — a stored key can be replaced
or deleted through the UI but never read back out.
"""

from __future__ import annotations

import logging

import keyring

log = logging.getLogger(__name__)

_SERVICE = "Arthur"
KNOWN_KEYS = ("tavily", "byok_openai", "byok_anthropic", "byok_gemini", "email_password")


class SecretsVault:
    def set(self, name: str, value: str) -> None:
        if name not in KNOWN_KEYS:
            raise ValueError(f"Unknown secret name: {name}")
        keyring.set_password(_SERVICE, name, value)
        log.info("secret '%s' stored in OS vault", name)

    def get(self, name: str) -> str | None:
        if name not in KNOWN_KEYS:
            return None
        try:
            return keyring.get_password(_SERVICE, name)
        except Exception as e:  # locked keychain, headless session, ...
            log.warning("vault read failed for '%s': %s", name, e)
            return None

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(_SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass  # already gone — deleting twice is fine

    def status(self) -> dict[str, bool]:
        return {name: self.get(name) is not None for name in KNOWN_KEYS}
