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

export async function initApi() {
  if (ready) return;
  if (window.arthur) {
    // Electron may still be booting the backend; poll until it hands us a port
    for (let i = 0; i < 150; i++) {
      const info = await window.arthur.getBackendInfo();
      if (info && info.port) {
        base = `http://127.0.0.1:${info.port}`;
        token = info.token;
        if (info.state === "ready") { ready = true; return; }
      }
      await new Promise((r) => setTimeout(r, 400));
    }
    throw new ApiError("backend_unreachable", "The Arthur backend did not start.", 0);
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
