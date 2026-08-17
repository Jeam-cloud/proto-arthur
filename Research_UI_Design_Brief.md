# Arthur — Finance research layer: the UI

**For:** the Arthur prototype mockup (same file family as `Arthur.dc.html`)
**Companion to:** `Equity_Research_Design_Brief.md` — that one is *what to show*,
this one is *how it behaves on screen*
**Sits on:** the existing symbol page, which is live
**Date:** 14 Aug 2026

---

## 1. What this feature is

Today the symbol page answers **"what is it doing?"** — price, chart, ranges,
sector, coverage.

This layer answers **"is it any good, and what am I paying for it?"** It is the
difference between watching a ticker and researching a company, and it is the
thing that makes Finance mode worth having over a browser tab.

It appends to the bottom of the symbol page. It is not a new screen, not a tab,
and not a modal — you scroll into it.

---

## 2. The rule that governs every row

From the content brief, restated because the whole layout depends on it:

> **A number on its own is not research. A number with a comparison is.**

"P/E 32" is worthless. "P/E 32, 5-year median 19" is a finding.

So the row unit is not `label: value`. It is:

```
label        value        comparison        (?)
```

If a metric cannot be given a comparison, it either goes in a lesser tier or it
does not appear. **Please do not design a dense grid of bare ratios** — it looks
like research, teaches nothing, and is the most common way finance UIs fail.

**No verdicts.** Never *cheap*, *expensive*, *undervalued*, *strong*, *weak*,
*good*, *bad*. No traffic lights, no scores, no letter grades, no "Arthur's
take". Arthur shows the comparison and the user decides. This is a hard product
boundary, not a stylistic preference.

---

## 3. Structure

Six sections, in this order. Each collapsible; **only the first is open by
default.**

1. **Valuation** — P/E (trailing and forward), price/sales, price/book
2. **Profitability** — gross, operating and net margin; ROE
3. **Financial health** — debt/equity, current ratio, free cash flow
4. **Growth** — revenue and earnings growth
5. **Dividend** — yield, payout ratio *(hide entirely when the company pays none —
   a dividend section full of dashes is noise)*
6. **Ownership** — institutional %, insider %

Collapsed-by-default is not tidiness — it is what makes the fetching honest.
See §4.

---

## 4. Progressive fetch — design the wait

The cheap quote is already on screen by the time anyone scrolls here. The
research data comes from separate, slower calls against a rate-limited source.

- **Sections fetch when opened**, not on page load. Opening all six at once
  would be six network calls and would trip the limiter.
- Each section therefore needs a **loading state**: skeleton rows at the row
  count, since the metric labels are known before the values are.
- And a **failed state**, per section, with a retry. One section failing must
  not disturb the five around it.
- The existing "everything above is already final" note is the tone to match —
  say what is still coming and confirm what is not moving.

---

## 5. The comparison patterns

In order of how often they will apply:

**5a. Against its own history — the workhorse.**
A small range bar with today marked, and the 5-year low/median/high beneath.
Reuse the symbol page's existing range bar; it already reads correctly and
users will have seen it above.

**5b. Trend across periods.**
For anything with 3–5 years of statements — margin, revenue, FCF. A tiny
sparkline plus first-to-last direction. Direction carries an arrow, never
colour alone.

**5c. Against the sector.**
Value and sector median side by side. **We do not have sector aggregates.**
Design it if you like, but mark it clearly as a later addition — do not let
v1 depend on it.

**5d. Explain in context.**
The "?" on every row. Always available, and the fallback whenever the three
above are not.

---

## 6. Statements

A compact table, 3–5 years, one row per line item, numbers in mono and
right-aligned. This is the one place in the app where a table beats a chart —
the reader is comparing down a column, and that is what tables are for.

Annual by default, with a quarterly toggle. Do not design both at once.

---

## 7. Missing data is the normal case

ETFs, ADRs, trusts, small caps and most non-US listings return nulls for half
of this. A screen designed only for a large US tech company will look broken
for everything else.

- The empty cell needs a designed treatment — an em dash and a muted label, not
  a blank.
- A section where **everything** is missing should say so in one line
  (*"Yahoo has no valuation data for this listing"*) rather than render six
  empty rows.
- The dividend section is the common case: most tickers pay nothing. Hide it.

---

## 8. Every figure carries its period

More important here than the 15-minute price delay, and easier to get wrong.

A P/E built on the last reported quarter can be months old. Every value needs
`TTM`, `Q2 2026` or `FY2025` next to it — small, muted, but present. A
fundamental with no date is a fundamental you cannot trust.

---

## 9. Where the AI belongs

Three affordances, all obeying the existing rule: **the page hands off, it does
not host.** Click, page closes, answer arrives in the transcript.

1. **Per-row "?"** — *"P/E of 32, against a 5-year median of 19 — what does
   that tell me?"* The workhorse, and the reason a beginner can use this screen
   at all.
2. **Per-section** — *"Explain this company's financial health"*, with the
   section's figures as context.
3. **The qualitative gap** — one action, prominent, near the bottom:
   *"What's this company's competitive position?"* Moat, management and
   competitive dynamics are what serious investors weight most heavily, and no
   API returns them. This is the single clearest thing Arthur can do that a
   data table cannot.

---

## 10. Design system

Tokens verbatim from `Arthur.dc.html` `renderVals()` — `--bg`, `--bg2`,
`--muted`, `--emph`, `--border`, `--text`, `--tmut`, `--accent`, `--green`,
`--red`, `--yellow`, `--ease`, `--mono` for every figure.

Monochrome. Green and red for direction only, never as the sole channel.
Reuse: the symbol page's range bar and stat cards, the segmented control from
Settings → Security for the annual/quarterly toggle, Research mode's citation
treatment for anything sourced.

---

## 11. Out of scope

Screeners, backtests, DCF models, portfolio optimisation, options analytics,
price targets computed by Arthur, and any ranking or scoring of a stock.
And trading, of any kind, ever.

---

## 12. Open questions

1. **How much is v1?** Valuation and Profitability alone are nearly free —
   `.info` is already fetched for this page and carries most of both. Financial
   health, Growth and the statements need new endpoints.
2. **Does the "?" open the section's context or just that row's?** Row-level is
   more precise; section-level is fewer round trips.
3. **Do the statements belong on this page at all**, or behind a "Financials"
   affordance that swaps the main pane? Five years by five line items is a lot
   of page.
4. **Comparison against a peer** — same screen, or does it stay in the chat
   where the overlay chart already works?
