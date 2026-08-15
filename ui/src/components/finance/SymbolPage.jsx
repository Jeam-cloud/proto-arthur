// A symbol's page, filling the main pane.
//
// NOT A MODAL, deliberately. The composer stays live below it and the
// watchlist stays beside it, so you can ask about what you are looking at
// without dismissing it first. A popup would cover the conversation — and the
// conversation is where the assistant lives, so a popup that blocks it fights
// the feature it exists to serve.
//
// THE PAGE HANDS OFF, IT DOES NOT HOST. Every AI action closes the page and
// sends the question into the transcript. Two places that can hold a
// conversation means two memories and the user guessing which one knows what;
// the transcript is the app's spine, and this is somewhere you visit.
import React from "react";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { sparkPath, useFinance } from "../../stores/finance";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";

const PERIODS = ["5d", "1mo", "3mo", "6mo", "1y", "5y"];
// Spelled out for the caption. "1mo" is a control label, not English.
const SPAN = { "5d": "5 days", "1mo": "month", "3mo": "3 months",
               "6mo": "6 months", "1y": "year", "5y": "5 years" };

function money(v, currency, digits = 2) {
  if (typeof v !== "number" || !isFinite(v)) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency", currency: currency || "USD",
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(v);
  } catch { return v.toFixed(digits); }
}

