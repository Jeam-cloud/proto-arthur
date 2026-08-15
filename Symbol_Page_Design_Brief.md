# Arthur — Finance symbol page design brief

**For:** the Arthur prototype mockup (same file family as `Arthur.dc.html`)
**Follows:** `Finance_Mode_Design_Brief.md` §4c, and the symbol-page draft
**Built already:** watchlist panel (live), inline charts in the transcript (live)
**Date:** 14 Aug 2026

---

## 1. What this is

Click a ticker in the watchlist → its page fills the main pane.

**Not a modal.** The draft got this right and it should stay: the composer
remains live at the bottom and the watchlist stays on the right, so you can ask
about what you are looking at without dismissing it first. A modal would cover
the conversation, and the conversation is where the assistant lives — a popup
that blocks it fights the feature it exists to serve.

It replaces the transcript view the way Research mode's run screen does, and
the chat is one click away.

---

## 2. THE RULE THAT MATTERS: the page hands off, it does not host

**Every AI action closes the page and sends the question into the conversation.**
The answer streams in the transcript, not on this page.

Why this and not an inline answer box: two places that can hold a conversation
means two memories, and the user has to guess which one knows what. The
transcript is the app's spine — the symbol page is somewhere you *visit*, not
somewhere you live.

So the action row at the bottom (`Explain today's move`, `Compare with another
symbol`, `See it in my portfolio`) behaves like this:

1. click → page closes
2. the question appears in the transcript as a normal user message, with the
   symbol as context
3. Arthur answers there, and — because inline charts already ship — may draw a
   chart in the reply

Design the transition: this is a view swap, not a dismiss. Something should
carry the eye from the page back to the conversation.

---

## 3. What data actually exists

**Do not design a field that is not on this list.** If a mock needs one, flag
it — it means backend work, not styling.

### Free (already fetched for the watchlist)
- `price`, `currency`
- `previous_close`, and the derived `change` / `change_pct`
- `day_high`, `day_low`
- `year_high`, `year_low`
- `market_cap`
- `name`
- Full daily close series for `5d · 1mo · 3mo · 6mo · 1y · 5y`

### One extra call, and here it is worth paying for
`.info` is yfinance's slow, rate-limited call. It was removed from the
watchlist because it fired per symbol on every refresh and timed out the
container. A detail page is the opposite case — one symbol, user-initiated,
once — so it is appropriate here, cached hard, because none of it changes
intraday:

- `sector`, `industry`
- `longBusinessSummary` (a paragraph)
- `trailingPE`, `dividendYield`
- `fullTimeEmployees`, `website`

### News: from Tavily, NOT from yfinance
yfinance's news endpoint has a long-standing bug returning articles unrelated
to the ticker. Arthur already has Tavily wired for `quick_search` — use that.

### Does not exist, do not draw it
- Candlesticks / OHLC bars — only daily **closes** are returned
- Volume
- Intraday beyond 60 days; 1-minute data beyond 7 days
- Analyst ratings, price targets, earnings calendar
- Real-time anything

---

## 4. Fix these two from the draft

### 4a. The two percentages fight each other
The header showed **−1.79%** and the summary line **up 3.0%**. Both are
correct — one is today against previous close, the other is the whole selected
window — but they sat far apart on one screen with nothing saying so. A reader
will conflate them, and the day that matters is the day someone reads "up" and
is down.

Fix: make the window figure obviously belong to the chart. Either label it in
full (*"Over the past month: up 3.0%"*) or move it directly under the period
selector so it reads as the chart's caption rather than free-floating text.
The header % is **today**; the caption % is **the selected period**. Nothing on
screen should require working that out.

### 4b. The news was for a different company
The AMD page listed *"Nvidia slips as data-centre buyers signal a pause in
orders"* as its first item. In a mock that is placeholder content; in
production it is precisely the failure mode described above.

Design the list assuming it may be **empty** or **partially relevant**:
- an empty state that says no recent coverage was found, which is a normal
  outcome and not an error
- source, age and type on every row (`reuters.com · 2h ago · News`), as drawn
- the same numbered, citable treatment Research mode uses — this is evidence,
  and it should look like Arthur's evidence

---

## 5. Layout

Keep the draft's order. It matches the dashboard convention the research
supports: largest numbers first, trend second, detail third.

