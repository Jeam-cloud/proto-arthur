# Arthur — Research Mode: Design Handoff

Brief for designing Arthur's Research mode. Self-contained: everything needed
to produce screens that drop into the existing app without restyling.

---

## 1. Product context

**Arthur** is a local-first AI assistant. Electron desktop app, Windows-first.
Every model runs on the user's own machine via Ollama. No cloud account, no
subscription, no data leaving the computer by default.

The app has seven **modes** (General, Research, Code, Email, Finance, Computer,
Design). Until now a mode only swapped which model and tools were active — every
mode looked like the same chat window. That is the problem this work fixes.

**Research mode should stop looking like a chat and start looking like a
workspace for an investigation.**

---

## 2. Existing visual system

Match these exactly. Do not introduce new colors or radii.

### Palette (dark, and dark only for now)

| Token | Value | Used for |
|---|---|---|
| `--bg` | `#1a1c21` | Main working pane. **Lighter than the chrome.** |
| `--bg2` | `#121418` | Rail, sidebar, cards, modals. The darker frame. |
| `--muted` | `rgba(244,246,250,0.05)` | Inputs, pills, resting card surfaces |
| `--emph` | `rgba(244,246,250,0.09)` | Hovered rows, active nav, tracks |
| `--border` | `rgba(244,246,250,0.08)` | All hairlines |
| `--text` | `#f4f6fa` | Primary text |
| `--tmut` | `#9aa3b2` | Secondary text, icons at rest |
| `--accent` | `#f4f6fa` | Primary fills (buttons, progress) |
| `--inv` | `#0b0d12` | Text on top of `--accent` |
| `--green` | `#6ee7b7` | Good / supported / installed |
| `--yellow` | `#fcd34d` | Caution / thin / needs review |
| `--red` | `#fca5a5` | Error / destructive / contradiction |
| selection | `rgba(253,224,71,0.35)` | Yellow text selection |

Two things that surprise people, both intentional:

1. **The main pane is lighter than the sidebar.** Chrome recedes, work surface
   is lit. Do not invert this.
2. **Surfaces are translucent white, not solid grey.** A card on `--bg2` reads
   differently from the same card on `--bg`, automatically.

### Type
- Sans: **Geist** (400 / 500 / 600). Body 13.5–15px, section headers 15px,
  page titles 21px, letter-spacing tightens as size grows (−0.01 to −0.025em).
- Mono: Cascadia Code / Consolas. Used for model names, URLs, numbers, code.
- Micro-labels: 10–10.5px, weight 600, UPPERCASE, letter-spacing 0.07em.

### Shape
- Cards 13px radius · modals 16px · buttons and inputs 9–10px · pills 7px ·
  small chips 5–7px
- Buttons: 38px tall default, 34px compact, 30–32px small
- Icons: Lucide, 1.7–1.8 stroke weight, 13–20px

### Existing chrome (already built, shown for context)
- **62px icon rail** far left: logo, then mode icons, then Model hub + Settings
  pinned to the bottom
- **260px sidebar** (resizable 200–420px): New chat / Search / New folder, then
  Pinned, Folders, Recents
- **Main pane** fills the rest

---

## 3. What Research mode actually is

The unit of work changes from **a message** to **an investigation**.

An investigation is a long-lived object with its own lifecycle. It can run for
minutes, do many steps unattended, and it leaves behind artifacts that outlive
the conversation: a report document and a library of sources.

The pipeline, which the UI must make legible:

```
Question → Plan (3-6 sub-questions) → [per sub-question: search → read →
extract] → Gap check → second pass on thin areas → Synthesize report
```

**The plan is visible and editable before it runs.** This is deliberate and
should be treated as a feature, not a speed bump — it is the user's steering
wheel, and it is also how we work around small local models being poor at
long autonomous planning.

---

## 4. Screens to design

### 4.1 — Research home (empty state)

Replaces the chat empty state when Research mode is selected.

Not a chat box. A **brief composer**:

- Large question field. Placeholder should model a good research question, not
  a chat message.
- **Depth** — segmented control: `Quick` / `Standard` / `Exhaustive`. Each shows
  its real budget underneath (e.g. "~8 sources · about 2 min").
- **Sources** — multi-select chips: `Web` `Academic papers` `News` `Docs`
- Collapsed "Advanced": include-domains / exclude-domains fields
- Primary button: **Start investigation**

Below the composer: recent investigations as rows, each with title, date,
source count, and a status dot. These are *not* chat titles — show them as
research artifacts.

### 4.2 — Plan review

The moment after the model decomposes the question.

- Original question at top, de-emphasised, editable on click
- **3–6 sub-question rows**, each with: drag handle, inline-editable text,
  delete. Plus an "Add sub-question" ghost row at the end.
- A budget line: estimated sources and time, updating as rows are added/removed
- Primary: **Run investigation** · Secondary: **Back**

Design note: this must feel like reviewing a colleague's proposed approach, not
like filling in a form. Keep it light.

