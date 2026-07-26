# Arthur — Feature Spec for UI Design

Arthur is a local-first AI assistant desktop app (Electron). No account, no subscription — everything runs on the user's machine via Ollama, with optional opt-in cloud fallback. Design direction: **sleek, modern, dark**.

This doc lists every screen and interactive element currently in the app, for a full interface redesign. It does not prescribe visuals — that's the design task.

---

## 1. App shell

Three-pane desktop layout: a persistent left sidebar, a main pane (chat or settings), and two global overlays (approval modal, toast stack) that can appear on top of anything.

- **Sidebar**: logo/wordmark ("ARTHUR — local assistant"), "New chat" button, scrollable list of past conversations (title + delete-on-hover), a Settings entry pinned at the bottom.
- **Status banner**: a single strip pinned above the main content, worst-problem-first. Shows only one of: "Ollama isn't running — chat is paused" (blocking, red, with Retry), or "Docker is off — research/finance/code disabled, chat still works" (dismissible warning). Silent when everything's fine.
- **Global keyboard shortcuts**: Ctrl+N new chat, Ctrl+, settings, Esc back to chat, Ctrl+Shift+A summons the Quick Widget from anywhere (even outside the app).
- **Boot screen**: shown while the local backend spins up.
- **Onboarding wizard**: replaces the shell entirely until setup is complete (see §5).

## 2. Chat view

- **Empty/no-conversation state**: headline + subtext ("Your assistant, entirely on your machine — no cloud account, no subscription, no data leaving your computer by default") and a "Start a conversation" CTA.
- **Empty chat (conversation exists, no messages yet)**: headline, subtext, and 2-3 clickable suggestion chips ("What can you do?", "Remember that I prefer short, direct answers", "Summarize what's on my screen").
- **Message list**: chat bubbles, user vs. assistant styling. Assistant messages render Markdown (code blocks, etc.).
  - A small "memory chip" appears above messages when recalled memories informed the reply ("using 2 memories").
  - A "cloud · openai" / "cloud · anthropic" badge appears on messages answered by a BYOK cloud model instead of local.
  - Partial/stopped-early replies get a subtle "— stopped early" note.
  - Error bubbles for security blocks or failures, styled distinctly (red accent).
- **Live activity feed** (inside the assistant's bubble while it's working): a small stack of status lines with icons — spinner while running, checkmark on success, X on failure, shield icon when something was flagged. e.g. "Researching… → read 4 pages". This is a core transparency feature — the user should always see what tools Arthur is invoking in real time.
- **Streaming cursor**: blinking cursor at the end of in-progress assistant text.

### Composer (bottom bar, always visible in chat)

- **Mode chips** — a row of 7 selectable pills: **General, Research, Code, Email, Finance, Computer, Design**. This is a security control made visible: the mode picked determines which tools the model is even allowed to call. Disabled chips show why in a tooltip (e.g. "Needs Docker running", "Set up email in Settings → Integrations"). Selecting "Computer" mode changes the input placeholder to a warning about screen/mouse/keyboard control.
- Typing an email-like request in General mode auto-switches the chip to Email mode with a toast notification ("Switched to Email mode for this request").
- **Model chip / menu** ("Claude-bar style"): click to open a panel showing, for the current mode: recommended models ranked best-first (each tagged installed/fits-but-missing-with-inline-download/too-big-for-this-PC-with-reason), other installed models, and an "Auto" option that defers to the Settings default. Download progress shows inline as a percentage.
- **Text input**: auto-growing textarea, Enter to send / Shift+Enter for newline.
- **Push-to-talk mic button**: hold to record, transcribes on release; shows a recording state and a transcribing spinner. Denied-permission state has its own tooltip.
- **Send / Stop button**: swaps to a stop icon while a reply is streaming.
- **Hint line** under the composer, mode-dependent (shortcuts reminder, or a Computer-mode safety warning: "each action needs your OK — slam the mouse into the top-left corner to abort instantly").

## 3. Human-approval modal (global overlay)

