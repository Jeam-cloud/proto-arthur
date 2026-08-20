// The portfolio: what you own, valued against live prices.
//
// TRACKING, NOT BROKERAGE. Every number here was typed in by the user; Arthur
// prices it and computes the difference. Nothing on this screen connects to a
// broker, moves money, or could be mistaken for doing so.
//
// TOTALS ARE PER CURRENCY AND NEVER CONVERTED. Adding a EUR holding to a USD
// one needs an FX rate this app does not fetch, and one wrong total is worse
// than two right subtotals — so each currency gets its own row of figures.
//
// NO ADVICE. No "consider rebalancing", no "overweight", no scoring. The same
// boundary as the rest of Finance mode: Arthur shows, the person decides.
import React, { useEffect, useState } from "react";
import { AlertTriangle, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useFinance } from "../../stores/finance";
import { useConfirm } from "../../stores/confirm";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";

// Enough to cover the exchanges Yahoo quotes and the brokers most people use.
// Not a complete ISO list: a 180-entry dropdown is worse than a short one that
// covers 99% of cases, and the resolved quote currency is always added.
const CURRENCIES = ["USD", "CAD", "EUR", "GBP", "JPY", "AUD", "CHF", "HKD", "INR"];

function money(v, currency, digits = 2) {
  if (typeof v !== "number" || !isFinite(v)) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency", currency: currency || "USD",
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(v);
  } catch { return v.toFixed(digits); }
}

// Direction never rides on colour alone.
function Signed({ value, pct, currency }) {
  if (typeof value !== "number" || !isFinite(value)) return <span className="pf-flat">—</span>;
  const up = value >= 0;
  return (
    <span className={up ? "pf-up" : "pf-down"}>
      {up ? "▲" : "▼"} {up ? "+" : "−"}{money(Math.abs(value), currency)}
      {typeof pct === "number" && isFinite(pct) && (
        <span className="pf-pct"> ({up ? "+" : "−"}{Math.abs(pct).toFixed(2)}%)</span>
      )}
    </span>
  );
}

