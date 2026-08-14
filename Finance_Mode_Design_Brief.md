# Arthur — Finance mode design brief

**For:** the Arthur prototype mockup (same file family as `Arthur.dc.html` / `Rail Options.dc.html`)
**Status:** Finance mode currently has **no UI at all.** It has four tools and a prompt note; every answer arrives as JSON that the model narrates in prose. This brief is for the first real screen.
**Date:** 14 Aug 2026

---

## 1. What this mode is, and what it is not

Arthur is a local-first assistant, not a trading terminal. Finance mode should not try to beat TradingView at charting — it cannot, and the audience that wants that is already served.

**The thing only Arthur can do:** you ask a question in plain language and get a chart, a comparison, or an explanation back — and your watchlist and holdings never leave the machine.

Design for a **retail investor checking in**, not a day trader. Research is consistent that watchlists, quotes and news dominate actual usage; advanced charting is a minority audience and its complexity is a barrier for everyone else.

**Emotional register matters here more than in any other mode.** Investors looking at losses make more impulsive decisions, and dashboards amplify that. So: no flashing, no auto-refresh that makes numbers twitch, no oversized red. Calm, factual, quiet. Red and green should be legible but never loud — and never the *only* channel carrying the information (see §7).

---

## 2. The hard constraint the design must carry: staleness

**This is the single most important thing to get right.**

The data is Yahoo Finance via `yfinance`. It is **~15 minutes delayed**, and Arthur caches responses for **another 15 minutes**. A displayed price can therefore be **up to 30 minutes old** while looking perfectly current.

Every price surface must carry its age. Suggested treatment:

- A persistent, quiet line in the header: `Delayed ~15 min · updated 3:42pm`
- On a card that is serving from cache and is near expiry, the timestamp is the honest signal — do not hide it
- A manual **Refresh** control. Do NOT design an auto-refreshing ticker: it implies real-time, and it makes numbers move while someone is reading them

If a mock shows a price with no timestamp anywhere near it, that mock is wrong.

There is no intraday depth to lean on either: 1-minute bars exist only for the last 7 days, and *any* sub-daily interval only for the last 60. Do not design a candlestick/orderbook/level-2 aesthetic — the data underneath is daily closes.

---

## 3. What data actually exists right now

Design against these fields. **Do not invent data** — if a mock needs a field not on this list, flag it, because it means backend work, not just design.

**`quote` returns, per symbol:**
- `price`, `currency`
- `day_high`, `day_low`
- `year_high`, `year_low`
- `market_cap`

Note: there is currently **no company name, no logo, no change %, and no previous close.** Change % has to be derived, and name/logo need a new field. Assume name will exist; call it out as a dependency.

**`history` returns, per symbol:**
- A list of `{ date, close }` — **daily closes only**, max 120 points
- Periods: `5d`, `1mo`, `3mo`, `6mo`, `1y`, `5y`

No open/high/low per bar, no volume. **A candlestick chart is not possible.** Design line and area charts.

**`quick_search`** (shared with Research mode) returns web results — this is what powers news and "why did it move".

---

## 4. Screens and states to design

### 4a. Empty state — no watchlist yet
The mode's first-run face. Should invite one action, not explain the whole mode.
- Heading, one line of purpose
- A symbol input with a real example placeholder (`AAPL`, `NVDA` — not "e.g. ticker")
- 3–4 suggestion chips to add common symbols in one click
- One quiet line stating the delay and that nothing leaves the machine

### 4b. Watchlist — the default view
**This is the most important screen in the brief.** It is what the mode opens to.

Per row:
- Symbol (mono) and company name
- Current price + currency
- Change % — the only place colour is used
- A small **sparkline** (~72×24), 1-month daily closes
- Row click → detail; hover → remove/reorder affordance

Follow the dashboard convention the research supports: **≤5–6 elements in the initial view**, largest numbers at the top. Do not build a 12-column data grid.

Also design:
- **Empty-but-loading** (skeleton rows, not a spinner)
- **A row that failed to fetch** while its neighbours succeeded — one bad ticker must not blank the list

### 4c. Symbol detail
- Large price, change, currency, timestamp
- **Main chart**: line/area, with period selector matching the real options exactly — `5d · 1mo · 3mo · 6mo · 1y · 5y`. No `1d`, no `max`, no custom range; those don't exist
- Day range and 52-week range — a range bar with a marker reads far better than four numbers
- Market cap
- **News list** below the chart (from `quick_search`)

### 4d. Comparison
"Compare NVDA and AMD over 6 months."
- **Normalised percentage overlay**, both starting at 0% — not two price axes. Two differently-scaled price lines on one chart is the classic misleading finance chart
- Legend with each symbol's return over the window
- 2–4 symbols max

