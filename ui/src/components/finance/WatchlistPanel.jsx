// Finance mode's watchlist — the panel the mode opens to.
//
// WHY THIS IS THE FIRST FINANCE SCREEN: across the research, watchlists are
// what people actually use a finance app for. Charting is a smaller, more
// specialised audience, and the tools that serve it well are already very
// good. What Arthur can offer that they cannot is a list you can then ASK
// QUESTIONS ABOUT — so the list comes first and the chat stays the spine.
//
// EVERY NUMBER HERE CARRIES ITS AGE. Yahoo's feed is ~15 minutes delayed and
// the backend caches on top of that, so a price can be half an hour old while
// looking current. The footer prints when the data was taken, and there is a
// manual Refresh rather than a ticker that updates itself: an auto-refreshing
// price implies real-time, and it moves numbers while someone is reading them.
import React, { useEffect, useRef, useState } from "react";
import { ArrowUp, Copy, MessageSquare, Plus, RefreshCw, Trash2, X } from "lucide-react";
import ContextMenu from "../ContextMenu";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";
import { useToasts } from "../../stores/toasts";
import { copyToClipboard } from "../../lib/clipboard";
import { sparkPath, useFinance } from "../../stores/finance";

// Direction is never carried by colour alone — roughly 1 in 12 men cannot
// separate red from green reliably, and this is the screen where confusing up
// with down costs the most. The arrow is the primary signal; the colour
// reinforces it.
function Change({ pct }) {
  if (typeof pct !== "number" || !isFinite(pct)) {
    // No previous close came back, so there is no honest change to show.
    return <div className="wl-chg flat">—</div>;
  }
  const up = pct >= 0;
  return (
    <div className={`wl-chg ${up ? "up" : "down"}`}>
      {up ? "▲" : "▼"} {up ? "+" : ""}{pct.toFixed(2)}%
    </div>
  );
}

function Sparkline({ values, pct }) {
  const { line, area } = sparkPath(values);
  if (!line) return <div className="wl-spark-empty" aria-hidden="true" />;
  const dir = typeof pct === "number" && pct < 0 ? "down" : "up";
  return (
    <svg className={`wl-spark ${dir}`} viewBox="0 0 72 24" preserveAspectRatio="none" aria-hidden="true">
      <path className="wl-spark-area" d={area} />
      <path className="wl-spark-line" d={line} />
    </svg>
  );
}

function money(value, currency) {
  if (typeof value !== "number" || !isFinite(value)) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency", currency: currency || "USD",
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(value);
  } catch {
    // An unknown currency code from upstream must not blank the row.
    return value.toFixed(2);
  }
}