function AddForm({ onDone, initialSymbol = "" }) {
  const addHolding = useFinance((s) => s.addHolding);
  const resolveSymbol = useFinance((s) => s.resolveSymbol);
  const [f, setF] = useState({
    symbol: initialSymbol, quantity: "", cost_basis: "", purchase_date: "",
    // Empty means "whatever the quote is in" — the right default, and what
    // every holding entered before this field existed already assumed.
    cost_currency: "",
  });
  const [busy, setBusy] = useState(false);
  // What the ticker actually resolved to. See the resolve route for why this
  // exists: "BTC" is a Grayscale trust, not bitcoin, and the only moment that
  // is cheap to discover is while the cost basis is still being typed.
  const [found, setFound] = useState(null);

  // TYPED NUMBERS ARE NOT NUMBERS. People paste "88,784.00" or "$88,784" —
  // exactly the format this page prints them in — and Number() returns NaN,
  // which JSON.stringify writes as `null`, which the API rejects as a 422 with
  // no useful message. Cleaning the obvious separators here means the form
  // accepts what a person would reasonably type.
  const num = (raw) => {
    const cleaned = String(raw ?? "").replace(/[\s,$£€]/g, "");
    const n = Number(cleaned);
    return cleaned !== "" && isFinite(n) ? n : null;
  };
  const quantity = num(f.quantity);
  const costBasis = num(f.cost_basis);
  // Checks the VALUES, not merely that the boxes are non-empty — the old test
  // (`f.cost_basis !== ""`) happily let "88,784.00" through to become null.
  const ready = f.symbol.trim() && quantity !== null && quantity > 0
    && costBasis !== null && costBasis >= 0;
  // Typed something, but not a number Arthur can use.
  const badNumber = (f.quantity !== "" && quantity === null)
    || (f.cost_basis !== "" && costBasis === null);

  // On blur, not on every keystroke: each call is a container start against a
  // rate-limited feed, and "AAP" on the way to "AAPL" is not a question worth
  // asking upstream.
  const lookup = async () => {
    const sym = f.symbol.trim().toUpperCase();
    if (!sym || sym === found?.symbol) return;
    setFound(await resolveSymbol(sym));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    // Only sent when it differs from the quote — a cost_currency equal to the
    // listing currency is the default, and storing it explicitly would just be
    // noise in the row.
    const ok = await addHolding({
      ...f,
      // The parsed numbers, not the raw strings — the store must never be the
      // place that discovers a field was unusable.
      quantity, cost_basis: costBasis,
      cost_currency: f.cost_currency && f.cost_currency !== found?.currency
        ? f.cost_currency : null,
    });
    setBusy(false);
    if (ok) {
      setF({ symbol: "", quantity: "", cost_basis: "", purchase_date: "", cost_currency: "" });
      setFound(null);
      onDone?.();
    }
  };

  return (
    <form className="pf-add" onSubmit={submit}>
      <div className="pf-field">
        <label>Symbol</label>
        <input value={f.symbol} placeholder="AAPL" autoFocus onBlur={lookup}
               onChange={(e) => setF({ ...f, symbol: e.target.value.toUpperCase() })} />
      </div>
      <div className="pf-field">
        <label>Units</label>
        <input value={f.quantity} placeholder="40" inputMode="decimal"
               onChange={(e) => setF({ ...f, quantity: e.target.value })} />
      </div>
      {/* TWO WAYS TO SAY THE SAME THING, because only one of them is a number
          people actually have.
          Anyone buying on a schedule has no single purchase price — they have
          twenty-six of them — but their broker shows a book value, the total
          they have put in. Asking for "paid per unit" makes that person do
          division before they can use the screen, and division is where the
          mistakes come from. Storage is unchanged: cost_basis stays per unit,
          and total mode just divides. */}
      <div className="pf-field">
        <label>
          Paid
          <span className="pf-mode">
            <button type="button" className={f.costMode === "unit" ? "on" : ""}
                    onClick={() => setF({ ...f, costMode: "unit" })}>each</button>
            <button type="button" className={f.costMode === "total" ? "on" : ""}
                    onClick={() => setF({ ...f, costMode: "total" })}>total</button>
          </span>
        </label>
        <input value={f.cost_basis} inputMode="decimal"
               placeholder={f.costMode === "total" ? "675.34" : "171.20"}
               onChange={(e) => setF({ ...f, cost_basis: e.target.value })} />
      </div>
      {/* PAID IN, which is not always the currency the thing quotes in. Buying
          a US-listed instrument through a Canadian broker in CAD is ordinary,
          and without this the P/L subtracts CAD from USD. Defaults to the
          resolved quote currency so the common case needs no thought. */}
      <div className="pf-field narrow">
        <label>Paid in</label>
        <select value={f.cost_currency || found?.currency || "USD"}
                onChange={(e) => setF({ ...f, cost_currency: e.target.value })}>
          {CURRENCIES.includes(found?.currency) || !found?.currency
            ? null : <option value={found.currency}>{found.currency}</option>}
          {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <button className="btn primary" type="submit" disabled={!ready || busy}>
        {busy ? "Adding…" : "Add"}
      </button>
      {/* STATED, NOT ENFORCED. Arthur cannot know which instrument was meant,
          so it names what the ticker resolved to and stops. Seeing "Bitwise
          XRP ETF" while typing a cost basis paid for the token is the whole
          intervention — no blocking, no autocorrect, no guess. */}
      {found?.ok && (
        <div className="pf-resolved">
          <strong>{found.symbol}</strong> is {found.name} — trading at{" "}
          {money(found.price, found.currency)}. If that isn't what you hold,
          check the ticker: crypto symbols in particular often resolve to a
          fund rather than the coin.
        </div>
      )}
      {found && !found.ok && found.unknown && (
        <div className="pf-resolved">
          Yahoo doesn't recognise <strong>{found.symbol}</strong>. You can still
          save it — the holding is kept, it just won't be priced. Canadian
          listings usually need a suffix, like <code>XEQT.TO</code>.
        </div>
      )}
      {/* Said HERE, next to the fields, rather than as a toast after a failed
          request. "Request failed (422)" tells a person nothing about which
          box to fix. */}
      {badNumber && (
        <div className="pf-add-error">
          Shares and price need to be plain numbers — <code>88784.00</code>, not
          <code>$88,784.00</code>.
        </div>
      )}
      {/* Named as optional so nobody stalls looking for it. Every extra
          required field is a person deciding not to bother. */}
      <div className="pf-add-note">Purchase date is optional and can be filled in later.</div>
    </form>
  );
}

// One row, in edit mode.
//
// WHY EDITING AND NOT DELETE-AND-RETYPE. The mistakes people actually make
// here are single-field: the wrong ticker, or a cost basis that's really the
// current price. Forcing a delete throws away the three fields they got right
// to fix the one they didn't, and it makes correcting a number feel like a
// destructive act — so people leave wrong data in place instead.
function EditRow({ h, onDone }) {
  const updateHolding = useFinance((s) => s.updateHolding);
  const [f, setF] = useState({
    symbol: h.symbol,
    quantity: String(h.quantity),
    cost_basis: String(h.cost_basis),
    cost_currency: h.cost_currency || "",
  });
  const [busy, setBusy] = useState(false);

  const parse = (raw) => {
    const cleaned = String(raw ?? "").replace(/[\s,$£€]/g, "");
    const n = Number(cleaned);
    return cleaned !== "" && isFinite(n) ? n : null;
  };
  const quantity = parse(f.quantity);
  const costBasis = parse(f.cost_basis);
  const ready = f.symbol.trim() && quantity > 0 && costBasis !== null && costBasis >= 0;

  const save = async () => {
    if (!ready || busy) return;
    setBusy(true);
    // Only the fields that actually changed. PATCH semantics: sending an
    // unchanged value is harmless but sending ALL of them makes every save
    // look like a full rewrite in any future audit of this table.
    const patch = {};
    if (f.symbol.trim().toUpperCase() !== h.symbol) patch.symbol = f.symbol.trim().toUpperCase();
    if (quantity !== h.quantity) patch.quantity = quantity;
    if (costBasis !== h.cost_basis) patch.cost_basis = costBasis;
    if ((f.cost_currency || null) !== (h.cost_currency || null)) {
      patch.cost_currency = f.cost_currency || null;
    }
    if (Object.keys(patch).length) await updateHolding(h.id, patch);
    setBusy(false);
    onDone();
  };

  return (
    <div className="pf-row editing">
      <span className="pf-sym">
        <input className="pf-edit-input sym" value={f.symbol} autoFocus
               onChange={(e) => setF({ ...f, symbol: e.target.value.toUpperCase() })} />
      </span>
      <span className="pf-num">
        <input className="pf-edit-input" value={f.quantity} inputMode="decimal"
               onChange={(e) => setF({ ...f, quantity: e.target.value })} />
      </span>
      <span className="pf-num pf-edit-cost">
        <input className="pf-edit-input" value={f.cost_basis} inputMode="decimal"
               onChange={(e) => setF({ ...f, cost_basis: e.target.value })} />
        <select className="pf-edit-input cur" value={f.cost_currency || h.currency}
                onChange={(e) => setF({ ...f, cost_currency: e.target.value })}>
          {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </span>
      <span className="pf-num" />
      <span className="pf-num" />
      {/* Spans the last two columns (P/L + actions), so the six cells above
          plus this pair still total the row's seven tracks. An extra trailing
          cell here would create an implicit eighth column and knock the edit
          row out of alignment with every row around it. */}
      <span className="pf-edit-actions">
        <button className="btn tiny" onClick={onDone} disabled={busy}>Cancel</button>
        <button className="btn tiny primary" onClick={save} disabled={!ready || busy}>
          {busy ? "…" : "Save"}
        </button>
      </span>
    </div>
  );
}

export default function PortfolioPage() {
  const {
    holdings, totals, pfLoaded, pfLoading, pfPricingFailed, pfError,
    loadPortfolio, removeHolding, symbols, open, setView,
  } = useFinance();
  const ask = useConfirm((s) => s.ask);
  const send = useChat((s) => s.send);
  const activeId = useConversations((s) => s.activeId);
  const [adding, setAdding] = useState(false);
  // The id of the row being edited, or null. One at a time on purpose: two
  // open editors means two sets of unsaved changes and no clear Escape.
  const [editing, setEditing] = useState(null);

  // Explains the flagged row in plain language. NOT a confirm-to-act dialog —
  // there is nothing to agree to. It names the likely cause and leaves the
  // decision (and the data) entirely alone.
  const setChecking = (h) => ask({
    title: `${h.symbol} — these figures look off`,
    body:
      `You entered ${money(h.cost_basis, h.cost_currency)} per share, but ${h.symbol} `
      + `(${h.name}) trades at ${money(h.price, h.currency)}. That gap is too large `
      + "to be a market move.\n\n"
      + "The usual cause is a ticker that isn't the thing you hold — crypto symbols "
      + "especially. Plain \"BTC\" is the Grayscale Bitcoin Mini Trust, not bitcoin; "
      + "\"XRP\" is the Bitwise XRP ETF, not the token. The coins themselves are "
      + "BTC-USD and XRP-USD.\n\n"
      + "Arthur has left your numbers exactly as you typed them.",
    confirmLabel: "Got it",
  });

  useEffect(() => { loadPortfolio(); }, [loadPortfolio]);

  const currencies = Object.keys(totals);

  const confirmRemove = (h) => ask({
    title: `Remove ${h.symbol} from your portfolio?`,
    // Explicit about what cannot be recovered: unlike a chat, these numbers
    // were typed by hand and Arthur cannot fetch them again.
    body: `The ${h.quantity} shares and the ${money(h.cost_basis, h.cost_currency)} `
      + "cost basis you entered are deleted. Arthur can't recover them — they "
      + "were never stored anywhere else.",
    confirmLabel: "Remove holding",
    onConfirm: () => removeHolding(h.id),
  });

  if (pfLoaded && !pfLoading && !holdings.length) {
    return (
      <div className="symbol-page">
        <div className="sp-scroll">
          <div className="pf-empty">
            <h3>Nothing entered yet</h3>
            <p>
              Type what you hold and Arthur values it against the current price.
              Three fields per holding, about thirty seconds. You can add the rest later.
            </p>
            {/* Keyed on the chosen symbol: initialSymbol is read once at mount,
                so without this a chip click would update state and change
                nothing the user can see. */}
            <AddForm key={String(adding)} initialSymbol={typeof adding === "string" ? adding : ""} />
            {symbols.length > 0 && (
              <>
                <div className="pf-or">Or start from something on your watchlist:</div>
                <div className="pf-chips">
                  {symbols.slice(0, 6).map((s) => (
                    <button key={s} className="btn tiny" onClick={() => setAdding(s)}>{s}</button>
                  ))}
                </div>
              </>
            )}
            {/* Stated ONCE, permanently, not as a badge on every row. */}
            <div className="pf-privacy">
              Your holdings are stored on this computer and are never sent anywhere.
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="symbol-page">
      <div className="sp-scroll">
        <div className="pf-head">
          <h2>Portfolio</h2>
          <span className="pf-count">
            {holdings.length} holding{holdings.length === 1 ? "" : "s"}, entered by you
          </span>
          <button className="btn" onClick={() => setAdding(true)}>
            <Plus size={13} strokeWidth={2} /> Add holding
          </button>
          {/* Manual, like the watchlist's. An auto-refreshing portfolio implies
              real-time data it does not have, and moves your own money around
              on screen while you are reading it. */}
          <button className="icon-btn-sm" title="Refresh prices" disabled={pfLoading}
                  onClick={loadPortfolio}>
            <RefreshCw size={13} strokeWidth={1.9} className={pfLoading ? "spin" : ""} />
          </button>
        </div>

        {/* THE LOADING LINE SAYS WHICH HALF IS PENDING. Quantities and cost
            basis are local and already final; only the valuation is in flight.
            Without that sentence a spinner over a portfolio reads as "your
            holdings are loading", which is the one thing that would be
            alarming — and untrue. */}
        {pfLoading && (
          <div className="pf-loading-note">
            <span className="spinner" />
            Your quantities and cost are local and already final. Fetching prices to value them.
          </div>
        )}

        {/* A PRICING failure, not a data failure — said in those words, because
            "couldn't load your holdings" when the holdings are right there
            tells the user their data is gone. */}
        {pfPricingFailed && (
          <div className="sp-error">
            <span>
              Your holdings are here, but Arthur couldn't price them just now.
              {pfError ? ` ${pfError}` : ""}
            </span>
            <button className="btn tiny" onClick={loadPortfolio}>Retry</button>
          </div>
        )}

        {/* Skeletons only when there is nothing yet to show. A REFRESH keeps
            the old figures on screen and dims nothing — they were true a
            minute ago, and blanking them to re-fetch the same numbers is a
            worse answer than a slightly stale one. */}
        {pfLoading && !currencies.length && (
          <div className="pf-totals">
            {["Total value", "Unrealised P/L", "Today"].map((label, i) => (
              <div className="pf-total" key={label}>
                <div className="pf-total-label">{label}</div>
                {/* Staggered so the three read as one loading group rather
                    than three unrelated pulses. */}
                <div className="pf-sk" style={{ animationDelay: `${i * 0.08}s` }} />
              </div>
            ))}
          </div>
        )}

        {currencies.map((cur) => {
          const t = totals[cur];
          return (
            <div className="pf-totals" key={cur}>
              <div className="pf-total">
                <div className="pf-total-label">Total value · {cur}</div>
                <div className="pf-total-value">{money(t.value, cur)}</div>
              </div>
              <div className="pf-total">
                <div className="pf-total-label">Unrealised P/L</div>
                <div className="pf-total-value"><Signed value={t.pl} pct={t.pl_pct} currency={cur} /></div>
              </div>
              <div className="pf-total">
                <div className="pf-total-label">Today</div>
                <div className="pf-total-value"><Signed value={t.day_change} currency={cur} /></div>
              </div>
              {t.unpriced > 0 && (
                <div className="pf-total-note">
                  {t.unpriced} holding{t.unpriced > 1 ? "s" : ""} not included — no price available
                </div>
              )}
              {/* A total that doesn't describe everything above it has to say
                  so. The value line still counts these — only the cost-derived
                  P/L excludes them. */}
              {!t.pl_covers_all && (
                <div className="pf-total-note">
                  P/L excludes {t.fx_blocked} holding{t.fx_blocked > 1 ? "s" : ""} bought
                  in a different currency from the one it trades in. Total value still includes
                  {t.fx_blocked > 1 ? " them" : " it"}.
                </div>
              )}
            </div>
          );
        })}
        {/* Only when it could actually mislead. */}
        {currencies.length > 1 && (
          <div className="pf-fx-note">
            Totals are shown per currency and not combined — Arthur doesn't fetch
            exchange rates, and a converted total would be a guess.
          </div>
        )}

        {adding && <AddForm initialSymbol={typeof adding === "string" ? adding : ""}
                            onDone={() => setAdding(false)} />}

        <div className="pf-table">
          {/* LABELS SAY PER-UNIT OR TOTAL, because mixing the two silently is
              what made this table hard to read: "Cost" was per share while
              "Value" was the whole position, so the two numbers sitting side
              by side could not be compared and nothing on screen said so.
              "Paid each" and "Price now" can only mean one thing.

              The titles are the column's definition — this is a screen full of
              terms of art, and a tooltip is cheaper than a legend nobody
              scrolls to. */}
          <div className="pf-row pf-header">
            <span title="The ticker Arthur prices, and what that ticker actually is">Holding</span>
            <span title="How many units you hold — shares, coins or ounces">Quantity</span>
            <span title="What you paid per unit, in the currency you paid in">Paid each</span>
            <span title="What one unit trades at now. Delayed about 15 minutes">Price now</span>
            <span title="Quantity x price now — what the whole position is worth today">Value</span>
            <span title="Value minus what you paid. Unrealised: nothing has been sold">P/L</span>
            <span />
          </div>
          {pfLoading && !holdings.length && [0, 1, 2, 3].map((i) => (
            <div className="pf-row" key={`sk${i}`}>
              <span className="pf-sym"><div className="pf-sk sm" style={{ animationDelay: `${i * 0.06}s` }} /></span>
              <span className="pf-num"><div className="pf-sk sm" /></span>
              <span className="pf-num"><div className="pf-sk sm" /></span>
              <span className="pf-num"><div className="pf-sk sm" /></span>
              <span className="pf-num"><div className="pf-sk sm" /></span>
              <span className="pf-num"><div className="pf-sk sm" /></span>
              <span />
            </div>
          ))}

          {holdings.map((h) => (editing === h.id ? (
            <EditRow key={h.id} h={h} onDone={() => setEditing(null)} />
          ) : (
            <div className={`pf-row${h.priced ? "" : " unpriced"}`} key={h.id}>
              <span className="pf-sym">
                <button className="pf-link" onClick={() => open(h.symbol)}>{h.symbol}</button>
                <span className="pf-name">{h.name}</span>
                {/* The percentage is arithmetically correct and almost
                    certainly meaningless — flagged rather than hidden, because
                    it is the user's data and only they know what they hold. */}
                {h.cost_suspect && (
                  <button className="pf-suspect"
                          title="Why this looks wrong"
                          onClick={() => setChecking(h)}>
                    <AlertTriangle size={11} /> check this
                  </button>
                )}
              </span>
              <span className="pf-num">{h.quantity}</span>
              {/* THE COST IS IN THE CURRENCY IT WAS PAID IN, not the quote's.
                  Formatting CA$88,784 as $88,784 is how a currency mismatch
                  hides in plain sight. */}
              <span className="pf-num">{money(h.cost_basis, h.cost_currency)}</span>
              <span className="pf-num">{h.priced ? money(h.price, h.currency) : "—"}</span>
              <span className="pf-num">{h.priced ? money(h.value, h.currency) : "—"}</span>
              <span className="pf-num">
                {!h.priced ? <span className="pf-unpriced-note">no price</span>
                  : h.fx_blocked ? (
                    <span className="pf-fx-blocked"
                          title={`Paid in ${h.cost_currency}, quoted in ${h.currency}. `
                            + "Arthur doesn't fetch exchange rates, so this can't be computed honestly."}>
                      {h.cost_currency} vs {h.currency}
                    </span>
                  ) : <Signed value={h.pl} pct={h.pl_pct} currency={h.currency} />}
              </span>
              <span className="pf-actions">
                <button className="icon-btn-sm" title={`Edit ${h.symbol}`}
                        onClick={() => setEditing(h.id)}>
                  <Pencil size={12} />
                </button>
                <button className="icon-btn-sm" title={`Remove ${h.symbol}`}
                        onClick={() => confirmRemove(h)}>
                  <Trash2 size={12} />
                </button>
              </span>
            </div>
          )))}
        </div>

        <div className="pf-foot">
          {/* THE QUESTION CARRIES THE PORTFOLIO WITH IT.
              The model has no tool that can read the holdings table — it is
              local, private data — so "explain my portfolio" on its own leaves
              it no choice but to ask which symbols the user owns, which is a
              question the screen already answers. Naming the positions and
              their day changes turns a dead end into a single tool call. */}
          <button
            className="btn"
            onClick={() => {
              if (!activeId) return;
              // BACK TO THE TRANSCRIPT, always. The answer arrives in the
              // conversation, so leaving the user on the portfolio means the
              // reply streams into a screen they cannot see.
              setView("watchlist");
              const priced = holdings.filter((h) => h.priced);
              if (!priced.length) return;
              const lines = priced.map((h) =>
                `${h.symbol} (${h.quantity} shares, ${typeof h.day_change === "number"
                  ? `${h.day_change >= 0 ? "+" : "−"}${money(Math.abs(h.day_change), h.currency)} today`
                  : "today's change unavailable"})`).join("; ");
              send(activeId,
                `Explain today's move across my holdings: ${lines}. `
                + "Use explain_move on the ones that moved most and tell me what drove them.",
                { mode: "finance" });
            }}
          >
            Explain today's move
          </button>
          <span className="sp-handoff-note">Answers appear in the conversation</span>
        </div>

        <div className="pf-privacy">
          Stored on this computer. Never sent anywhere. Prices are delayed ~15 min.
        </div>
      </div>
    </div>
  );
}