**Header** — symbol (mono) + company name; price large; currency; change in
money and percent with an arrow; `Delayed ~15 min · updated 3:42pm` right-aligned.

**Chart** — line with area fill; the six real periods; `daily closes` labelled;
the window caption beneath (see 4a).

**Stat row** — the draft's four cards are the right count: Previous close,
Market cap, Day range, 52-week range. The two ranges as bars with a marker
read far better than four loose numbers — keep that.

**Optional second stat row** — sector, industry, P/E, dividend yield. Free once
`.info` is called. Design it, mark it optional; if it makes the page feel long,
cut it rather than shrinking the ones above.

**About** — `longBusinessSummary` clamped to two lines with a "more". Never
full height by default; it is reference, not reading.

**Recent coverage** — as drawn, with the empty state from 4b.

**Action row** — the three chips. See §2 for what they do.

---

## 6. States to design

- **Loading** — skeletons in the real layout, not a spinner. The symbol and
  name are already known from the watchlist row that was clicked, so the header
  can be real while the numbers land.
- **Chart loaded, `.info` still pending** — the page must be useful before the
  slow call returns. Price and chart first; sector/P&#8203;/E fill in.
- **Unknown or delisted ticker**
- **Upstream unavailable** — Arthur stops trying for 10 minutes after 3
  consecutive failures. Copy should say roughly when it will retry, not just
  "error".
- **Docker not running** — market data runs in a container. Finance is disabled
  in the rail with an amber dot in this case, so this state may be unreachable
  from a click; confirm before designing it.
- **News empty** (4b).

---

## 7. Watchlist changes implied by the draft

The draft added **drag handles** and a **per-row ×**, with *"Click a ticker for
the full page. Drag to reorder, × to remove."* in the footer.

Reordering is **not built** — the stored list is currently insertion-ordered.
It is a small change, but flag it as work rather than styling. Design the drag
state: what the row looks like lifted, and where the drop indicator sits.

Also: the active symbol needs a selected state in the panel, since the page and
the list are now two views of one selection.

---

## 8. Design system

Tokens verbatim from `Arthur.dc.html` `renderVals()`:

```
--bg      rgb(26,28,33)     main pane
--bg2     rgb(18,20,24)     rail, sidebar, cards
--muted   rgba(244,246,250,.05)
--emph    rgba(244,246,250,.09)
--border  rgba(244,246,250,.08)
--text    #f4f6fa
--tmut    #9aa3b2
--accent  #f4f6fa          (monochrome — the purple was dropped)
--green   #6ee7b7
--red     #fca5a5
--yellow  #fcd34d
--ease    cubic-bezier(.32,.72,0,1)
--mono    tickers, prices, dates, any aligned number
```

Green and red are the only hues that earn a place here, and only for direction.
Reuse: Research mode's citation treatment for coverage; the segmented control
from Settings → Security for the period selector; the activity-feed card for
any tool step.

---

## 9. Honesty and accessibility rules

1. **Direction is never colour alone.** Arrows on every change figure.
2. **Every price shows its age.** The header stamp covers the page; the chart
   carries its own note because it gets scrolled back to.
3. **The chart needs a text summary** — already drawn as the caption; it doubles
   as the accessible description.
4. **Currency is always shown.** Not every ticker is USD (the watchlist already
   has ASML in EUR).
5. `:focus-visible` is global — do not design controls that clip a 2px ring.
6. `prefers-reduced-motion` is honoured app-wide; any chart draw-in must
   degrade to instant.

---

## 10. Out of scope

Order entry, trading, transfers — **Arthur will never place a trade**. Also:
price alerts (no scheduler yet), options chains, screeners, and any real-time
or streaming presentation.

---

## 11. Open questions

1. **Back**: does the page have an explicit back control, or does clicking the
   active watchlist row again close it? Both need a resting affordance.
2. **Does the page survive a chat switch?** If someone opens AMD, then switches
   conversation, do they return to the page or the transcript?
3. **`See it in my portfolio`** implies portfolio, which is not built. Does the
   chip wait, or open an empty state offering to add the holding?
4. **Does the composer placeholder change** on this page (`Ask about a
   symbol…`) — and if so, does typing there automatically carry the symbol as
   context, or is it an ordinary message?