function cap(v) {
  if (typeof v !== "number" || !isFinite(v)) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toLocaleString()}`;
}

function stamp(sec) {
  return sec ? new Date(sec * 1000)
    .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : null;
}

/** Where the price sits inside a low–high band. Reads far faster than two
 *  numbers: the question is "near the top or the bottom", not "what are they". */
function RangeBar({ low, high, at, currency }) {
  const ok = [low, high, at].every((v) => typeof v === "number" && isFinite(v));
  const pct = ok && high > low ? ((at - low) / (high - low)) * 100 : null;
  return (
    <>
      <div className="sp-range">
        {pct !== null && <span className="sp-range-mark" style={{ left: `${Math.min(100, Math.max(0, pct))}%` }} />}
      </div>
      <div className="sp-range-ends">
        <span>{money(low, currency)}</span><span>{money(high, currency)}</span>
      </div>
    </>
  );
}

function Chart({ points, currency }) {
  const values = (points || []).map((p) => p.close);
  const { line, area } = sparkPath(values, 900, 260, 6);
  if (!line) return <div className="sp-chart empty">No price history for this period.</div>;
  const up = values[values.length - 1] >= values[0];
  return (
    <svg className={`sp-chart ${up ? "up" : "down"}`} viewBox="0 0 900 260"
         preserveAspectRatio="none" aria-hidden="true">
      <path className="sp-chart-area" d={area} />
      <path className="sp-chart-line" d={line} />
    </svg>
  );
}

export default function SymbolPage() {
  const {
    openSymbol, detail, detailLoading, detailError, news, period,
    close, setPeriod, loadDetail,
  } = useFinance();
  const send = useChat((s) => s.send);
  const activeId = useConversations((s) => s.activeId);

  if (!openSymbol) return null;
  const row = detail?.row || {};
  const profile = row.profile || {};
  const hist = row.history || [];
  const currency = row.currency;
  const up = (row.change_pct ?? 0) >= 0;

  // THE HANDOFF. Closes the page first so the transcript is already on screen
  // when the reply starts arriving — the answer must never land behind
  // something the user then has to dismiss to read.
  const ask = (question) => {
    close();
    if (activeId) send(activeId, question, { mode: "finance" });
  };

  const name = row.name || openSymbol;
  const windowPct = (() => {
    if (hist.length < 2) return null;
    const a = hist[0].close, b = hist[hist.length - 1].close;
    return a ? ((b - a) / a) * 100 : null;
  })();

  return (
    <div className="symbol-page">
      <div className="sp-scroll">
        <div className="sp-head">
          <button className="sp-back" onClick={close}>
            <ArrowLeft size={14} strokeWidth={2} /> Watchlist
          </button>
        </div>

        <div className="sp-title">
          <span className="sp-sym">{openSymbol}</span>
          <span className="sp-name">{name}</span>
        </div>

        <div className="sp-price-row">
          <span className="sp-price">{money(row.price, currency)}</span>
          <span className="sp-cur">{currency}</span>
          {typeof row.change === "number" && (
            <span className={`sp-change ${up ? "up" : "down"}`}>
              {up ? "+" : ""}{money(row.change, currency)} {up ? "▲" : "▼"}{" "}
              {up ? "+" : ""}{row.change_pct?.toFixed(2)}%
            </span>
          )}
          <span className="sp-stamp">
            Delayed ~15 min{detail?.fetchedAt ? ` · updated ${stamp(detail.fetchedAt)}` : ""}
          </span>
        </div>

        {detailError && (
          <div className="sp-error">
            <span>{detailError}</span>
            <button className="btn tiny" onClick={() => loadDetail(openSymbol, period)}>Retry</button>
          </div>
        )}

        <div className="sp-card">
          {detailLoading && !hist.length
            ? <div className="sp-chart skeleton" />
            : <Chart points={hist} currency={currency} />}

          <div className="sp-periods">
            {PERIODS.map((p) => (
              <button key={p} className={`sp-period${p === period ? " active" : ""}`}
                      onClick={() => setPeriod(p)}>{p}</button>
            ))}
            <span className="sp-daily">daily closes</span>
          </div>

          {/* THE WINDOW FIGURE, LABELLED IN FULL. The header % is today; this
              one is the selected period. They previously sat on one screen with
              nothing saying so, and the day that matters is the day someone
              reads "up" while being down. */}
          {windowPct !== null && (
            <div className="sp-caption">
              Over the past {SPAN[period]}: {windowPct >= 0 ? "up" : "down"}{" "}
              {Math.abs(windowPct).toFixed(1)}%. Daily closes, delayed about 15 minutes at source.
            </div>
          )}
        </div>

        <div className="sp-stats">
          <div className="sp-stat">
            <div className="sp-stat-label">Previous close</div>
            <div className="sp-stat-value">{money(row.previous_close, currency)}</div>
          </div>
          <div className="sp-stat">
            <div className="sp-stat-label">Market cap</div>
            <div className="sp-stat-value">{cap(row.market_cap)}</div>
          </div>
          <div className="sp-stat wide">
            <div className="sp-stat-label">Day range</div>
            <RangeBar low={row.day_low} high={row.day_high} at={row.price} currency={currency} />
          </div>
          <div className="sp-stat wide">
            <div className="sp-stat-label">52-week range</div>
            <RangeBar low={row.year_low} high={row.year_high} at={row.price} currency={currency} />
          </div>
        </div>

        {/* Profile arrives on the slow `.info` call, so the page above is
            already usable by the time this appears — it fills in rather than
            blocking. */}
        {(profile.sector || profile.pe || profile.dividend_yield) && (
          <div className="sp-stats secondary">
            {profile.sector && <div className="sp-stat"><div className="sp-stat-label">Sector</div><div className="sp-stat-value sm">{profile.sector}</div></div>}
            {profile.industry && <div className="sp-stat"><div className="sp-stat-label">Industry</div><div className="sp-stat-value sm">{profile.industry}</div></div>}
            {typeof profile.pe === "number" && <div className="sp-stat"><div className="sp-stat-label">P/E</div><div className="sp-stat-value">{profile.pe.toFixed(1)}</div></div>}
            {typeof profile.dividend_yield === "number" && <div className="sp-stat"><div className="sp-stat-label">Dividend yield</div><div className="sp-stat-value">{profile.dividend_yield.toFixed(2)}%</div></div>}
          </div>
        )}

        {profile.summary && (
          <details className="sp-about">
            <summary>About</summary>
            <p>{profile.summary}</p>
            {profile.website && (
              <a href={profile.website} target="_blank" rel="noreferrer" className="sp-link">
                {profile.website.replace(/^https?:\/\//, "")} <ExternalLink size={11} />
              </a>
            )}
          </details>
        )}

        <div className="sp-news">
          <div className="sp-news-head">Recent coverage</div>
          {news?.items?.length ? news.items.map((n, i) => (
            <a key={n.url} className="sp-news-item" href={n.url} target="_blank" rel="noreferrer">
              <span className="sp-news-n">{i + 1}</span>
              <span className="sp-news-body">
                <span className="sp-news-title">{n.title}</span>
                <span className="sp-news-meta">
                  {n.domain}{n.published ? ` · ${n.published}` : ""}
                </span>
              </span>
            </a>
          )) : (
            <div className="sp-news-empty">
              {news?.unconfigured
                ? "Add a Tavily key in Settings → Integrations to see recent coverage."
                : news
                  ? "No recent coverage found for this symbol."
                  : "Looking for recent coverage…"}
            </div>
          )}
        </div>

        {/* Each of these ENDS this page and continues in the conversation. */}
        <div className="sp-actions">
          <button className="btn" onClick={() => ask(`Why did ${openSymbol} move today?`)}>
            Explain today's move
          </button>
          <button className="btn" onClick={() => ask(`Compare ${openSymbol} with another symbol over 6 months.`)}>
            Compare with another symbol
          </button>
          <span className="sp-handoff-note">Answers appear in the conversation</span>
        </div>
      </div>
    </div>
  );
}
