# Arthur — Portfolio editing & data-entry safety

Design handoff. Everything described here is **built and working**; this brief
asks for the visual pass, not the logic. Where I've made a call I'll say so and
say why, so you can overrule it knowingly.

Dark only. Existing tokens: `--bg` (main pane, *lighter*), `--bg2` (cards/rails,
*darker*), `--muted`, `--emph`, `--border`, `--text`, `--tmut`, `--accent`,
`--green`, `--red`, `--yellow`, `--ease`, `--mono`. Monochrome by intent —
colour appears only for direction (up/down) and caution.

---

## 0. Context: why this round exists

The portfolio shipped, then real data went into it, and **five of six rows were
wrong**. Not a calculation bug — every figure was arithmetically correct. The
screen just made four different mistakes easy and none of them visible:

| What happened | Why the screen allowed it |
|---|---|
| `BTC` bought as bitcoin, priced as a Grayscale trust | Nothing showed what the ticker resolved to |
| `XRP` given the position's *total value* as its per-unit cost | "Cost" never said per-unit or total |
| Cost basis entered in CAD, instrument quotes in USD | Cost had no currency of its own |
| Current price entered as cost basis, so P/L read 0.00% | No way to see or correct it afterwards |

Every fix below traces to one of those. **The theme: this screen's real job is
not displaying numbers, it's keeping the numbers honest.** Design accordingly —
the states that matter most are the imperfect ones.

---

## 1. The holdings table

Seven columns: Holding · Quantity · Paid each · Price now · Value · P/L ·
actions. Columns 2–6 right-aligned with `tabular-nums`.

**Labels changed and shouldn't change back.** "Cost" and "Value" were sitting
side by side meaning different things — one per-unit, one a total. `$3.07` next
to `$24.96` looks like an 8× gain until you notice. **"Paid each" and "Price
now" can only be read one way.** Each header carries a hover definition.

### Row states — the whole point of this brief

Six states, in rough order of how much design attention they deserve:

1. **Normal** — priced, comparable, P/L in green/red with an arrow.
   Arrows carry direction, never colour alone.
2. **FX-blocked** — paid in a currency the thing doesn't quote in. Value and
   today's move are real; **P/L is withheld**, showing `CAD vs USD`. This is
   *not* an error — nothing failed, and the position is fine. Currently muted
   grey. It must not read as broken, but must be noticeable enough to explain
   the gap where a number should be.
3. **Cost-suspect** — a small amber `⚠ check this` chip under the ticker,
   opening a dialog. Fires when price is under 2% of cost basis. **Loss side
   only, deliberately**: a 25× *gain* is real and flagging it insults the best
   trade someone ever made. Amber, never red — nothing has failed and the user
   may be right.
4. **Unpriced** — quote failed; row dims to 62%, keeps its typed figures, shows
   "no price". Never removed: a position that vanishes reads as lost data.
5. **Loading** — skeletons (see §4).
6. **Editing** — see §2.

> **Ask:** states 2 and 3 are the ones I'm least sure of. They need to be
> *legible without being alarming*, and I may have landed too quiet on 2 and too
> loud on 3.

### Actions column

Pencil + trash, revealed on `:hover` **and `:focus-within`** — keyboard users
never hover, and without focus the actions are unreachable without a mouse.

*Just fixed:* the track was 34px holding two 26px buttons, so they overflowed
onto the P/L figure — the one number in the row you least want covered. Now
60px. Worth a second look at whether always-visible-but-dim beats
hidden-until-hover here; a table where the controls appear on approach can feel
like a guessing game.

---

## 2. The edit bar

**This is the piece most in need of your eye.**

I built it twice. First attempt kept the seven table columns so nothing would
shift when a row opened. That failed two ways: inputs stretched to fill `1fr`
tracks and became enormous, and two controls with no column of their own — the
each/total switch and the derived readback — got shoved into cells that meant
something else. **A form's fields want their own widths; a table's columns want
to stay aligned. Trying to be both produced neither.**

