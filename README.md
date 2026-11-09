# Arthur

A prototype of Arthur, a personal agentic AI assistant that runs on your own computer.

Arthur is a desktop app. You chat with a local AI model, and it can remember things across conversations and use tools: search the web, write a research paper, read and edit code, look up stock prices, send email, and click and type on your desktop. Risky actions ask for your approval first.

<img width="1919" height="1079" alt="image" src="https://github.com/user-attachments/assets/0bb33814-5638-48e2-a14e-9147d354502b" />
<img width="1206" height="820" alt="image" src="https://github.com/user-attachments/assets/3728dc2c-0736-4f98-a46e-271075d4745e" />
<img width="1826" height="1078" alt="image" src="https://github.com/user-attachments/assets/6c049979-5f93-433c-b53e-6ca7807577b2" />


## How it works

- **The app** is Electron with a React interface.
- **The brain** is a Python (FastAPI) backend that the app starts for you.
- **The AI models** run locally through [Ollama](https://ollama.com). No account or subscription needed.
- **Memory** is stored in SQLite (exact chat history) and ChromaDB (search by meaning). You can view, edit, and delete it in Settings.

## What it can do

| Area | Tools |
|---|---|
| Chat | Streaming replies, personas, file attachments, voice input (faster-whisper) |
| Research | Web search (Tavily), multi-step research runs, paper export to .docx and .pdf |
| Code | Read, list, search, write, edit, patch and delete files, with undo; run Python in a sandbox |
| Finance | Stock quotes and history (yfinance), portfolio tracking, CSV import |
| Email | Send, list, and search |
| Desktop | Open apps, take screenshots, click, type, press keys |

There is no calendar support yet.

## Safety

- Internet features are off until you set them up.
- Risky tools run inside Docker containers when Docker is available.
- Text that comes back from the web is scanned for prompt-injection and marked as untrusted before the model sees it.
- Destructive actions need your approval, and they are written to an audit log.
- API keys are stored in your OS credential vault, never in files.
- You can optionally send a single message to a hosted model with your own API key. This is off by default.

Details: [`docs/SECURITY.md`](docs/SECURITY.md)

## Running it

You need Python 3.11+, Node 20+, and Ollama. Docker is optional but recommended.

Backend:

    cd python
    python -m venv .venv
    .venv\Scripts\activate            # Windows (macOS/Linux: source .venv/bin/activate)
    pip install -r requirements-dev.txt
    pytest tests/

App (this also starts the backend):

    npm install
    npm run dev

Build a Windows installer:

    npm run dist                      # output goes to release/

More detail: [`docs/BUILD.md`](docs/BUILD.md)

## Tests

- Backend: 648 tests, all passing. They use fake versions of Ollama and Docker, so they run in seconds without either installed.
- UI: a small Vitest suite (`npm run test:ui`).
- CI runs linting, both test suites, and a UI build on every push.

## Known limits

- **Local models are weaker than cloud ones.** That is the trade for privacy and no monthly cost.
- **First run downloads a lot.** AI models are 2 to 8 GB, and the injection scanner is about 700 MB.
- **The tests don't cover the real thing.** They prove the logic works with fakes, not that a real model behaves well.
- **The installer is not code-signed.** Windows SmartScreen will warn you.
- **Stock data can break.** yfinance uses an unofficial Yahoo API.
- **Code scanning is limited on Windows.** CodeShield doesn't install there, so it falls back to simpler checks plus the sandbox.

## Project layout

    electron/   desktop shell: window, tray, auto-updater, starts the backend
    ui/         React interface (Vite + Zustand)
    python/
      core/     settings, database, Ollama client, chat, API
      agent/    the loop that lets the model call tools
      tools/    research, finance, email, coding, desktop control
      security/ approvals, scanners, audit log, key vault
      memory/   embeddings and the vector store
      sandbox/  Docker runner and images
      research/ research engine and paper export
      voice/    speech-to-text
      tests/
    docs/       architecture, security, and build notes

## License

MIT