### 4e. Explain-the-move — *the differentiator*
The one thing a normal tracker cannot do. Give this real design attention.
- Quote + change at the top
- Arthur's short written explanation
- **The sources it used, cited and clickable** — this must look like Research mode's evidence treatment, not like an unsourced claim. Reuse that visual language deliberately

### 4f. Portfolio (design last, ship last)
- Holdings: symbol, quantity, cost basis, current value, unrealised P/L
- Top row: total value, total P/L, day change — as large numbers per the KPI convention
- Needs an add/edit-holding form
- One line making the privacy point explicit: this is stored locally and never sent anywhere

### 4g. Failure states — do not skip these
- **Circuit breaker open**: after 3 consecutive failures Arthur stops trying for 10 minutes. Needs copy that says data is unavailable and roughly when it will retry — not a generic error
- **Docker off**: Finance tools run in a container. Mode is disabled; the rail already shows an amber dot and a reason
- **No network**
- **Unknown ticker**

---

## 5. Where it lives in the app

Finance is one of the seven modes in the 96px labelled rail. It has no dedicated UI today, so this is greenfield — but it must feel like the same application.

Two layout precedents to follow:
- **Code mode** puts a persistent context bar at the top (the workspace folder) and a panel above the composer. Finance's watchlist could occupy the same right-hand position as Code's file tree.
- **Research mode** has a full-width takeover for its run/report stages. Finance's portfolio view may want the same.

The composer stays. This is a chat mode — the panels are context, and typing a question is always the primary action.

---

## 6. Design system

Use the existing tokens verbatim (`Arthur.dc.html` `renderVals()`):

```
--bg      rgb(26,28,33)     main pane, the lit surface
--bg2     rgb(18,20,24)     rail, sidebar, cards
--muted   rgba(244,246,250,.05)
--emph    rgba(244,246,250,.09)
--border  rgba(244,246,250,.08)
--text    #f4f6fa
--tmut    #9aa3b2
--accent  #f4f6fa           (monochrome — the purple was dropped)
--green   #6ee7b7
--red     #fca5a5
--yellow  #fcd34d
--ease    cubic-bezier(.32,.72,0,1)
--mono    for tickers, prices, dates, and any aligned number
```

**The palette is deliberately monochrome.** Green and red are the *only* hues that earn their place here, and only for direction. Do not introduce a colour per symbol or a rainbow palette on the comparison chart — differentiate lines by weight, dash, and label instead.

Existing patterns worth reusing rather than reinventing:
- The **activity feed** card (`✓ Listed .` with a right-aligned metric in mono) for tool steps
- The **receipt strip** above the composer for "last action + undo"
- Research mode's **evidence/citation** treatment for the news and explain-the-move sources
- Range/period selectors: match the **segmented control** in Settings → Security

---

## 7. Accessibility and honesty rules

1. **Never encode direction in colour alone.** Every change % carries a sign or arrow as well. Roughly 1 in 12 men cannot separate red from green reliably, and this is the screen where confusing up with down costs the most.
2. **Every price shows its age** (§2).
3. **Charts need an accessible summary** — a text line stating the range and change, not just an SVG.
4. **Focus states**: the global `:focus-visible` ring applies; don't design controls that would clip it.
5. **`prefers-reduced-motion` is honoured app-wide** — any chart draw-in animation must degrade to instant.
6. Currency is always shown. Not every ticker is USD.

---

## 8. Explicitly out of scope

Do not design these — the data or the platform does not support them, and mocking them creates expectations:

- Candlestick / OHLC charts (no open-high-low data)
- Real-time or streaming prices (delayed feed)
- Order entry, trading, or anything that moves money — **Arthur will never place a trade**
- Price alerts (needs a background scheduler Arthur does not have yet)
- Options chains, analyst ratings, earnings calendars (not wired up)
- Crypto (untested through this path)

---

## 9. Priority for the mock

If only part of this gets designed, this is the order:

1. **Watchlist** (4b) — the default view, the dominant use case
2. **Symbol detail with chart** (4c) — the "live graphs" ask
3. **Explain-the-move** (4e) — the differentiator
4. **Comparison** (4d)
5. **Empty + failure states** (4a, 4g)
6. **Portfolio** (4f)

---

## 10. Open questions for design

1. Does the watchlist live in the **right-hand panel** (like Code's file tree) or as a **full view** (like Research)? Both fit; the answer decides whether chat and watchlist are ever visible together.
2. Is the symbol detail a **panel, a modal, or an inline card in the transcript**? Inline is most chat-native and most novel; a panel is more conventional.
3. When Arthur answers "how has NVDA done this year", does the chart render **inline in the reply** or open the detail view? Inline keeps the conversation the spine of the app.
4. How much does Finance mode show **before** you type anything? Code mode shows a folder and a file tree. Finance could open to the watchlist — meaning the mode has a resting state that is useful on its own.