It's now a flex bar spanning the row: `Holding · Quantity · Paid [each|total] ·
In (currency) · = derived · Cancel/Save`. Marked with a 2px `--accent` inset on
the left edge so a wide table still tells you which row is open. Enter saves,
Escape cancels.

**Why editing at all, rather than delete-and-retype:** the real mistakes are
single-field. Forcing a delete throws away the three fields someone got right to
fix the one they didn't, and makes correcting a number feel destructive — so
people leave wrong data in place instead.

Open questions for you:

- Does an inline bar hold up, or should this be a small popover anchored to the
  row? The bar is honest about being a mode change; a popover keeps the table
  intact but hides context.
- Save/Cancel are pushed right so they're always in the same place regardless of
  how many fields show. Should Save be `primary` at all — is a portfolio edit an
  affirmative act or a quiet correction?

---

## 3. Cost entry: `each` / `total`

A two-state switch **inside the Paid field's own label** — it changes how one
box is read, so it belongs to that box, not to the form.

**Why it exists:** anyone buying on a schedule has no single purchase price,
they have twenty-six. But their broker shows a *book value* — the total they've
put in. Asking for "paid per unit" makes that person do division before they can
use the screen, and **division is where the errors came from.**

In `total` mode the derived per-unit figure is **printed back** (`That's CA$41.03
per unit across 16.4593 units`). Someone entering a total is trusting a number
they never typed; showing it makes that checkable at a glance.

Storage is unchanged — `cost_basis` is always per-unit, and one line in the form
divides. Present in both the add form and the edit bar; **the edit bar is where
it matters more**, because a fortnightly top-up changes the total, not the
average.

---

## 4. Loading

From your zip 13, kept faithfully:

- Skeleton bars in the three totals cards, staggered 0 / .08s / .16s so they
  read as one group rather than three unrelated pulses.
- Skeleton rows in the table.
- The line: *"Your quantities and cost are local and already final. Fetching
  prices to value them."*

**That sentence is the design.** A bare spinner over a portfolio reads as "your
holdings are loading" — the one thing that would be alarming, and untrue. Only
the valuation is in flight.

Skeletons appear **only when there's nothing yet to show**. A refresh keeps the
existing figures up: they were true a minute ago, and blanking them to re-fetch
the same numbers is a worse answer than a slightly stale one.

---

## 5. Add form + the resolver

On blur, the symbol field looks up what the ticker actually is and says so:
*"XRP is Bitwise XRP ETF — trading at $11.17. If that isn't what you hold, check
the ticker."*

**This is the single highest-value element on the screen** and currently it's a
plain muted note. It's the thing that would have caught two of the six bad rows.
It fires while the cost basis is still being typed — the only moment the
correction is cheap.

Three outcomes to design: **resolved** (quiet confirmation — most of the time
it's reassurance, not a warning), **unknown ticker** (still savable, just
unpriced; suggests `XEQT.TO`-style suffixes), **lookup unavailable** (Docker
down — must not block entry).

Also present: `Paid in` currency select, and an inline validation message for
unparseable numbers (`88784.00`, not `$88,784.00`) shown **beside the field**
rather than as a toast after a round-trip.

---

## 6. Native `<select>`

Just fixed, flagging so it doesn't regress: the currency dropdown was rendering
as white rows with a blue highlight in the middle of a black app. A `<select>`'s
popup is drawn by the browser, not the page — **no amount of CSS on the element
touches it.** `color-scheme: dark` on `:root` is the only supported fix. The
closed control now uses `appearance: none` with an inline SVG chevron.

If your prototype includes any dropdown, please assume this token exists.

---

## 7. Totals

One card group per currency, **never combined** — no FX rate is fetched, and one
wrong total is worse than two right subtotals.

Header: `Portfolio · N holdings, entered by you` — three words carrying the whole
product boundary — plus Add holding and a manual Refresh. Refresh is manual by
design: auto-refresh implies real-time data this feed doesn't have, and it moves
your own money around while you're reading it.

Two notes appear conditionally, and both are *honesty about scope*:

- More than one currency → why totals aren't combined.
- Any FX-blocked holding → **"P/L excludes 1 holding bought in a different
  currency"**. A total that doesn't describe everything above it has to say so.

---

## What I'm not asking for

No advice, scores, ratings, traffic lights, or "Arthur's take" anywhere. No
allocation pie charts, no rebalancing prompts, no benchmark comparison. Arthur
isn't a licensed adviser and knows nothing about the user's situation — it shows
figures and offers to explain them. **Every number on this screen was typed by
the user; nothing here connects to a broker.**

Also deliberately absent: IRR, time-weighted return, tax lots. All need a full
transaction history against three fields of input. Deriving them anyway would
produce numbers that look authoritative and are wrong.

---

## Priority

1. **Edit bar** (§2) — rebuilt once, still the weakest thing visually
2. **Resolver feedback** (§5) — highest value, currently plainest
3. **Row states** (§1) — FX-blocked and cost-suspect especially
4. **each/total switch** (§3) — small control, load-bearing
5. Loading, totals, actions column — mostly polish