### 4.3 — Run in progress ← the signature screen

Three regions:

**Left** — sidebar switches to a run list. Each run: title, status dot, elapsed
time. Active run highlighted.

**Center** — the live trace. One **lane per sub-question**, stacked. Each lane
shows its state and can expand to show detail. Lanes progress in parallel and
finish out of order.

Lane states to draw (all six):

| State | Meaning |
|---|---|
| `queued` | Not started |
| `searching` | Querying providers |
| `reading 3/7` | Fetching + extracting pages, with counter |
| `done` | Answered, with source count |
| `thin` | Found too little — will get a second pass |
| `blocked` | Paywalled / unreachable / provider failed |

Above the lanes: overall progress, elapsed time, live source counter, and a
**Stop** control. Stopping must be obviously free and non-destructive — partial
results are kept.

**Right** — the evidence panel. Sources stream in as they're found and
accumulate across the whole run. Never resets between steps.

### 4.4 — Report + live document ← the second signature screen

When synthesis begins, the center becomes the **document**.

This document is **simultaneously editable by the user and the AI.** That
collaboration needs visual language:

- The document is a stack of **blocks** (paragraph, heading, list, quote).
- A block the AI just wrote or changed gets a brief highlight that fades, plus a
  subtle attribution mark in the left margin.
- Recently AI-touched blocks offer **accept / revert** on hover.
- The block containing the user's cursor is visibly "held" — the AI will not
  write into it. Show this restraint; it builds trust.
- Inline `[n]` citation pills. Hovering one highlights the matching source card
  on the right; clicking scrolls to it.

Toolbar: export to workspace folder, copy, and a toggle to show/hide the
evidence panel.

### 4.5 — Evidence panel (detailed)

Two distinct card types:

**Web source card** — favicon, title, domain, publish date, type badge
(`news` / `docs` / `forum` / `blog`), which sub-question it served, and whether
it made it into the report. Expands to show the extracted passage.

**Academic paper card** — visually distinct from web. Authors, year, venue,
citation count, DOI, PDF link. Papers come from OpenAlex / arXiv / Crossref.

Two signals unique to this product:

- **Contradiction flag** — when two sources disagree on a fact, both get marked
  and linked to each other. Design the paired state.
- **Independence grouping** — five articles rewriting one press release are not
  five sources. Group by root domain and show the collapse
  ("4 sources · 2 independent").

Panel controls: filter by type, filter to "used in report" only, sort.

### 4.6 — Confidence / uncertainty language ← the distinctive feature

Arthur runs models locally, which means it can read the model's own token-level
confidence. It knows when the model was guessing. **We surface that.**

Three levels, and they must be *quiet*:

| Level | Meaning | Suggested treatment |
|---|---|---|
| Supported | Multiple independent sources agree | No marking. Silence is the default. |
| Thin | One source, or the model was unsure | Subtle marker — dotted underline or a small margin tick |
| Unverified | Model asserted without source support | Clearer marker + "verify this" affordance |

Hard constraints:
- **Never a red banner.** A report peppered with alarms is unreadable and
  trains people to ignore it.
- Marking must not disrupt reading flow. Text stays primary.
- Clicking a marker reveals *why* it was flagged, in plain language.
- The unmarked case is the common case. If most of a report is marked, the
  design has failed.

### 4.7 — Empty, error, and degraded states

- **No Tavily API key** — research can't run. Route to Settings → Integrations.
- **Docker off** — page fetching is sandboxed in Docker; without it we fall
  back to search snippets only. Communicate the degradation honestly rather
  than failing.
- **Zero results**
- **Run failed partway** — must preserve and present whatever was gathered
- **Offline**

---

## 5. Components to spec

1. Sub-question lane (six states)
2. Web source card (collapsed + expanded)
3. Academic paper card (collapsed + expanded)
4. Contradiction pair
5. Citation pill (rest / hover / active)
6. Confidence marker (two visible levels)
7. Document block with AI attribution + accept/revert
8. Depth segmented control with budget readout
9. Run list row (with status dot states)
10. Run progress header with Stop

---

## 6. Constraints and non-goals

- **Dark theme only** for this pass.
- **Desktop only.** Assume ≥1280px wide. No mobile layouts.
- Do not restyle the existing rail or sidebar chrome — design *within* it.
- No new icon set. Lucide only.
- Copy should be **formal and declarative. No questions as labels or headings.**
  ("Install by name", not "Know the name already?") The one exception is
  consent prompts, which genuinely ask.
- Avoid the word "Cookbook" — this feature area is called the **Model hub**
  elsewhere in the app; Research mode is its own thing.

---

## 7. What success looks like

Someone opening Research mode should immediately understand that this is not a
chat window — that they are commissioning a piece of work, that they can see and
steer how it will be done, that every claim traces back to a source they can
open, and that the tool will tell them when it isn't sure.

The competitive point being made, visually: a local model that admits what it
doesn't know is more useful than a cloud model that is fluently confident about
everything.
