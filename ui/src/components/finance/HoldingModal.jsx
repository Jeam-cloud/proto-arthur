// Add or edit a holding.
//
// A MODAL, NOT AN INLINE FORM. Entering a position is a deliberate, low
// frequency act with five fields and a lookup that talks about what you are
// about to save — that wants the whole of your attention, and inline it was
// competing with a table of numbers directly beneath it.
//
// ONE COMPONENT FOR BOTH JOBS, because they are the same form. The only
// difference is that editing cannot change the SYMBOL: a different ticker is a
// different holding, and quietly repointing a cost basis at another instrument
// is how you get a position that looks reasonable and describes nothing. The
// field goes read-only and says what to do instead.
import React, { useEffect, useRef, useState } from "react";
import { AlertCircle, Check, Info, Lock, Trash2 } from "lucide-react";
import { useFinance } from "../../stores/finance";

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

const qty = (n) => n.toLocaleString(undefined, { maximumFractionDigits: 8 });

/** Typed text -> a number, or null. See the 422 this used to cause: Number()
 *  turns "88,784.00" into NaN, JSON.stringify writes NaN as null, and the API
 *  rejects a field the user can see they filled in. */
function parseNum(raw) {
  const cleaned = String(raw ?? "").replace(/[\s,$£€]/g, "");
  const n = Number(cleaned);
  return cleaned !== "" && isFinite(n) ? n : null;
}

