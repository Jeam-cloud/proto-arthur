# Arthur — Architecture and the Why of Each Choice

This is the "explain it so I actually learn it" companion to the code. Every file carries a WHY header; this doc covers the decisions that span files.

## The shape of the system

Electron (window, tray, hotkey, updates) spawns the Python backend as a child process and hands it three things through the environment: a port, a data directory, and a freshly generated auth token. The React renderer — sandboxed, no Node access — talks to the backend over `fetch` with that token, and receives streams as Server-Sent Events. The backend owns everything intelligent: Ollama calls, memory, the agent loop, security scanning, tool execution. This split means the security-critical code is all in one auditable process, and the UI could be rewritten without touching it.

## Decisions worth internalizing

**SSE over WebSockets for streaming.** Chat is strictly server→client after one request; SSE is plain HTTP (no upgrade dance, no reconnect protocol to invent) and `sse-starlette` handles disconnects and keep-alives. WebSockets earn their complexity only with bidirectional traffic — our one client→server signal ("stop") is just aborting the request.

**Fetch-parsed SSE instead of EventSource.** The browser's EventSource can't POST a JSON body or set an Authorization header — both non-negotiable here. Parsing the framing manually is ~40 lines and is unit-tested (`ui/src/api/sse.test.js`).

**Raw SQL over an ORM.** Seven tables, one process. Migrations are append-only SQL strings tracked by `PRAGMA user_version`; WAL mode plus a single write lock resolves the "database is locked" class of bugs. An ORM would add a query language between you and every bug.

**SQLite as truth, Chroma as index.** Embeddings are stored in the SQLite row *and* in ChromaDB. Vector stores corrupt and change formats far more often than SQLite; on boot Arthur rebuilds the index from SQLite if needed. Losing the index loses nothing.

**One embedding model everywhere.** nomic-embed-text via Ollama for memories and research chunks. Chroma's default would silently download its own ONNX model — one more download, inconsistent vectors, and (worse) Chroma telemetry defaults to ON; both are disabled in code.

**The agent loop is hand-rolled.** No LangChain and friends: the loop is the security boundary, and every interposition point (approval gates, output scanning, mode checks, iteration caps) must be visible in one readable file (`agent/loop.py`, ~200 lines). Frameworks optimize for capability plumbing, not audit-ability.

**Dependency injection without a framework.** `core/deps.py` builds the object graph once; tests build the same graph with fakes (`tests/conftest.py`). This is why the suite runs in 4 seconds with no GPU, no Docker, no network — and why CI is honest rather than mocked-into-meaninglessness.

**Small-model realism.** Everything assumes the model is unreliable: malformed tool args go back as correctable errors, tool-less models get a retry without tools, titles/facts parse defensively, history is trimmed by budget, few-shots live in personas because examples steer small models better than prose.

**Plain CSS + plain JS renderer.** Tailwind and TypeScript are good defaults for teams; here they'd add a build-tooling learning tax without changing the product. The upgrade path (TS on the renderer first, file by file) is the first thing to consider post-ship.

## Request lifecycles

**A chat message:** scan input → persist user turn → recall memories → assemble prompt (persona + spotlight rules + memory block + few-shots + trimmed history) → agent loop streams tokens and possibly tools → redact secrets → persist reply → background title + fact extraction. Every step is a separate SSE event type the UI renders (`core/events.py`).

**A research question:** Tavily search (key from vault, in-process) → pages fetched inside the research container → extract readable text → scan + spotlight each page → chunk (1200/150 overlap) → embed → cosine-rank against the question → top passages with [n] labels → the model synthesizes with citations.

**An email send:** the model proposes `email_send` → Pydantic validates → risk=CONFIRM → SSE `approval_required` with the real recipients/subject/body → user approves → MSAL silent token → Graph API → result summarized back into the loop.
