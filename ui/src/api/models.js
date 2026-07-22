// Model download with progress — shared by onboarding and the composer's
// model menu. Wraps the /models/pull SSE stream into a simple callback API.
import { apiUrl, authHeaders } from "./client";

export async function pullModel(model, onProgress) {
  const res = await fetch(apiUrl("/models/pull"), {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ model }),
  });
  if (!res.ok) throw new Error(`Download failed to start (${res.status})`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let error = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line.startsWith("data:")) continue;
      try {
        const d = JSON.parse(line.slice(5));
        if (d.code) error = d.message || "download failed";
        else if (d.total) onProgress(Math.round((d.completed / d.total) * 100), d.status);
      } catch { /* partial frame */ }
    }
  }
  if (error) throw new Error(error);
}
