// The portfolio: what you own, valued against live prices.
//
// TRACKING, NOT BROKERAGE. Every number here was typed in by the user; Arthur
// prices it and computes the difference. Nothing on this screen connects to a
// broker, moves money, or could be mistaken for doing so.
//
// TOTALS ARE PER CURRENCY AND NEVER CONVERTED. Adding a EUR holding to a USD
// one needs an FX rate this app does not fetch, and one wrong total is worse
// than two right subtotals.
//
// NO ADVICE. No "consider rebalancing", no "overweight", no scoring. Arthur
// shows, the person decides.
//
// A REAL <table>, not a CSS grid. Eight columns need horizontal scroll on a
// narrow pane, and the edit bar needs to span the full width beneath its row —
// both are one attribute in a table and a fight in a grid.
import React, { useEffect, useState } from "react";
import { AlertTriangle, Download, Pencil, Plus, RefreshCw, Trash2, Upload } from "lucide-react";
import { useFinance } from "../../stores/finance";
import { useConfirm } from "../../stores/confirm";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";
import HoldingModal from "./HoldingModal";
import ImportModal from "./ImportModal";

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

const units = (n) =>
  typeof n === "number" ? n.toLocaleString(undefined, { maximumFractionDigits: 8 }) : "—";

// Direction is never carried by colour alone — the arrow is the primary signal.
function signed(v, currency) {
  if (typeof v !== "number" || !isFinite(v)) return null;
  return `${v >= 0 ? "▲ +" : "▼ −"}${money(Math.abs(v), currency)}`;
}
const dirClass = (v) =>
  typeof v !== "number" || !isFinite(v) ? "pf-flat" : v >= 0 ? "pf-up" : "pf-down";

function parseNum(raw) {
  const cleaned = String(raw ?? "").replace(/[\s,$£€]/g, "");
  const n = Number(cleaned);
  return cleaned !== "" && isFinite(n) ? n : null;
}

/** The inline edit bar, in its own full-width row BENEATH the holding.
 *
 *  Beneath rather than replacing, deliberately: the row you are correcting
 *  stays on screen while you correct it, so the current figures and the new
 *  ones are visible at once. Replacing the row hid the very numbers being
 *  changed.
 *
 *  No symbol here — see HoldingModal for why a ticker change is not an edit. */