export default function HoldingModal({ holding, initialSymbol = "", onClose }) {
  const { addHolding, updateHolding, removeHolding, resolveSymbol } = useFinance();
  const editing = !!holding;
  const [f, setF] = useState({
    symbol: holding?.symbol || initialSymbol,
    quantity: holding ? String(holding.quantity) : "",
    cost_basis: holding ? String(holding.cost_basis) : "",
    purchase_date: holding?.purchase_date || "",
    cost_currency: holding?.cost_currency || "",
    costMode: "each",
  });
  const [found, setFound] = useState(null);
  const [busy, setBusy] = useState(false);
  const symRef = useRef(null);

  const quantity = parseNum(f.quantity);
  const paid = parseNum(f.cost_basis);
  // ONE PLACE CONVERTS. `cost_basis` means per-unit everywhere else — in the
  // store, the API and the table — so total mode divides here and nowhere else.
  const costBasis = f.costMode === "total"
    ? (paid !== null && quantity > 0 ? paid / quantity : null)
    : paid;
  const badNumber = (f.quantity !== "" && quantity === null)
    || (f.cost_basis !== "" && paid === null);
  const ready = f.symbol.trim() && quantity !== null && quantity > 0
    && costBasis !== null && costBasis >= 0;

  const currency = f.cost_currency || found?.currency || holding?.currency || "USD";

  useEffect(() => {
    // A ticker arriving from a watchlist chip is already known — resolve it at
    // once rather than waiting for a blur that will never come.
    if (!editing && initialSymbol) lookup(initialSymbol);
    else if (!editing) symRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const lookup = async (raw) => {
    const sym = String(raw ?? f.symbol).trim().toUpperCase();
    if (!sym || sym === found?.symbol) return;
    setFound(await resolveSymbol(sym) || { down: true, symbol: sym });
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    let ok;
    if (editing) {
      // Only what changed. A PATCH that resends every field makes each save
      // look like a full rewrite of a row the user barely touched.
      const patch = {};
      if (quantity !== holding.quantity) patch.quantity = quantity;
      if (costBasis !== holding.cost_basis) patch.cost_basis = costBasis;
      if ((f.cost_currency || null) !== (holding.cost_currency || null)) {
        patch.cost_currency = f.cost_currency || null;
      }
      if ((f.purchase_date || "") !== (holding.purchase_date || "")) {
        patch.purchase_date = f.purchase_date || null;
      }
      ok = Object.keys(patch).length ? await updateHolding(holding.id, patch) : true;
    } else {
      ok = await addHolding({
        ...f, quantity, cost_basis: costBasis,
        cost_currency: f.cost_currency && f.cost_currency !== found?.currency
          ? f.cost_currency : null,
      });
    }
    setBusy(false);
    if (ok) onClose();
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form
        className="modal hold-modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}
        onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}
      >
        <div className="hm-head">
          <h3>{editing ? `Edit your ${holding.symbol} holding` : "Add a holding"}</h3>
          <p>
            {editing
              ? "Bought more, or sold some? Change the numbers and save — Arthur revalues it at the current price."
              : "Three fields. Arthur values it against the current price — it never connects to a broker."}
          </p>
        </div>

        <div className="hm-body">
          <div className="hm-field">
            <label htmlFor="hm-sym">Symbol</label>
            <input
              id="hm-sym" ref={symRef} value={f.symbol} placeholder="AAPL"
              readOnly={editing} className={editing ? "locked" : ""}
              onBlur={() => !editing && lookup()}
              onChange={(e) => { setF({ ...f, symbol: e.target.value.toUpperCase() }); setFound(null); }}
            />
            {editing && (
              <div className="hm-note">
                <Lock size={11} /> Remove the holding and add it again to change the symbol.
              </div>
            )}

            {/* THE LOOKUP, IN THREE OUTCOMES. This is the element that catches
                a ticker meaning something other than what you think — plain
                BTC is a Grayscale trust, XRP is an ETF — and it only works if
                it fires while the cost basis is still being typed. */}
            {!editing && found?.ok && (
              <div className="hm-resolve ok">
                <Check size={14} strokeWidth={2.1} />
                <div>
                  <strong>{found.symbol}</strong> is <b>{found.name}</b>, trading at{" "}
                  <strong>{money(found.price, found.currency)}</strong>.{" "}
                  <span>If that isn't what you hold, check the ticker.</span>
                </div>
              </div>
            )}
            {!editing && found && !found.ok && found.unknown && (
              <div className="hm-resolve warn">
                <AlertCircle size={14} strokeWidth={1.9} />
                <div>
                  No quote found for <strong>{found.symbol}</strong>. You can still save it —
                  it will show your figures without a price.{" "}
                  <span>
                    Listings outside the US often need a suffix, like{" "}
                    <code>{found.symbol}.TO</code> or <code>{found.symbol}.L</code>.
                  </span>
                </div>
              </div>
            )}
            {!editing && found && !found.ok && !found.unknown && (
              <div className="hm-resolve muted">
                <Info size={14} strokeWidth={1.9} />
                <div>
                  Arthur can't look up tickers right now, so it can't confirm what this one
                  is. Saving still works — check the name once prices are back.
                </div>
              </div>
            )}
          </div>

          {/* TWO EQUAL COLUMNS, and the labels are held to one height so the
              inputs beneath them share a baseline. The "Paid" label carries a
              toggle and is therefore taller than the others — left alone, it
              pushed its own input down and the row read as broken. */}
          <div className="hm-row">
            <div className="hm-field">
              <label htmlFor="hm-qty">Units</label>
              <input id="hm-qty" value={f.quantity} placeholder="40" inputMode="decimal"
                     onChange={(e) => setF({ ...f, quantity: e.target.value })} />
            </div>
            <div className="hm-field">
              <label htmlFor="hm-date">Purchase date <span className="opt">optional</span></label>
              <input id="hm-date" value={f.purchase_date} placeholder="2024-02-03"
                     onChange={(e) => setF({ ...f, purchase_date: e.target.value })} />
            </div>
          </div>

          {/* Anyone buying on a schedule has no single purchase price — they
              have twenty-six. What they DO have is the broker's book value.
              Asking for per-unit makes that person divide first, and division
              is where the wrong cost bases came from.
              The currency sits INSIDE the field rather than beside it: it is a
              unit on this number, not a separate question, and a third box in
              the row was what made the form feel cramped. */}
          <div className="hm-field">
            <label htmlFor="hm-paid">
              Paid
              <span className="pf-mode">
                <button type="button" className={f.costMode === "each" ? "on" : ""}
                        onClick={() => setF({ ...f, costMode: "each" })}>each</button>
                <button type="button" className={f.costMode === "total" ? "on" : ""}
                        onClick={() => setF({ ...f, costMode: "total" })}>total</button>
              </span>
            </label>
            <div className="hm-combo">
              <input id="hm-paid" value={f.cost_basis} inputMode="decimal"
                     placeholder={f.costMode === "total" ? "675.34" : "171.20"}
                     onChange={(e) => setF({ ...f, cost_basis: e.target.value })} />
              <select aria-label="Currency paid in" value={currency}
                      onChange={(e) => setF({ ...f, cost_currency: e.target.value })}>
                {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>

          {/* Beside the fields, before any request — "Request failed (422)"
              tells nobody which box to fix. */}
          {badNumber && (
            <div className="hm-badnum">
              <AlertCircle size={13} strokeWidth={1.9} />
              <span>Enter digits only — <code>88784.00</code>, not <code>$88,784.00</code>.</span>
            </div>
          )}

          {/* THE ARITHMETIC, SHOWN BOTH WAYS. Whichever number you typed, the
              other one is the one you did not — and it is the one that will
              appear in the table. */}
          {quantity > 0 && costBasis !== null && (
            <div className="hm-derived">
              {f.costMode === "total"
                ? <>= <strong>{money(costBasis, currency)}</strong> per unit across {qty(quantity)} units</>
                : <>= <strong>{money(costBasis * quantity, currency)}</strong> in total across {qty(quantity)} units</>}
            </div>
          )}

          <div className="hm-privacy">
            <Lock size={13} strokeWidth={1.8} />
            <span>Saved to a file on this computer. No account, no sync, nothing sent anywhere.</span>
          </div>
        </div>

        <div className="hm-foot">
          <button className="btn primary" type="submit" disabled={!ready || busy}>
            {busy ? "Saving…" : editing ? "Save changes" : "Add holding"}
          </button>
          <button className="btn" type="button" onClick={onClose}>Cancel</button>
          {editing && (
            <>
              <span className="hm-spacer" />
              <button
                type="button" className="btn hm-remove"
                onClick={async () => { await removeHolding(holding.id); onClose(); }}
              >
                <Trash2 size={13} strokeWidth={1.8} /> Remove holding
              </button>
            </>
          )}
        </div>
      </form>
    </div>
  );
}
