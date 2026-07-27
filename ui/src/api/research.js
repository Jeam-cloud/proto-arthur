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

export function synthesizeInvestigation(body, { signal } = {}) {
  // Writes the paper from sources the client already has -- no searching.
  // Same SSE shape as runInvestigation, so stores/research.js applies events
  // from either stream through the identical applyEvent() handler.
  return streamSSE("/research/synthesize", body, { signal });
}

export function findMoreSources(body, { signal } = {}) {
  // Streams only sources that are NEW relative to what we already hold.
  return streamSSE("/research/find-sources", body, { signal });
}

// Export returns file BYTES, not SSE, so it bypasses the api helper (which
// assumes JSON) and reads the blob directly.
export async function exportPaper(body) {
  const { apiUrl, authHeaders, ApiError } = await import("./client");
  const res = await fetch(apiUrl("/research/export"), {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let payload = null;
    try { payload = await res.json(); } catch { /* binary route, may not be JSON */ }
    const err = payload && payload.error;
    throw new ApiError(err ? err.code : `http_${res.status}`,
      err ? err.message : "Export failed.", res.status);
  }
  // The filename the server chose (derived from the paper title) comes back
  // in Content-Disposition; reuse it so the download is not called "export".
  const disp = res.headers.get("content-disposition") || "";
  const match = /filename="([^"]+)"/.exec(disp);
  return { blob: await res.blob(), filename: match ? match[1] : `paper.${body.fmt}` };
}