function EditBar({ h, onDone }) {
  const updateHolding = useFinance((s) => s.updateHolding);
  const [f, setF] = useState({
    quantity: String(h.quantity),
    cost_basis: String(h.cost_basis),
    cost_currency: h.cost_currency || h.currency,
    costMode: "each",
  });
  const [busy, setBusy] = useState(false);

  const quantity = parseNum(f.quantity);
  const paid = parseNum(f.cost_basis);
  const costBasis = f.costMode === "total"
    ? (paid !== null && quantity > 0 ? paid / quantity : null)
    : paid;
  const ready = quantity !== null && quantity > 0 && costBasis !== null && costBasis >= 0;

  const save = async () => {
    if (!ready || busy) return;
    setBusy(true);
    const patch = {};
    if (quantity !== h.quantity) patch.quantity = quantity;
    if (costBasis !== h.cost_basis) patch.cost_basis = costBasis;
    if (f.cost_currency !== (h.cost_currency || h.currency)) patch.cost_currency = f.cost_currency;
    if (Object.keys(patch).length) await updateHolding(h.id, patch);
    setBusy(false);
    onDone();
  };

  return (
    <tr className="pf-editrow">
      <td colSpan={8}>
        <div
          className="pf-editbar"
          onKeyDown={(e) => {
            // A form in all but tag name. One you can only leave with the
            // mouse is a trap.
            if (e.key === "Enter") { e.preventDefault(); save(); }
            if (e.key === "Escape") { e.preventDefault(); onDone(); }
          }}
        >
          <div className="pf-ef static">
            <label>Holding</label>
            <div className="pf-ef-static">
              <span className="pf-ef-sym">{h.symbol}</span>
              <span className="pf-ef-cur">{h.currency}</span>
            </div>
          </div>
          <div className="pf-ef qty">
            <label htmlFor={`q-${h.id}`}>Quantity</label>
            <input id={`q-${h.id}`} autoFocus value={f.quantity} inputMode="decimal"
                   onChange={(e) => setF({ ...f, quantity: e.target.value })} />
          </div>
          <div className="pf-ef paid">
            <label htmlFor={`b-${h.id}`}>
              Paid
              <span className="pf-mode">
                <button type="button" className={f.costMode === "each" ? "on" : ""}
                        onClick={() => setF({ ...f, costMode: "each" })}>each</button>
                <button type="button" className={f.costMode === "total" ? "on" : ""}
                        onClick={() => setF({ ...f, costMode: "total" })}>total</button>
              </span>
            </label>
            <input id={`b-${h.id}`} value={f.cost_basis} inputMode="decimal"
                   onChange={(e) => setF({ ...f, cost_basis: e.target.value })} />
          </div>
          <div className="pf-ef cur">
            <label htmlFor={`c-${h.id}`}>In</label>
            <select id={`c-${h.id}`} value={f.cost_currency}
                    onChange={(e) => setF({ ...f, cost_currency: e.target.value })}>
              {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          {quantity > 0 && costBasis !== null && (
            <div className="pf-ef-derived">
              {f.costMode === "total"
                ? `= ${money(costBasis, f.cost_currency)} per unit across ${units(quantity)} units`
                : `= ${money(costBasis * quantity, f.cost_currency)} in total across ${units(quantity)} units`}
            </div>
          )}
          <div className="pf-ef-actions">
            <button type="button" className="btn" onClick={onDone} disabled={busy}>Cancel</button>
            <button type="button" className="btn primary" onClick={save} disabled={!ready || busy}>
              {busy ? "…" : "Save"}
            </button>
          </div>
        </div>
        <div className="pf-edithint">Enter saves · Escape cancels</div>
      </td>
    </tr>
  );
}

function Row({ h, loading, onOpen, onEdit, onRemove, onSuspect }) {
  const noPrice = !h.priced;
  return (
    <tr className={noPrice ? "pf-tr dim" : "pf-tr"} onClick={() => onOpen(h.symbol)}>
      <td className="c-sym">
        <div className="pf-symline">
          <span className="pf-sym-t">{h.symbol}</span>
          <span className="pf-sym-cur">{h.currency}</span>
        </div>
        <div className="pf-sym-name">{h.name}</div>
        {/* Amber, never red: nothing failed, and the user may well be right.
            An invitation to look, not a verdict. */}
        {h.cost_suspect && (
          <button className="pf-suspect"
                  onClick={(e) => { e.stopPropagation(); onSuspect(h); }}>
            <AlertTriangle size={11} /> check this
          </button>
        )}
      </td>
      <td className="c-num">{units(h.quantity)}</td>
      {/* Cost is printed in the currency it was PAID in, which is not always
          the one the instrument quotes in. */}
      {/* BOTH NUMBERS, because only one of them was ever typed.
          "Paid each" is derived — you put in $56.06 for a third of a share and
          Arthur divided it out to $176.51. The per-unit figure has to lead,
          since it is the only one on the same scale as "Price now" and so the
          only one that can be compared to it. But showing it alone hides the
          number the user actually recognises from their own bank statement,
          which is what made this column need explaining twice. */}
      <td className="c-num mut">
        <div>
          {money(h.cost_basis, h.cost_currency)}
          {h.fx_blocked && <span className="pf-costcur">{h.cost_currency}</span>}
        </div>
        <div className="pf-costtotal">
          {money(h.cost_basis * h.quantity, h.cost_currency)} total
        </div>
      </td>
      <td className="c-num mut">{noPrice ? "—" : money(h.price, h.currency)}</td>
      <td className="c-num">{noPrice ? "—" : money(h.value, h.currency)}</td>
      <td className="c-pl">
        {loading ? <span className="pf-sk cell" />
          : noPrice ? (
            <span className="pf-noprice">no price</span>
          ) : h.fx_blocked ? (
            <span className="pf-fx-blocked"
                  title={`You paid in ${h.cost_currency} and this quotes in ${h.currency}. `
                    + "Arthur doesn't fetch exchange rates, so it won't show a profit it can't compute."}>
              {h.cost_currency} vs {h.currency}
            </span>
          ) : (
            <>
              <div className={`pf-pl ${dirClass(h.pl)}`}>
                {h.pl >= 0 ? "+" : "−"}{money(Math.abs(h.pl), h.currency)}
              </div>
              {typeof h.pl_pct === "number" && (
                <div className={`pf-plpct ${dirClass(h.pl)}`}>
                  {h.pl >= 0 ? "▲ +" : "▼ −"}{Math.abs(h.pl_pct).toFixed(2)}%
                </div>
              )}
            </>
          )}
      </td>
      <td className="c-pl">
        {loading ? <span className="pf-sk cell sm" />
          : typeof h.day_change === "number" ? (
            <span className={dirClass(h.day_change)}>{signed(h.day_change, h.currency)}</span>
          ) : (
            <span className="pf-flat" title={noPrice
              ? "No price for this holding yet, so today's change can't be computed."
              : "No previous close for this listing, so today's change can't be computed."}>—</span>
          )}
      </td>
      <td className="c-act">
        {/* Visible at rest, brightening on hover. Hidden-until-hover made the
            controls a guessing game, and keyboard users never hover at all. */}
        <div className="pf-acts">
          <button title={`Edit ${h.symbol}`}
                  onClick={(e) => { e.stopPropagation(); onEdit(h); }}>
            <Pencil size={13} strokeWidth={1.8} />
          </button>
          <button className="danger" title={`Remove ${h.symbol}`}
                  onClick={(e) => { e.stopPropagation(); onRemove(h); }}>
            <Trash2 size={13} strokeWidth={1.8} />
          </button>
        </div>
      </td>
    </tr>
  );
}

export default function PortfolioPage() {
  const {
    holdings, totals, pfLoaded, pfLoading, pfPricingFailed, pfError,
    loadPortfolio, removeHolding, symbols, open, setView, exportPortfolio,
  } = useFinance();
  const ask = useConfirm((s) => s.ask);
  const send = useChat((s) => s.send);
  const activeId = useConversations((s) => s.activeId);
  const [modal, setModal] = useState(null);   // {holding} | {initialSymbol} | null
  const [importing, setImporting] = useState(false);
  const [editing, setEditing] = useState(null);

  useEffect(() => { loadPortfolio(); }, [loadPortfolio]);

  const currencies = Object.keys(totals);
  // The currency holding the most value leads; the rest are stated beneath the
  // table rather than repeating the whole card group. Three cards per currency
  // buries the portfolio you actually have under the one you barely do.
  const primary = currencies.slice().sort((a, b) => totals[b].value - totals[a].value)[0];
  const secondary = currencies.filter((c) => c !== primary);
  const t = primary ? totals[primary] : null;

  const explainSuspect = (h) => ask({
    title: `${h.symbol} — these figures look off`,
    body:
      `You entered ${money(h.cost_basis, h.cost_currency)} per unit, but ${h.symbol} `
      + `(${h.name}) trades at ${money(h.price, h.currency)}. That gap is too large `
      + "to be a market move.\n\n"
      + "Two usual causes. The cost may be a TOTAL rather than a per-unit price — "
      + "open the edit bar and switch Paid to \"total\". Or the ticker isn't the thing "
      + "you hold: plain \"BTC\" is the Grayscale Bitcoin Mini Trust, not bitcoin, and "
      + "\"XRP\" is the Bitwise XRP ETF, not the token. The coins are BTC-USD and XRP-USD.\n\n"
      + "This holding is left out of the total P/L until it's resolved, because counting "
      + "it would swamp the figure. Your numbers are exactly as you typed them.",
    confirmLabel: "Got it",
  });

  const confirmRemove = (h) => ask({
    title: `Remove your ${h.symbol} holding?`,
    body: `You entered ${units(h.quantity)} units at ${money(h.cost_basis, h.cost_currency)} `
      + "each. Arthur can't fetch this back — you'd have to type it in again.",
    confirmLabel: "Remove holding",
    onConfirm: () => removeHolding(h.id),
  });

  const explainMove = () => {
    if (!activeId) return;
    const priced = holdings.filter((h) => h.priced);
    if (!priced.length) return;
    setView("watchlist");     // the answer lands in the transcript, so go there
    const lines = priced.map((h) =>
      `${h.symbol} (${units(h.quantity)} units, ${typeof h.day_change === "number"
        ? `${h.day_change >= 0 ? "+" : "−"}${money(Math.abs(h.day_change), h.currency)} today`
        : "today's change unavailable"})`).join("; ");
    send(activeId,
      `Explain today's move across my holdings: ${lines}. `
      + "Use explain_move on the ones that moved most and tell me what drove them.",
      { mode: "finance" });
  };

  if (pfLoaded && !pfLoading && !holdings.length) {
    return (
      <div className="symbol-page">
        <div className="sp-scroll">
          <div className="pf-empty">
            <h3>Nothing entered yet</h3>
            <p>
              Type what you hold and Arthur values it against the current price.
              Three fields per holding, about thirty seconds.
            </p>
            <div className="pf-empty-actions">
              <button className="btn primary" onClick={() => setModal({})}>
                <Plus size={13} strokeWidth={2} /> Add a holding
              </button>
              {/* Anyone arriving with a portfolio already somewhere else should
                  not have to type it in one row at a time. */}
              <button className="btn" onClick={() => setImporting(true)}>
                <Upload size={13} strokeWidth={1.8} /> Import a CSV
              </button>
            </div>
            {symbols.length > 0 && (
              <>
                <div className="pf-or">Or start from something on your watchlist:</div>
                <div className="pf-chips">
                  {symbols.slice(0, 6).map((s) => (
                    <button key={s} className="btn tiny"
                            onClick={() => setModal({ initialSymbol: s })}>{s}</button>
                  ))}
                </div>
              </>
            )}
            <div className="pf-privacy">
              Your holdings are stored on this computer and are never sent anywhere.
            </div>
          </div>
        </div>
        {modal && <HoldingModal {...modal} onClose={() => setModal(null)} />}
        {importing && <ImportModal onClose={() => setImporting(false)} />}
      </div>
    );
  }

  const priced = holdings.filter((h) => h.priced).length;

  return (
    <div className="symbol-page">
      <div className="sp-scroll">
        <div className="pf-head">
          <h2>Portfolio</h2>
          <span className="pf-count">
            {holdings.length} holding{holdings.length === 1 ? "" : "s"}, entered by you
          </span>
          {/* Export before import, left to right: the safe one first, and the
              one you should do BEFORE the destructive one. */}
          <button className="icon-btn-sm" title="Export holdings as CSV"
                  disabled={!holdings.length} onClick={exportPortfolio}>
            <Download size={13} strokeWidth={1.9} />
          </button>
          <button className="icon-btn-sm" title="Import holdings from CSV"
                  onClick={() => setImporting(true)}>
            <Upload size={13} strokeWidth={1.9} />
          </button>
          <button className="icon-btn-sm" title="Refresh prices" disabled={pfLoading}
                  onClick={loadPortfolio}>
            <RefreshCw size={13} strokeWidth={1.9} className={pfLoading ? "spin" : ""} />
          </button>
        </div>

        {/* A PRICING failure, not a data failure — said in those words, because
            "couldn't load your holdings" when they're right there tells the
            user their data is gone. */}
        {pfPricingFailed && !pfLoading && (
          <div className="sp-error">
            <span>Your holdings are intact. Only the valuation is unavailable.
              {pfError ? ` ${pfError}` : ""}</span>
            <button className="btn tiny" onClick={loadPortfolio}>Retry</button>
          </div>
        )}

        <div className="pf-totals">
          <div className="pf-total">
            <div className="pf-total-label">
              Total value · {primary || "—"}
              {holdings.length > 0 && (
                <span className="pf-chip">{priced} of {holdings.length} priced</span>
              )}
            </div>
            {pfLoading && !t ? <div className="pf-sk" />
              : <div className="pf-total-value">{money(t?.value, primary)}</div>}
            {t?.unpriced > 0 && (
              <div className="pf-total-note">
                {t.unpriced} holding{t.unpriced > 1 ? "s" : ""} not counted — no price yet.
              </div>
            )}
          </div>

          <div className="pf-total">
            <div className="pf-total-label">Unrealised P/L</div>
            {pfLoading && !t ? <div className="pf-sk" style={{ animationDelay: "0.08s" }} />
              : (
                <>
                  <div className={`pf-total-value ${dirClass(t?.pl)}`}>
                    {t && typeof t.pl === "number"
                      ? `${t.pl >= 0 ? "+" : "−"}${money(Math.abs(t.pl), primary)}` : "—"}
                  </div>
                  {typeof t?.pl_pct === "number" && (
                    <div className={`pf-total-sub ${dirClass(t.pl)}`}>
                      {t.pl >= 0 ? "▲ +" : "▼ −"}{Math.abs(t.pl_pct).toFixed(2)}%
                    </div>
                  )}
                </>
              )}
            {/* A total that doesn't describe everything above it has to say so. */}
            {t?.fx_blocked > 0 && (
              <div className="pf-total-note">
                P/L excludes {t.fx_blocked} holding{t.fx_blocked > 1 ? "s" : ""} bought in a
                different currency to the one it quotes in.
              </div>
            )}
            {t?.suspect > 0 && (
              <div className="pf-total-note warn">
                P/L also excludes {t.suspect_symbols.join(", ")} — the cost entered looks like
                it may be a total rather than a per-unit price. Counting it would swamp
                this figure.
              </div>
            )}
          </div>

          <div className="pf-total">
            <div className="pf-total-label">Today, your position</div>
            {pfLoading && !t ? <div className="pf-sk" style={{ animationDelay: "0.16s" }} />
              : (
                <div className={`pf-total-value ${dirClass(t?.day_change)}`}>
                  {t && typeof t.day_change === "number"
                    ? signed(t.day_change, primary) : "—"}
                </div>
              )}
            <div className="pf-total-sub mut">Delayed ~15 min</div>
          </div>
        </div>

        {/* Names which half is pending. A bare spinner over a portfolio reads
            as "your holdings are loading" — alarming, and untrue. */}
        {pfLoading && (
          <div className="pf-loading-note">
            <span className="spinner" />
            Your quantities and cost are local and already final. Fetching prices to value them.
          </div>
        )}

        <div className="pf-card">
          <div className="pf-card-head">
            <span className="pf-card-title">Holdings</span>
            <button className="btn" onClick={() => setModal({})}>
              <Plus size={13} strokeWidth={2} /> Add holding
            </button>
          </div>

          <div className="pf-scroll">
            <table className="pf-table">
              <thead>
                <tr>
                  <th title="The ticker as the source resolved it, with the name it returned.">Holding</th>
                  <th title="How many units you told Arthur you hold.">Quantity</th>
                  <th title="What you paid for ONE unit, with your total underneath. Shown per-unit so it can be compared with Price now.">Paid each</th>
                  <th title="The latest quote for one unit, delayed about 15 minutes.">Price now</th>
                  <th title="Quantity multiplied by the current price.">Value</th>
                  <th title="Value today minus what you paid. Withheld when the two currencies differ.">P/L</th>
                  <th title="Change in this position's value since yesterday's close.">Today</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {pfLoading && !holdings.length && [0, 1, 2, 3].map((i) => (
                  <tr className="pf-tr" key={`sk${i}`}>
                    <td className="c-sym"><span className="pf-sk cell wide" /></td>
                    {[1, 2, 3, 4, 5, 6].map((c) => (
                      <td className="c-num" key={c}><span className="pf-sk cell" /></td>
                    ))}
                    <td />
                  </tr>
                ))}
                {holdings.map((h) => [
                  <Row key={h.id} h={h} loading={pfLoading && !h.priced}
                       onOpen={open} onEdit={(x) => setEditing(x.id)}
                       onRemove={confirmRemove} onSuspect={explainSuspect} />,
                  editing === h.id
                    ? <EditBar key={`${h.id}-e`} h={h} onDone={() => setEditing(null)} />
                    : null,
                ])}
              </tbody>
            </table>
          </div>

          {/* Other currencies live here rather than as another card group —
              stated, kept separate, and never added to the figure above. */}
          {secondary.map((cur) => (
            <div className="pf-otherccy" key={cur}>
              <span className="pf-otherccy-label">Held in {cur}, kept separate:</span>
              <span className="pf-otherccy-val">{money(totals[cur].value, cur)}</span>
              {typeof totals[cur].pl === "number" && (
                <>
                  <span className={dirClass(totals[cur].pl)}>
                    {totals[cur].pl >= 0 ? "+" : "−"}{money(Math.abs(totals[cur].pl), cur)}
                  </span>
                  {typeof totals[cur].pl_pct === "number" && (
                    <span className="pf-otherccy-pct">
                      {totals[cur].pl >= 0 ? "▲ +" : "▼ −"}{Math.abs(totals[cur].pl_pct).toFixed(2)}%
                    </span>
                  )}
                </>
              )}
              <span className="pf-otherccy-note">
                Arthur doesn't fetch exchange rates, so it won't add currencies together —
                a wrong total is worse than no total.
              </span>
            </div>
          ))}
        </div>

        <div className="pf-foot">
          <button className="btn" onClick={explainMove}>Explain today's move in my portfolio</button>
          <span className="sp-handoff-note">Answers appear in the conversation</span>
        </div>

        <div className="pf-privacy">
          Stored on this computer. Never sent anywhere. Prices are delayed ~15 min.
        </div>
      </div>

      {modal && <HoldingModal {...modal} onClose={() => setModal(null)} />}
      {importing && <ImportModal onClose={() => setImporting(false)} />}
    </div>
  );
}
