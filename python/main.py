"""Backend entrypoint — what Electron spawns (and PyInstaller freezes).

Port/token/data-dir arrive via ARTHUR_* environment variables set by
electron/backend.js. Run standalone for development:

    cd python && uvicorn main:app --port 8756
    (an ARTHUR_AUTH_TOKEN=... env var or .env makes curl testing easier)
"""

from __future__ import annotations

import multiprocessing

import uvicorn

from core.app import create_app
from core.config import get_settings
from core.logging_setup import setup_logging

settings = get_settings()
setup_logging(settings)
app = create_app(settings)


if __name__ == "__main__":
    # PyInstaller on Windows re-executes the binary for child processes;
    # without freeze_support a frozen app can fork-bomb itself.
    multiprocessing.freeze_support()
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