function updatedAt(epochSeconds) {
  if (!epochSeconds) return null;
  return new Date(epochSeconds * 1000)
    .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export default function WatchlistPanel() {
  const {
    symbols, rows, fetchedAt, loading, loaded, error, openSymbol,
    load, refresh, add, remove, dismissError, open, reorder,
  } = useFinance();
  // HTML5 drag, not a library: the list is short, the rows are uniform,
  // and a drag-and-drop dependency for one panel is not a trade worth
  // making. `dragFrom` is a ref because it changes mid-gesture and must
  // not re-render the row being dragged.
  const dragFrom = useRef(null);
  const [dragOver, setDragOver] = useState(null);
  // {sym, x, y} — the same ContextMenu the sidebar uses, rather than a second
  // implementation of viewport-edge clamping.
  const [menu, setMenu] = useState(null);
  const send = useChat((s) => s.send);
  const activeId = useConversations((s) => s.activeId);
  const pushToast = useToasts((s) => s.push);

  // Right-click actions. "Explain" follows the same rule as the symbol page:
  // the question goes to the conversation, because that is where answers live.
  const menuItems = (sym) => [
    { label: "Open page", icon: ArrowUp, onClick: () => open(sym) },
    {
      label: "Explain today's move", icon: MessageSquare,
      onClick: () => activeId && send(activeId, `Why did ${sym} move today? Use explain_move.`, { mode: "finance" }),
    },
    {
      label: "Copy symbol", icon: Copy,
      onClick: async () => {
        await copyToClipboard({ text: sym });
        pushToast(`${sym} copied.`, "success");
      },
    },
    { divider: true },
    { label: "Remove from watchlist", icon: Trash2, danger: true, onClick: () => remove(sym) },
  ];
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => { load(); }, [load]);

  const submit = (e) => {
    e.preventDefault();
    if (!draft.trim()) return;
    add(draft);
    setDraft("");
    setAdding(false);
  };

  const stamp = updatedAt(fetchedAt);

  return (
    <div className="watchlist">
      <div className="wl-head">
        <span className="wl-title">Watchlist</span>
        <button
          className="icon-btn-sm" title="Add a symbol"
          onClick={() => setAdding((v) => !v)}
        >
          <Plus size={13} strokeWidth={2} />
        </button>
        <button
          className="icon-btn-sm" title="Refresh prices"
          disabled={loading}
          onClick={() => refresh({ force: true })}
        >
          <RefreshCw size={13} strokeWidth={1.9} className={loading ? "spin" : ""} />
        </button>
      </div>

      {adding && (
        <form className="wl-add" onSubmit={submit}>
          {/* A real ticker as the placeholder, not "e.g. symbol" — an example
              of valid input teaches the format in one glance. */}
          <input
            autoFocus value={draft}
            placeholder="NVDA"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => !draft.trim() && setAdding(false)}
            onKeyDown={(e) => e.key === "Escape" && setAdding(false)}
          />
          <button className="btn tiny" type="submit">Add</button>
        </form>
      )}

      <div className="wl-rows">
        {/* Skeletons, not a spinner: the row count is already known from the
            symbol list, so the panel can hold its final shape while the prices
            land instead of collapsing and reflowing. */}
        {loading && !Object.keys(rows).length && symbols.map((s) => (
          <div className="wl-row skeleton" key={s}>
            <div className="sk sk-sym" /><div className="sk sk-spark" /><div className="sk sk-price" />
          </div>
        ))}

        {symbols.map((sym, i) => {
          const row = rows[sym];
          if (!row) return null;

          // A symbol that failed stays IN PLACE with a retry. Dropping it would
          // look like the user removed it, and the neighbours fetched fine —
          // one bad ticker must not blank the list.
          if (row.failed) {
            return (
              <div className="wl-row failed" key={sym}>
                <span className="wl-sym">{sym}</span>
                <span className="wl-failed-note">didn't load</span>
                <button className="btn tiny" onClick={() => refresh({ force: true })}>Retry</button>
              </div>
            );
          }

          return (
            <button
              className={`wl-row${sym === openSymbol ? " open" : ""}`
                + (dragOver === i ? " drop-target" : "")}
              key={sym}
              draggable
              onDragStart={() => { dragFrom.current = i; }}
              onDragOver={(e) => { e.preventDefault(); setDragOver(i); }}
              onDragLeave={() => setDragOver((v) => (v === i ? null : v))}
              onDrop={(e) => {
                e.preventDefault();
                if (dragFrom.current !== null) reorder(dragFrom.current, i);
                dragFrom.current = null;
                setDragOver(null);
              }}
              onDragEnd={() => { dragFrom.current = null; setDragOver(null); }}
              onClick={() => open(sym)}
              onContextMenu={(e) => { e.preventDefault(); setMenu({ sym, x: e.clientX, y: e.clientY }); }}
              title={`Open ${sym}`}
            >
              <div className="wl-id">
                <div className="wl-sym">{sym}</div>
                <div className="wl-name">{row.name || sym}</div>
              </div>
              <Sparkline values={row.spark} pct={row.change_pct} />
              <div className="wl-figures">
                <div className="wl-price">{money(row.price, row.currency)}</div>
                <Change pct={row.change_pct} />
              </div>
            </button>
          );
        })}

        {loaded && !symbols.length && !adding && (
          <div className="wl-empty">
            <p>Nothing on your watchlist yet.</p>
            <button className="btn tiny" onClick={() => setAdding(true)}>Add a symbol</button>
          </div>
        )}

        {/* DISMISSIBLE, because this notice outlives its own truth. It reports
            what the LAST fetch hit; once you have read it, it is a stale
            description of a moment that has passed, and with no way to clear it
            the panel keeps showing a failure that may no longer be happening.
            Retry replaces it with a fresh result; the × says "I have read
            this" without spending another upstream call to find out. */}
        {error && (
          <div className="wl-error">
            <button className="wl-error-close" aria-label="Dismiss" onClick={dismissError}>
              <X size={11} strokeWidth={2.2} />
            </button>
            <span>{error}</span>
            <button className="btn tiny" onClick={() => refresh({ force: true })}>Retry</button>
          </div>
        )}
      </div>

      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y}
          items={menuItems(menu.sym)}
          onClose={() => setMenu(null)}
        />
      )}

      {/* The delay is stated permanently, not on hover. It is the single most
          important fact about every number above it. */}
      <div className="wl-foot">
        Click for the full page, right-click for more. Drag to reorder. Delayed ~15 min{stamp ? ` · updated ${stamp}` : ""}.
        Arrows carry direction, not just colour.
      </div>
    </div>
  );
}
