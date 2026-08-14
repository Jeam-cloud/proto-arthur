// A chart in the transcript, drawn from the tool result that produced it.
//
// WHY THIS IS NOT MARKDOWN. Asked "how has NVDA done this year", a model can
// only describe a price series — and describing 252 numbers produces a
// paragraph nobody reads and nobody can check. Worse, asking a 7B model to
// transcribe them into a chart spec is asking it to invent most of them. So
// the picture is built from the same payload the model was handed, and the
// model's job shrinks to saying what it means.
//
// HAND-ROLLED SVG, like the watchlist sparkline. A line, an area, a baseline
// and some labels is about eighty lines here; the smallest charting library
// worth having is ~45KB and would arrive with its own opinions about colour.
import React, { useMemo, useState } from "react";
import { sparkPath } from "../../stores/finance";

const W = 640;
const H = 200;
const PAD = { top: 10, right: 8, bottom: 18, left: 8 };

// Multi-series comparison differentiates by DASH, not by hue. The palette is
// deliberately monochrome — introducing a colour per symbol would undo that,
// and colour alone is the one channel a red/green-confusable reader lacks.
const DASH = ["none", "5 3", "2 3", "8 3"];

function fmt(v, currency) {
  if (typeof v !== "number" || !isFinite(v)) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency", currency: currency || "USD",
      maximumFractionDigits: v >= 1000 ? 0 : 2,
    }).format(v);
  } catch {
    return v.toFixed(2);
  }
}

export default function ChartCard({ chart }) {
  const [hover, setHover] = useState(null);
  const series = chart?.series || [];

  // NORMALISED when there is more than one line. Two prices on one axis makes
  // the more expensive stock look like the better performer regardless of what
  // it did — the classic misleading finance chart. Comparing percentages from
  // a shared zero is the honest version, and it is why the axis labels change
  // with the mode.
  const normalise = series.length > 1;

  const { paths, lo, hi, count } = useMemo(() => {
    const prepared = series.map((s) => {
      const vals = (s.points || []).map((p) => p.v);
      if (!normalise) return vals;
      const base = vals.find((v) => typeof v === "number" && v !== 0);
      return base ? vals.map((v) => ((v - base) / base) * 100) : vals;
    });
    const flat = prepared.flat().filter((v) => typeof v === "number" && isFinite(v));
    return {
      paths: prepared,
      lo: flat.length ? Math.min(...flat) : 0,
      hi: flat.length ? Math.max(...flat) : 0,
      count: Math.max(...prepared.map((p) => p.length), 0),
    };
  }, [series, normalise]);

  if (!series.length || count < 2) return null;

  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const span = hi - lo || 1;
  const yOf = (v) => PAD.top + innerH * (1 - (v - lo) / span);
  const xOf = (i, n) => PAD.left + (innerW * i) / Math.max(n - 1, 1);

  const hoverIndex = hover === null ? null : Math.round((hover / innerW) * (count - 1));

  return (
    <div className="chart-card">
      <div className="chart-head">
        <div>
          <div className="chart-title">{chart.title}</div>
          <div className="chart-sub">{chart.subtitle}</div>
        </div>
        {/* On the chart itself, not only in the panel at the edge of the
            screen: this picture gets scrolled back to. */}
        {chart.note && <span className="chart-note">{chart.note}</span>}
      </div>

      <svg
        className="chart-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        role="img" aria-label={chart.summary}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const x = ((e.clientX - r.left) / r.width) * W - PAD.left;
          setHover(Math.max(0, Math.min(innerW, x)));
        }}
      >
        {/* Zero line, only when normalised — it is the thing every series is
            being compared against, so it has to be visible. */}
        {normalise && lo < 0 && hi > 0 && (
          <line className="chart-zero" x1={PAD.left} x2={W - PAD.right} y1={yOf(0)} y2={yOf(0)} />
        )}

        {paths.map((vals, i) => {
          const pts = vals.map((v, j) => [xOf(j, vals.length), yOf(v)]);
          const d = pts.map(([x, y], j) => `${j ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
          const up = vals[vals.length - 1] >= vals[0];
          return (
            <g key={series[i].label}>
              {/* The filled area is for a single series only. Overlapping
                  translucent fills on a comparison read as a third colour
                  that means nothing. */}
              {series.length === 1 && (
                <path
                  className={`chart-area ${up ? "up" : "down"}`}
                  d={`${d} L${pts[pts.length - 1][0].toFixed(1)} ${H - PAD.bottom} L${pts[0][0].toFixed(1)} ${H - PAD.bottom} Z`}
                />
              )}
              <path
                className={`chart-line ${series.length === 1 ? (up ? "up" : "down") : ""}`}
                d={d} strokeDasharray={series.length > 1 ? DASH[i % DASH.length] : "none"}
              />
            </g>
          );
        })}

        {hoverIndex !== null && hoverIndex >= 0 && hoverIndex < count && (
          <line
            className="chart-cursor"
            x1={xOf(hoverIndex, count)} x2={xOf(hoverIndex, count)}
            y1={PAD.top} y2={H - PAD.bottom}
          />
        )}
      </svg>

      <div className="chart-legend">
        {series.map((s, i) => {
          const p = s.points[hoverIndex ?? s.points.length - 1];
          return (
            <span className="chart-key" key={s.label}>
              <svg width="16" height="6" aria-hidden="true">
                <line x1="0" y1="3" x2="16" y2="3" className="chart-line"
                      strokeDasharray={series.length > 1 ? DASH[i % DASH.length] : "none"} />
              </svg>
              <strong>{s.label}</strong>
              <span className="chart-val">
                {fmt(p?.v, chart.currency)}
                {hoverIndex !== null && p?.t && <span className="chart-date"> · {p.t}</span>}
              </span>
            </span>
          );
        })}
      </div>

      {/* The same sentence the SVG carries as its aria-label. Shown, not just
          announced: it is the fastest way to read the chart, and the only way
          if the line is a shape you cannot interpret at a glance. */}
      <div className="chart-summary">{chart.summary}</div>
    </div>
  );
}
