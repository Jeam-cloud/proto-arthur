# Arthur — Finance portfolio: design brief

**For:** the Arthur prototype mockup (same file family as `Arthur.dc.html`)
**Follows:** `Finance_Mode_Design_Brief.md`, `Symbol_Page_Design_Brief.md`
**Built already:** watchlist, symbol page, inline charts, explain-move, comparison
**Date:** 14 Aug 2026

---

## 1. What this is

The watchlist answers *"what is NVDA doing?"*
The portfolio answers **"how am I actually doing?"**

You enter what you hold — symbol, quantity, and what you paid per share —
and Arthur values it against the current price:

- current value per holding, and the total
- unrealised profit or loss, in money and percent
- today's change, for your position specifically

So instead of *"AAPL is $229.42"*, it is *"your 40 shares are worth $9,177, up
$1,240 since you bought them."*

**Tracking, not brokerage.** Arthur computes what you already own from numbers
you gave it. It never connects to a broker, never syncs positions, never
executes anything. See §7.

---

## 2. Two things make this feature worth building

**It is the strongest privacy claim in the app.** What you own is more
sensitive than what you are curious about. Everything else in Finance mode is a
public price anyone could look up; your holdings are yours. In Arthur they sit
in a local SQLite file and never leave the machine — no account, no sync, no
server. Most finance apps cannot say that, and the design should say it
plainly, **once**, somewhere permanent. Not a badge on every row.

**It is the only screen with genuinely personal numbers**, which is why the
emotional register matters more here than anywhere else in the app.

---

## 3. The hard problem: data entry

This is the only Finance feature that does nothing until the user works. The
watchlist needs a ticker; this needs a ticker, a quantity, a price and a date,
per holding — and stays wrong if they buy more and forget to update it.

That makes the **add-a-holding flow the most important thing in this brief**,
not the totals.

Design for:

- **Adding from a symbol you already have.** The symbol page's "See it in my
  portfolio" chip should lead into a pre-filled form — the ticker is known, so
  ask for two numbers, not four.
- **A minimum viable holding**: symbol + quantity + cost basis. Purchase date
  optional. Every extra required field is a person deciding not to bother.
- **A visible "this is only what you told me" state.** The portfolio is not
  authoritative and must not present as though it is.
- **Editing being as easy as adding.** People buy more of things.

Explicitly design the **empty state**: it is what most users will see, and it
has to make the first holding feel like thirty seconds of work rather than a
data-entry project.

---

## 4. Layout

**Top row — three figures, large.** Total value, total unrealised P/L (money
and %), today's change. Per dashboard convention these are the numbers someone
opens the screen for; everything else is detail.

**Holdings table**, one row each:

```
symbol · name    qty    cost basis    price    value    P/L ($ and %)    today
```

Numbers in mono, right-aligned. The P/L column is the only place colour
appears, and it carries a sign or arrow as well — never colour alone.

**Per-row**: click through to the symbol page (it already exists), and a
right-click menu matching the watchlist's — edit, remove, explain move.

**Deliberately NOT here:**
- Allocation pie charts. They look like analysis and say very little at
  five holdings.
- Any performance figure Arthur cannot compute honestly from cost basis —
  no IRR, no time-weighted return, no benchmark comparison. Those need a
  transaction history we are not asking for. See §7.

---

## 5. Emotional register

The research on investment dashboards is consistent: **people looking at losses
make more impulsive decisions, and dashboards amplify it.** This is the screen
where that applies.

- Red is legible, never loud. No large red fills, no flashing, no animation on
  a falling number.
- No auto-refresh. Numbers must not move while someone is reading them — the
  watchlist's manual Refresh is the precedent.
- Losses are stated as plainly as gains. No apologetic copy, no encouragement,
  no "hang in there". State the number and stop.
- **No advice.** Not *"consider rebalancing"*, not *"this position is
  overweight"*. Same boundary as the research layer: Arthur shows, the person
  decides.

---

## 6. States

- **Empty** — the important one. See §3.
- **Adding / editing** a holding.
- **Prices loading** — the holdings are local and instant; the prices are not.
  Show the table with quantities and cost basis immediately and fill the market
  columns in. It is the same progressive pattern as the symbol page, and the
  same note applies: what is already shown is final.
- **A symbol that failed to price** — show the holding with its cost basis and
  a retry, never drop the row. A missing position reads as lost data.
- **Upstream unavailable** — the portfolio still lists what you own; only the
  valuation is unavailable. Make that distinction visible.
- **Stale entry prompt** — optional, worth considering: if a holding has not
  been edited in a long time, a quiet way to ask whether it is still right.

---

## 7. Boundaries

**Arthur will never place a trade, move money, or connect to a broker.**
Nothing in this feature may look like it could.

Also out of scope, because the data to do them honestly is not being collected:
- IRR, time-weighted or money-weighted return
- Benchmark comparison ("you vs the S&P")
- Tax lots, wash sales, capital gains reporting
- Dividend income tracking
- Rebalancing suggestions of any kind

If a mock wants one of these, it means asking the user for a full transaction
history — a much larger product decision, not a design detail.

---

## 8. Where the AI belongs

Same rule as everywhere: **the page hands off, it does not host.** Actions
close the view and answer in the transcript.

Worth designing:
1. **"Explain today's move in my portfolio"** — which holdings drove the
   change. Genuinely useful and only possible because Arthur has both the
   positions and the news.
2. **Per-holding explain**, reusing `explain_move`, which is already built.
3. **"What am I most exposed to?"** — sector concentration, described rather
   than scored. The line to hold: describing concentration is information,
   recommending a change is advice.

---

## 9. Design system

Tokens verbatim from `Arthur.dc.html` `renderVals()`. Monochrome; green and red
for direction only and never alone. `--mono` for every figure so columns align.

Reuse: the watchlist's row treatment and context menu, the symbol page's stat
cards for the top row, the segmented control from Settings → Security for any
toggle, the existing ConfirmDialog for removing a holding.

---

## 10. Open questions

1. **Where does it live?** The watchlist panel already has a `Portfolio`
   toggle in the design, which implies it replaces the panel. But a holdings
   table wants width — is it a full page like the symbol page instead, with the
   panel toggle simply navigating there?
2. **One portfolio or several?** Several is more honest for people with an ISA
   and a pension; one is far simpler.
3. **Multi-currency.** The watchlist already holds ASML in EUR. Does the total
   convert, or refuse to sum across currencies and show subtotals? Converting
   needs an FX rate we do not currently fetch — and a wrong total is worse than
   no total.
4. **Purchase date**: optional field, or not collected at all in v1?
5. **Does removing a holding confirm, or undo?** Chat delete uses undo; this is
   hand-entered data that cannot be re-fetched, which argues for confirm.