Appears whenever Arthur wants to do something consequential — interrupts whatever screen the user is on. Core trust mechanism of the app.

- Icon + plain-language question framed around the specific action, not a generic "Allow?": "Send this email?", "Add this to your calendar?", "Save this file?", "Run this code? (isolated sandbox, no internet)", "Open this app?", "Click your mouse here?", "Type this for you?", "Press this shortcut?" — with a sensible fallback for unknown/future tools.
- Body shows the **exact, validated arguments** — never a JSON dump, never the model's paraphrase. Type-specific layouts: an email renders as To/Cc/Bcc/Attachments/Subject/Body; code/file writes render as a labeled code block; typed text renders as a quoted string; everything else renders as clean label→value rows.
- 120-second countdown ("cancels itself in 47s") — auto-denies if ignored.
- Two buttons: a clear affirmative action verb (e.g. "Send it", "Run it", "Click") and "No, don't".

## 4. Toast notifications

Global, transient, stacked. Used for confirmations, errors, and mode-switch notices throughout the app (success/info/error variants seen elsewhere in this doc).

## 5. Onboarding wizard (first run)

Step-dot progress indicator, 5 stages, each with a re-check/refresh action and no dead ends:

1. **Welcome** — brand intro, "no account, no subscription, nothing leaves your machine" promise, "Get started" CTA.
2. **Ollama check** — detects whether Ollama is installed/running; if not, a "Get Ollama" external link + "Check again" button; continue is disabled until detected.
3. **Model picker** — reads real hardware (RAM, CPU cores, GPU + VRAM if present) and recommends a chat model + the embedding model (nomic-embed-text) needed for memory. Inline download with a progress bar and percentage. "Continue" disabled until both are installed.
4. **Docker check (optional/skippable)** — explains Docker sandboxes the riskier tools (research, code execution, finance); without it those stay disabled but chat/memory/computer-control still work. External download link + recheck, or "Skip for now."
5. **Done** — confirms setup, surfaces the Ctrl+Shift+A quick-widget tip and points to Settings → Memory for transparency, "Open Arthur" CTA.

## 6. Quick Widget (Ctrl+Shift+A, global hotkey)

A separate, small, frameless, always-on-top window — "Spotlight-style" bar, distinct from the main app window.

- Titlebar: logo, "Arthur — quick ask" label, "open full app" button, close (Esc) button.
- Body: single streamed answer (Markdown-rendered), or a hint prompt before first use ("Type, or hold the mic and just talk — I'll answer as soon as you let go").
- Footer: single-line input, push-to-talk mic (hands-free: speaking with an empty box auto-sends), send button.
- One question in, one answer out, then escape hatch to the full app — this is intentionally minimal, not a full chat.

## 7. Settings

Accessed via sidebar or Ctrl+,. Left-hand tab nav (Back button + 6 tabs) + a content pane on the right.

### 7.1 General
- Text size slider (85%–130%, live percentage readout).
- Coding workspace folder picker (native OS folder dialog) — Code mode is sandboxed to read/write only inside this folder, called out explicitly.
- Memory on/off toggle switch.
- Global hotkey reference (display-only: "Ctrl+Shift+A").

### 7.2 Models
- "This machine" card: RAM, CPU cores, GPU/VRAM (or "no NVIDIA GPU detected"), recommended chat model.
- Ollama-not-running warning card (red) if applicable.
- List of installed models as cards: name, parameter size, disk size, "Use" button / "default" pill for the active one. Refresh action + a hint about `ollama pull <name>` for installing more.
- **Model-per-mode table**: one row per mode (General/Research/Code/Email/Finance/Computer/Design), each with a dropdown to assign a specific installed model or "Default." Explains override precedence (composer chip > this setting > global default).

### 7.3 Personas
- Row of persona chips (switchable, active one marked with a dot) + "New" button.
- Editor for the selected persona: name field (locked for built-ins), system-prompt textarea, and a list of few-shot example pairs (user says / Arthur replies), up to 8, each individually removable, "Add example" button.
- Actions: Save, "Make active" (if not already active), Delete (non-built-ins only).
- Explains why few-shots matter: "steer small local models far more reliably than instructions alone."

