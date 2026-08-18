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
import { Plus, Trash2 } from "lucide-react";
import { useFinance } from "../../stores/finance";
import { useConfirm } from "../../stores/confirm";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";

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
  const [f, setF] = useState({ symbol: initialSymbol, quantity: "", cost_basis: "", purchase_date: "" });
  const [busy, setBusy] = useState(false);

  const ready = f.symbol.trim() && Number(f.quantity) > 0 && f.cost_basis !== "";

  const submit = async (e) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    const ok = await addHolding(f);
    setBusy(false);
    if (ok) { setF({ symbol: "", quantity: "", cost_basis: "", purchase_date: "" }); onDone?.(); }
  };

  return (
    <form className="pf-add" onSubmit={submit}>
      <div className="pf-field">
        <label>Symbol</label>
        <input value={f.symbol} placeholder="AAPL" autoFocus
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
      <button className="btn primary" type="submit" disabled={!ready || busy}>
        {busy ? "Adding…" : "Add"}
      </button>
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

  useEffect(() => { loadPortfolio(); }, [loadPortfolio]);

  const currencies = Object.keys(totals);

  const confirmRemove = (h) => ask({
    title: `Remove ${h.symbol} from your portfolio?`,
    // Explicit about what cannot be recovered: unlike a chat, these numbers
    // were typed by hand and Arthur cannot fetch them again.
    body: `The ${h.quantity} shares and the ${money(h.cost_basis, h.currency)} `
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
              </span>
              <span className="pf-num">{h.quantity}</span>
              <span className="pf-num">{money(h.cost_basis, h.currency)}</span>
              <span className="pf-num">{h.priced ? money(h.price, h.currency) : "—"}</span>
              <span className="pf-num">{h.priced ? money(h.value, h.currency) : "—"}</span>
              <span className="pf-num">
                {h.priced ? <Signed value={h.pl} pct={h.pl_pct} currency={h.currency} />
                          : <span className="pf-unpriced-note">no price</span>}
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
          <button
            className="btn"
            onClick={() => activeId && send(activeId,
              "Explain today's move in my portfolio — which holdings drove it?",
              { mode: "finance" })}
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
