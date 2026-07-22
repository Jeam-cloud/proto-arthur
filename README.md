# ARTHUR

A personal AI assistant that runs entirely on your own computer.

Arthur is a local-first desktop app (Electron + Python/FastAPI) that chats with you, remembers context across conversations, and can take real actions on your behalf — researching the web, managing email and calendar, working with code, analyzing market data, and controlling your desktop — each capability scoped, confirmed, and where possible sandboxed.

Models run locally through Ollama: no cloud account, no subscription, nothing leaves your machine by default. Memory is split between ChromaDB (semantic recall) and SQLite (exact history), and everything Arthur remembers is visible, editable, and deletable in Settings.

## Development quickstart

Backend (Python 3.11+):

    cd python
    python -m venv .venv && .venv\Scripts\activate
    pip install -r requirements-dev.txt
    pytest tests/            # 68 tests, no Ollama/Docker needed
    uvicorn main:app --port 8756

App (Node 20+):

    npm install
    npm run dev              # Vite + Electron, spawns the backend automatically

Ship:

    npm run dist             # vite build + PyInstaller + NSIS installer -> release/

## Layout

    electron/     main.js, preload.js, backend manager, tray, updater
    ui/           React + Zustand renderer (Vite)
    python/
      core/       config, db, Ollama client, chat service, personas, API
      security/   gateway, scanners, approvals, audit log, OS-vault access
      memory/     embeddings, vector store, fact extraction
      agent/      tool-calling loop + registry (privilege separation)
      tools/      research, finance, email/calendar, coding, computer control
      sandbox/    Docker runner + container images
      voice/      faster-whisper transcription
      tests/      pytest suite (gateway, agent loop, memory, paths, API)
    docs/         ARCHITECTURE.md (why each choice), SECURITY.md (threat model), BUILD.md
    site/         download page

## What Arthur is not

Not a hosted service — there are no servers. Not a frontier-model replacement — local models trade raw capability for privacy and zero recurring cost, and that trade is the point. Not a black box — the memory store and the security event log are user-visible surfaces, not internals.

## Privacy model

Everything runs locally by default. Features that need the internet (research, email, finance) are off until configured, run in locked-down Docker containers where possible, and every external byte they bring back is scanned and marked untrusted before the model sees it. Optional BYOK routes a single request to a hosted model — opt-in per message, chat-only, keys in the OS credential vault. Full detail: `docs/SECURITY.md`.
