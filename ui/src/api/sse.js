// SSE over fetch — an async generator of {event, data} objects.
//
// WHY not the browser's EventSource: it can't send POST bodies (our chat
// request is a POST with JSON) and can't set the Authorization header. Both
// are non-negotiable here, so we parse the text/event-stream framing
// ourselves — it's ~40 lines: lines starting with "event:"/"data:", blank
// line dispatches, ":" lines are heartbeats to ignore.
import { apiUrl, authHeaders, ApiError } from "./client";

export async function* streamSSE(path, body, { signal } = {}) {
  let res;
  try {
    res = await fetch(apiUrl(path), {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") return;
    throw new ApiError("backend_unreachable", "Can't reach Arthur's backend.", 0);
  }
  if (!res.ok) {
    let payload = null;
    try { payload = await res.json(); } catch { /* ignore */ }
    const err = payload && payload.error;
    throw new ApiError(err ? err.code : `http_${res.status}`,
      err ? err.message : `Stream failed (${res.status})`, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";
  let dataLines = [];

  try {
    while (true) {
      let done, value;
      try {
        ({ done, value } = await reader.read());
      } catch (e) {
        // Aborting mid-body rejects the READER, not the fetch — a separate
        // path from the abort already handled above, and the one Stop actually
        // takes once tokens are flowing. Chromium words it "BodyStreamBuffer
        // was aborted", which is how that string ended up rendered to users as
        // an error. A cancelled stream simply ends; treat it like `done`.
        if (signal?.aborted || e.name === "AbortError") return;
        throw e;
      }
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).replace(/\r$/, "");
        buffer = buffer.slice(nl + 1);

        if (line === "") {
          if (dataLines.length) {
            let data;
            try { data = JSON.parse(dataLines.join("\n")); }
            catch { data = { raw: dataLines.join("\n") }; }
            yield { event: eventName, data };
          }
          eventName = "message";
          dataLines = [];
        } else if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
        // lines starting with ":" are keep-alive comments — skip
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}
