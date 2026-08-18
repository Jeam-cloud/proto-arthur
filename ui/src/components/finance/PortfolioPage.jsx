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
import { AlertTriangle, Plus, Trash2 } from "lucide-react";
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

  const ready = f.symbol.trim() && Number(f.quantity) > 0 && f.cost_basis !== "";

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
        <label>Shares</label>
        <input value={f.quantity} placeholder="40" inputMode="decimal"
               onChange={(e) => setF({ ...f, quantity: e.target.value })} />
      </div>
      <div className="pf-field">
        <label>Paid per share</label>
        <input value={f.cost_basis} placeholder="171.20" inputMode="decimal"
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
      {/* Named as optional so nobody stalls looking for it. Every extra
          required field is a person deciding not to bother. */}
      <div className="pf-add-note">Purchase date is optional and can be filled in later.</div>
    </form>
  );
}

export default function PortfolioPage() {
  const {
    holdings, totals, pfLoaded, pfLoading, pfPricingFailed, pfError,
    loadPortfolio, removeHolding, symbols, open,
  } = useFinance();
  const ask = useConfirm((s) => s.ask);
  const send = useChat((s) => s.send);
  const activeId = useConversations((s) => s.activeId);
  const [adding, setAdding] = useState(false);

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

  if (pfLoaded && !holdings.length) {
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
          <button className="btn" onClick={() => setAdding(true)}>
            <Plus size={13} strokeWidth={2} /> Add holding
          </button>
        </div>

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
          <div className="pf-row pf-header">
            <span>Holding</span><span>Shares</span><span>Cost</span>
            <span>Price</span><span>Value</span><span>P/L</span><span />
          </div>
          {holdings.map((h) => (
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
                <button className="icon-btn-sm" title={`Remove ${h.symbol}`}
                        onClick={() => confirmRemove(h)}>
                  <Trash2 size={12} />
                </button>
              </span>
            </div>
          ))}
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
