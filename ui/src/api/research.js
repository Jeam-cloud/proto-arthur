// Research API: one plain POST for planning, one SSE stream for the run.
//
// WHY the split: the plan is something the user sits and edits, sometimes for a
// minute, before approving it. Holding an SSE connection open across that would
// mean a research run "starts" the moment you type a question, which is exactly
// the impression the plan screen exists to avoid.
import { api } from "./client";
import { streamSSE } from "./sse";

export function planInvestigation({ question, depth, model = "" }) {
  return api.post("/research/plan", { question, depth, model });
}

export function runInvestigation(body, { signal } = {}) {
  // Returns an async generator of {event, data}; the caller (stores/research.js)
  // owns the AbortController so the Stop button can cut it mid-flight.
  return streamSSE("/research/run", body, { signal });
}
