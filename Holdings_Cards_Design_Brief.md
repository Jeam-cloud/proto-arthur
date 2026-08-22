# Arthur — Holdings, as cards

Design handoff for **one screen element**: the list of holdings inside Finance
mode's portfolio. Everything above it (the three totals) and below it (the
explain button, the privacy line) stays as it is unless you tell me otherwise.

Dark only. Existing tokens: `--bg` (main pane, *lighter*), `--bg2`
(cards/rails, *darker*), `--muted`, `--emph`, `--border`, `--text`, `--tmut`,
`--accent`, `--green`, `--red`, `--yellow`, `--ease`, `--mono`.

---

## Three decisions already made

I asked, so these are settled — please design to them rather than around them.

1. **Value leads.** The first thing a row answers is *what is this worth right
   now*. P/L supports it, the entry data (quantity, what was paid) is small
   print. Closest to how a broker app reads.
2. **One card per holding.** The table is being dropped, not tuned.
3. **Plain.** No allocation bars, no sparklines, no sort controls. Typography
   and spacing do all the work — consistent with the rest of Arthur being
   deliberately quiet.

---

## Why the table is being replaced

Eight columns need 856px and scroll sideways in a pane that also has a
watchlist beside it. Worse, three of the eight cells grew a second line
(symbol + name, P/L + percent, paid-each + total), so rows are tall *and* wide
and the whole thing reads as a spreadsheet nobody asked for. It has the density
of a data grid with the row count of a shopping list — five holdings.

---

## Real content to lay out

Please use these, not lorem — the awkward cases are the point.

| Symbol | Name | Value | P/L | Units | Invested | Today |
|---|---|---|---|---|---|---|
| `XEQT.TO` CAD | iShares Core Equity ETF | **CA$750.21** | +CA$74.87 (+11.09%) | 16.4593 | CA$675.34 | −CA$4.36 |
| `VFV.TO` CAD | Vanguard S&P 500 Index ETF | **CA$191.22** | +CA$20.69 (+12.13%) | 1.0193 | CA$170.53 | −CA$1.89 |
| `SPCX` USD | Space Exploration Technologies | **$43.50** | −$12.56 (−22.40%) | 0.3176 | $56.06 | +$1.17 |
| `BTC-CAD` CAD | Bitcoin CAD | **CA$24.00** | −CA$11.00 (−31.44%) | 0.000222 | CA$35.00 | +CA$0.49 |
| `XAUCAD=X` CAD | Gold CAD | **CA$45.13** | −CA$4.87 (−9.74%) | 0.0071493987 | CA$50.00 | — |

Note the range: values from **CA$24** to **CA$750**, and quantities from
**0.000222** to **16.4593** — eight decimal places beside four significant
figures, in the same column. Any layout assuming similar magnitudes falls apart
on real data. Long names truncate; `Space Exploration Technologies` and
`iShares Core Equity ETF` are both realistic lengths.

`XAUCAD=X` has no "today" figure because that listing returns no previous
close — a dash, not a zero, and not a hidden row.

---

## Every state a card must have

This is the part I most need you to design. The happy path is the easy one.

1. **Normal** — priced, comparable, P/L in green or red. Direction carries an
   **arrow as well as colour**, always: roughly 1 in 12 men can't separate red
   from green, and this is the screen where confusing up with down costs most.

2. **FX-blocked** — bought in a currency the instrument doesn't quote in.
   Value and today's change are real and shown; **P/L is withheld** and reads
   `CAD vs USD`. This is *not* an error — nothing failed, the position is fine,
   and Arthur simply won't fetch an exchange rate to invent a comparison. It
   must not look broken, but the gap where a number should be needs explaining.

3. **Cost looks wrong** — an amber `⚠ check this` affordance opening a dialog.
   Fires when the price is under 2% of the cost basis, which in practice means
   a wrong ticker or a total typed where a per-unit price was wanted. **Amber,
   never red**: nothing has failed and the user may be right. This holding is
   also excluded from the total P/L above, so the card and the totals block
   need to agree visually that something is being held out.

4. **Unpriced** — the quote failed. The card keeps every hand-entered figure
   and says "no price". It is **never removed or collapsed**: a position that
   vanishes reads as lost data, and this is the one screen where that would be
   alarming.

5. **Loading** — skeletons for the market figures only. The user's own
   quantities and cost are local and already final; the accompanying line says
   exactly that, because a spinner over a portfolio otherwise reads as "your
   holdings are loading".

