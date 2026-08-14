// Watchlist state. Live data only — there is no mock path in here.
//
// TWO SEPARATE REQUESTS ON PURPOSE. The symbol list is cheap and local; the
// quotes behind it are a container start against a rate-limited upstream. So
// the list loads instantly and renders (symbols, skeleton rows), and the data
// fills in when it arrives. Waiting for both would leave the panel blank for
// several seconds on every mount.
import { create } from "zustand";
import { api } from "../api/client";
import { useToasts } from "./toasts";

// The feed is ~15 min delayed and the backend caches for ~15 min on top, so a
// price can be half an hour old. Refetching faster than this buys nothing but
// upstream load — and yfinance rate-limits.
const MIN_REFETCH_MS = 60_000;

export const useFinance = create((set, get) => ({
  symbols: [],
  rows: {},          // { SYM: {name, price, change_pct, spark[], failed?} }
  fetchedAt: null,   // epoch seconds, from the server — what "updated 3:42pm" reads
  loading: false,    // a data fetch is in flight
  loaded: false,     // the symbol list has been read at least once
  error: null,       // upstream failed as a whole (breaker open, Docker off…)
  _lastFetch: 0,

  async load() {
    try {
      const { symbols } = await api.get("/finance/watchlist");
      set({ symbols, loaded: true });
      if (symbols.length) get().refresh();
    } catch (e) {
      set({ loaded: true, error: e.message });
    }
  },

  async refresh({ force = false } = {}) {
    if (get().loading) return;
    if (!force && Date.now() - get()._lastFetch < MIN_REFETCH_MS) return;
    set({ loading: true, error: null, _lastFetch: Date.now() });
    try {
      const res = await api.get("/finance/watchlist/data");
      set({
        rows: res.rows || {},
        fetchedAt: res.fetched_at || null,
        // `ok: false` is an upstream problem, not a transport one — the panel
        // shows a retry rather than an exception. See the route for why it is
        // a 200.
        error: res.ok ? null : (res.error || "Market data is unavailable right now."),
      });
    } catch (e) {
      set({ error: e.message });
    } finally {
      set({ loading: false });
    }
  },

  // Clears the notice without touching the data. The rows already on screen
  // stay: they were fetched successfully, and the failure being dismissed was
  // about the attempt after them.
  dismissError() { set({ error: null }); },

  async add(symbol) {
    const sym = String(symbol || "").trim().toUpperCase();
    if (!sym) return;
    const { symbols } = get();
    if (symbols.includes(sym)) return;
    const next = [...symbols, sym];
    set({ symbols: next });                        // optimistic: the row appears now
    try {
      await api.put("/finance/watchlist", { symbols: next });
      await get().refresh({ force: true });
    } catch (e) {
      set({ symbols });                            // roll back to what the server has
      useToasts.getState().push(`Couldn't add ${sym}: ${e.message}`, "error");
    }
  },

  async remove(symbol) {
    const { symbols, rows } = get();
    const index = symbols.indexOf(symbol);
    if (index === -1) return;
    const next = symbols.filter((s) => s !== symbol);
    const { [symbol]: gone, ...restRows } = rows;
    set({ symbols: next, rows: restRows });
    try {
      await api.put("/finance/watchlist", { symbols: next });
    } catch (e) {
      // Put it back where it was, not at the end — see conversations.remove()
      // for the same reasoning about undo restoring position.
      set({ symbols, rows });
      useToasts.getState().push(`Couldn't remove ${symbol}: ${e.message}`, "error");
    }
  },
}));

/**
 * Daily closes -> an SVG path pair (line, and the area beneath it).
 *
 * Hand-rolled rather than pulling in a charting library: a sparkline is a
 * polyline through n points and nothing else, and the smallest chart lib in
 * the running is ~45KB for a feature this file does in fifteen lines.
 *
 * The viewBox is fixed at 72x24 and the SVG is drawn with
 * preserveAspectRatio="none", so the path stretches to whatever width the row
 * gives it. That is why the stroke carries `vector-effect: non-scaling-stroke`
 * in CSS — without it the line thins horizontally as the panel widens.
 */
export function sparkPath(values, w = 72, h = 24, pad = 3) {
  const nums = (values || []).filter((v) => typeof v === "number" && isFinite(v));
  if (nums.length < 2) return { line: "", area: "" };

  const min = Math.min(...nums);
  const max = Math.max(...nums);
  // A flat series has zero range; dividing by it yields NaN and an empty path.
  // Drawing it down the middle is the honest picture of "this did not move".
  const span = max - min || 1;
  const stepX = (w - pad * 2) / (nums.length - 1);
  const pts = nums.map((v, i) => [
    pad + i * stepX,
    pad + (h - pad * 2) * (1 - (v - min) / span),
  ]);

  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  const area = `${line} L${pts[pts.length - 1][0].toFixed(2)} ${h} L${pts[0][0].toFixed(2)} ${h} Z`;
  return { line, area };
}
