// The research layer: "is it any good, and what am I paying for it?"
//
// THE RULE THAT SHAPES EVERY ROW: a number on its own is not research. "P/E 32"
// is worthless; "P/E 32, sector context, and a way to ask what that means" is a
// finding. So the row unit is label · value · a way to ask — never a bare grid
// of forty ratios, which looks like research and teaches nothing.
//
// NO VERDICTS. Never cheap, expensive, strong, weak, good, bad. No scores, no
// traffic lights, no "Arthur's take". This is a product boundary, not a style
// preference: Arthur is not a licensed adviser and knows nothing about the
// user's situation. It shows the figure and offers to explain it.
//
// EVERYTHING HERE RIDES ON THE `.info` CALL THE PAGE ALREADY MAKES — no extra
// request. Sections are collapsed by default because most people want two of
// them, and an open accordion of six is a wall.
import React, { useState } from "react";
import { ChevronRight, HelpCircle } from "lucide-react";

// Percentages arrive already normalised from the container (see _pct there) —
// this only decides how to PRINT them.
const pct = (v) => (typeof v === "number" && isFinite(v) ? `${v.toFixed(2)}%` : null);
const num = (v, d = 2) => (typeof v === "number" && isFinite(v) ? v.toFixed(d) : null);
const big = (v) => {
  if (typeof v !== "number" || !isFinite(v)) return null;
  const a = Math.abs(v);
  if (a >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return v.toLocaleString();
};

// `period` is not decoration. A P/E built on the last reported quarter can be
// months old — that matters more than the 15-minute price delay and is easier
// to get wrong, so every figure carries where it came from.
const SECTIONS = [
  {
    id: "valuation", label: "Valuation", period: "TTM",
    rows: (r) => [
      ["P/E", num(r.valuation?.pe, 1)],
      ["Forward P/E", num(r.valuation?.forward_pe, 1)],
      ["Price / sales", num(r.valuation?.price_to_sales, 2)],
      ["Price / book", num(r.valuation?.price_to_book, 2)],
    ],
  },
  {
    id: "profitability", label: "Profitability", period: "TTM",
    rows: (r) => [
      ["Gross margin", pct(r.profitability?.gross_margin)],
      ["Operating margin", pct(r.profitability?.operating_margin)],
      ["Net margin", pct(r.profitability?.profit_margin)],
      ["Return on equity", pct(r.profitability?.roe)],
    ],
  },
  {
    id: "health", label: "Financial health", period: "most recent quarter",
    rows: (r) => [
      ["Debt / equity", num(r.health?.debt_to_equity, 1)],
      ["Current ratio", num(r.health?.current_ratio, 2)],
      ["Free cash flow", big(r.health?.free_cash_flow)],
      ["Total debt", big(r.health?.total_debt)],
      ["Cash", big(r.health?.total_cash)],
    ],
  },
  {
    id: "growth", label: "Growth", period: "year on year",
    rows: (r) => [
      ["Revenue growth", pct(r.growth?.revenue_growth)],
      ["Earnings growth", pct(r.growth?.earnings_growth)],
    ],
  },
  {
    id: "dividend", label: "Dividend", period: "TTM",
    rows: (r) => [
      ["Yield", pct(r.dividend?.yield)],
      ["Payout ratio", pct(r.dividend?.payout_ratio)],
    ],
    // Most tickers pay nothing, and a dividend section full of dashes is noise.
    hideWhen: (r) => !r.dividend?.yield && !r.dividend?.rate,
  },
  {
    id: "ownership", label: "Ownership", period: "latest filing",
    rows: (r) => [
      ["Held by institutions", pct(r.ownership?.institutions)],
      ["Held by insiders", pct(r.ownership?.insiders)],
      ["Beta", num(r.ownership?.beta, 2)],
      ["Shares outstanding", big(r.ownership?.shares_outstanding)],
    ],
  },
];

export default function ResearchSections({ symbol, research, onAsk }) {
  const [open, setOpen] = useState({ valuation: true });
  if (!research) return null;

  const visible = SECTIONS.filter((s) => !(s.hideWhen && s.hideWhen(research)));

  return (
    <div className="rs">
      <div className="rs-head">
        <h3>Research</h3>
        {/* The one thing no API returns, and the clearest reason this beats a
            table: moat, management and competitive position are judgement, and
            Arthur can at least discuss them. */}
        <button className="btn tiny" onClick={() => onAsk(
          `What's ${symbol}'s competitive position and how durable is it?`)}>
          Ask about its competitive position
        </button>
      </div>

      {visible.map((sec) => {
        const rows = sec.rows(research).filter(([, v]) => v !== null && v !== undefined);
        const isOpen = !!open[sec.id];
        return (
          <div className={`rs-section${isOpen ? " open" : ""}`} key={sec.id}>
            <button className="rs-toggle"
                    onClick={() => setOpen((o) => ({ ...o, [sec.id]: !o[sec.id] }))}>
              <ChevronRight size={13} className="rs-caret" />
              <span className="rs-label">{sec.label}</span>
              <span className="rs-period">{sec.period}</span>
            </button>

            {isOpen && (
              rows.length ? (
                <div className="rs-rows">
                  {rows.map(([label, value]) => (
                    <div className="rs-row" key={label}>
                      <span className="rs-row-label">{label}</span>
                      <span className="rs-row-value">{value}</span>
                      {/* The workhorse. A beginner can read this screen only
                          because every figure can be asked about in context. */}
                      <button
                        className="rs-ask" title={`What does ${label} of ${value} mean?`}
                        onClick={() => onAsk(
                          `For ${symbol}, ${label} is ${value}. What does that tell me, `
                          + "and what would I compare it against?")}
                      >
                        <HelpCircle size={12} />
                      </button>
                    </div>
                  ))}
                  <button className="rs-explain" onClick={() => onAsk(
                    `Explain ${symbol}'s ${sec.label.toLowerCase()} using these figures: `
                    + rows.map(([l, v]) => `${l} ${v}`).join(", ") + ".")}>
                    Explain this section
                  </button>
                </div>
              ) : (
                // Missing data is the NORMAL case — ETFs, ADRs, trusts and most
                // non-US listings return nulls for half of this. Said in one
                // line rather than rendered as six empty rows.
                <div className="rs-empty">Yahoo has no {sec.label.toLowerCase()} data for this listing.</div>
              )
            )}
          </div>
        );
      })}
    </div>
  );
}