6. **Editing** — see below.

---

## The edit affordance

Currently each row has a pencil and a trash, visible at 45% and brightening on
hover, and the pencil opens a **full-width edit bar beneath the row** — quantity,
paid (with an each/total switch), currency, Save/Cancel, "Enter saves · Escape
cancels". Beneath rather than replacing, so the figures being corrected stay
visible while they're corrected.

With cards that arrangement has nowhere obvious to go. **Your call, and please
pick one:**

- The card flips into an edit state in place (same footprint, fields replace
  figures) — compact, but hides the numbers being changed, which is the exact
  problem the current layout was built to avoid.
- The card expands downward with the edit fields appended below the figures —
  preserves the "see it while you change it" property, costs vertical space.
- Editing moves entirely into the existing modal — simplest, but a modal for
  changing one number is heavy, and topping up a position is a **fortnightly**
  action for this user.

Whichever: the **each/total switch must survive**. It sits inside the Paid
field's own label and exists because someone buying on a schedule has no single
purchase price — they have twenty-six, and what they actually have is the
broker's book value. Removing it puts hand-division back in the loop, and hand
division is what put wrong cost bases on five of six holdings.

---

## "Paid each" is being cut from the card face

The old table led with a per-unit cost — `$171.20` — and it needed explaining
three separate times before we worked out why: **it duplicates the percentage
and displaces the real number.**

Per-unit cost exists to be compared against the current price. But `+34.01%`
already states the outcome of that comparison, more directly and without
arithmetic. Meanwhile the figure the user actually thinks in — *"I put in
$6,848"* — appeared only as small print beneath a number they didn't recognise.

Worse, it invites a wrong mental model. `$171.20` reads as *the price on the day
I bought*, and for anyone buying on a schedule there is no such day: they have
twenty-six of them. It is an **average**, and nothing in the label said so.

**So the card shows what was invested, not what one unit cost:**

```
16.4593 units · CA$675.34 invested
```

Per-unit does not disappear from the product. It stays in:

- the **add/edit form**, which is where it is entered (and where the each/total
  switch converts between the two);
- an **expanded detail view**, if you build one — the one real use for it is
  break-even (*"below CA$41.03 I'm underwater"*), which the percentage cannot
  express. Worth having occasionally; not worth a permanent slot.

Storage is unaffected: `cost_basis` stays per-unit in the database, because
that is the form that survives buying more. Display and storage do not have to
agree.

---

## Currency

Holdings in the leading currency are the cards. Anything else is summarised
beneath in a single strip — *"Held in USD, kept separate: $43.50, −$12.56"* —
with a line explaining that Arthur doesn't fetch exchange rates and won't add
currencies together.

**A problem to solve:** the cards for the secondary currency still have to
appear somewhere. Right now every holding is in one list regardless of
currency, and only the *totals* are separated. Does a USD holding sit inline
with a currency badge, or in its own grouped section under a heading? Grouping
is more honest and costs a header; inline is denser and relies on the badge
being noticed.

---

## A risk worth naming

The three totals above the list are already cards on `--bg2`. Making each
holding a card too gives you cards inside a card region on a card-coloured
background, which is how dashboards turn to mush. The holdings probably need a
**different surface treatment** from the totals — flatter, or separated by rules
rather than borders, or on `--bg` instead. Please solve this explicitly rather
than letting both use the same 13px-radius bordered box.

---

## Out of scope

No advice, scores, ratings, or "Arthur's take" anywhere. No rebalancing
prompts, no benchmark comparison, no allocation pie. Arthur isn't a licensed
adviser and knows nothing about the user's situation — it shows figures and
offers to explain them. Every number on this screen was typed by the user;
nothing connects to a broker.

Also absent by choice: IRR, time-weighted return, tax lots. All need a full
transaction history against three fields of input.

---

## What I need back

1. The card at rest, with the five real holdings above.
2. States 2, 3 and 4 (FX-blocked, cost-suspect, unpriced) — these matter more
   than the happy path.
3. Your answer on the edit affordance.
4. Your answer on how the secondary currency sits in the list.
5. The card region against the totals block, so I can see the two surfaces
   together rather than in isolation.
6. **Optional:** an expanded card, if you think one is worth having — the place
   per-unit cost, price now, and purchase date would live for someone who wants
   them. Say so if you think the card is complete without one; I would rather
   ship five fields that are always right than nine behind a chevron.