### 7.4 Memory
- Inline "teach Arthur something" text field + Add button.
- List of remembered facts as cards: text, a category pill, an enabled/paused toggle switch, inline edit (pencil → text field with confirm/cancel), delete button. Dimmed when disabled.
- Empty state: "Nothing remembered yet."
- Framed as the app's transparency promise: everything remembered is visible, editable, deletable — nothing hidden.

### 7.5 Security
- **Message scanner card**: current backend status pill (e.g. "heuristic" flagged as a warn-state, others as ok), a 3-way mode selector (Standard: blocks suspicious messages / Relaxed: warns only, never blocks / Off: no scanning), with a cautionary note when Off is selected. Also clarifies that external content (web pages, emails) is *always* scanned regardless of this setting, and risky actions always require approval independent of this toggle.
- **Tool sandbox (Docker) card**: status pill (active/off).
- **"Allow research without the sandbox" toggle**: off by default, orange warning copy explaining the tradeoff.
- **Event log**: scrolling list of timestamped security events, each with a severity pill (info/warning/blocked), an event kind, and a detail string. Refresh and "Clear" actions. Empty state: "No security events yet — that's a good sign."

### 7.6 Integrations
- **Email card**: connect via address + app-password (Gmail/Yahoo/iCloud-style), inline "Connect" that actively verifies credentials before confirming ("Verifying…"), shows the connected address as a pill once verified, "Disconnect" removes the password from the OS vault. Copy clarifies sending always shows a draft first.
- **Microsoft 365 (OAuth) card**: alternate to app-password email, also unlocks calendar tools. Connect/Disconnect buttons, "connected" pill, note about personal Outlook accounts requiring this route.
- **Tavily (web research) card**: single API-key field, "configured" pill once set, powers Research mode's web search.
- **BYOK cloud models section** (OpenAI, Anthropic): explicit framing that this is opt-in per message, chat-only (cloud models never get tool access), and that replies get a visible cloud badge. Each is a simple password-style key field with Save/Remove and a "configured" pill.
- General framing note at the top: keys go into the OS credential vault, are write-only (the API never returns a stored key), and never enter tool sandboxes.

---

## Cross-cutting UI patterns to preserve in the redesign

- **Mode chips** (pill/segmented-button style) recur in the composer, Models tab, and Personas tab — should be a single reusable component.
- **Status pills** (ok / warn / off / cat) recur throughout Security, Integrations, Models — small colored badges communicating state at a glance.
- **Cards with a title + sub + trailing action/pill** are the dominant list-item pattern across every settings tab.
- **Toggle switches** for binary settings (memory on/off, scanner relaxed, memory-item enabled).
- **Progress bar + percentage** for model downloads (onboarding and composer's model menu both need this).
- **Toast stack** for transient feedback.
- Everything currently uses a dark theme with CSS variables (`--accent`, `--red`, `--green`, `--orange`, `--dim`, `--mid`, `--text`, `--surface`/`--surface2`/`--surface3`, `--border`) — the redesign should define a cohesive dark palette from scratch rather than reusing these, but the *number* of semantic states (accent, danger, success, warning, three surface elevations, border, three text-emphasis levels) is a useful inventory of what the UI actually needs.

## Design goals to communicate to Claude Design

- **Trust through visibility**: nothing Arthur does is hidden — tool activity, security events, memory, and approvals are all first-class, always-visible UI, not buried in logs.
- **Privilege separation should feel deliberate, not bureaucratic**: mode selection and approval dialogs are safety mechanisms disguised as normal product interactions — they should feel quick and natural, not like friction.
- **Local-first identity**: the product's core pitch (no cloud, no subscription, runs on your machine) should come through in tone/copy treatment, not just be stated once in onboarding.
- Voice input (push-to-talk) is a first-class input method alongside typing, in both the main composer and the quick widget.
