// API client: token-authenticated fetch against the local backend.
//
// init() runs once at boot: it asks the Electron preload for {port, token}.
// window.arthur is absent when the UI runs in a plain browser tab (dev
// convenience), so there's a fallback to a fixed port + dev token — handy for
// hacking on the UI with `uvicorn main:app` running manually.
let base = "";
let token = "";
let ready = false;

export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

/**
 * Wait for the backend, reporting progress.
 *
 * `onSlow` fires once if this is taking long enough to be worth saying so.
 *
 * THIS NO LONGER HAS ITS OWN DEADLINE. It used to poll 150 times at 400ms and
 * then throw "The Arthur backend did not start." — a 60s timeout that just
 * happened to equal the 60s timeout in electron/backend.js, two copies of one
 * decision with nothing tying them together, so either could be raised while
 * the other still gave up. Worse, it made a *guess* ("60s elapsed") outrank a
 * *fact* the main process already had ("the child process is alive and still
 * booting"). Now there is one authority: main reports `failed` when the child
 * actually dies, and this waits for `ready` or `failed` and nothing else.
 */
export async function initApi({ onSlow } = {}) {
  if (ready) return;
  if (window.arthur) {
    const startedAt = Date.now();
    let saidSlow = false;
    for (;;) {
      const info = await window.arthur.getBackendInfo();
      if (info && info.port) {
        base = `http://127.0.0.1:${info.port}`;
        token = info.token;
        if (info.state === "ready") { ready = true; return; }
        if (info.state === "failed") {
          throw new ApiError("backend_unreachable", "The Arthur backend did not start.", 0);
        }
      }
      if (!saidSlow && (info?.state === "slow" || Date.now() - startedAt > 8_000)) {
        saidSlow = true;
        onSlow?.();
      }
      await new Promise((r) => setTimeout(r, 400));
    }
  } else {
    base = "http://127.0.0.1:8756"; // plain-browser dev fallback
    token = "dev-token";
    ready = true;
  }
}

export function authHeaders(extra = {}) {
  return { Authorization: `Bearer ${token}`, ...extra };
}

export function apiUrl(path) {
  return `${base}${path}`;
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(apiUrl(path), {
      ...options,
      headers: authHeaders({
        ...(options.body && !(options.body instanceof FormData)
          ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      }),
    });
  } catch {
    throw new ApiError("backend_unreachable", "Can't reach Arthur's backend.", 0);
  }
  if (!res.ok) {
    let payload = null;
    try { payload = await res.json(); } catch { /* non-JSON error body */ }
    const err = payload && payload.error;
    throw new ApiError(
      err ? err.code : `http_${res.status}`,
      err ? err.message : `Request failed (${res.status})`,
      res.status,
    );
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: (path, body) => request(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: (path, body) => request(path, { method: "PUT", body: JSON.stringify(body) }),
  del: (path) => request(path, { method: "DELETE" }),
  postForm: (path, formData) => request(path, { method: "POST", body: formData }),
};
