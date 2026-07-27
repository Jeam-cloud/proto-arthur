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
//
// WHY the fetch is wrapped: an unwrapped fetch that fails at the NETWORK layer
// throws a bare TypeError whose message is the browser's own useless string,
// "Failed to fetch". That is what every export failure used to surface as --
// one sentence that names no cause and suggests no fix, for at least four very
// different problems (backend not up yet, request never sent because the body
// could not be serialised, connection dropped mid-render, route missing).
// Classifying it here means the toast can say which one it was, and gives the
// caller a code it can branch on to fall back to the local renderer.
export async function exportPaper(body) {
  const { apiUrl, authHeaders, ApiError } = await import("./client");

  // Serialisation is its own failure mode and must not be blamed on the
  // network: a circular reference or a BigInt anywhere in the paper throws
  // here, BEFORE a single byte goes out.
  let payloadText;
  try {
    payloadText = JSON.stringify(body);
  } catch (e) {
    throw new ApiError("export_serialise", `The paper could not be packaged for export: ${e.message}`, 0);
  }

  let res;
  try {
    res = await fetch(apiUrl("/research/export"), {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: payloadText,
    });
  } catch (e) {
    // Reached only for network-layer failures. `export_unreachable` is the
    // signal the store uses to switch to the local renderer instead of just
    // reporting a dead end -- see stores/research.js exportAs().
    throw new ApiError(
      "export_unreachable",
      `Arthur's backend did not answer the export request (${e.message}). `
      + "It may still be starting up, or it stopped while rendering.",
      0,
    );
  }
  if (!res.ok) {
    let payload = null;
    try { payload = await res.json(); } catch { /* binary route, may not be JSON */ }
    const err = payload && payload.error;
    throw new ApiError(err ? err.code : `http_${res.status}`,
      err ? err.message : `Export failed (HTTP ${res.status}).`, res.status);
  }
  // The filename the server chose (derived from the paper title) comes back
  // in Content-Disposition; reuse it so the download is not called "export".
  const disp = res.headers.get("content-disposition") || "";
  const match = /filename="([^"]+)"/.exec(disp);
  return { blob: await res.blob(), filename: match ? match[1] : `paper.${body.fmt}` };
}
