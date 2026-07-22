// SSE parser unit tests — the framing logic is the one piece of the UI where
// a silent bug corrupts every chat, so it gets real tests.
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("./client", () => ({
  apiUrl: (p) => `http://test${p}`,
  authHeaders: (extra = {}) => extra,
  ApiError: class ApiError extends Error {
    constructor(code, message, status) { super(message); this.code = code; this.status = status; }
  },
}));

import { streamSSE } from "./sse";

function mockFetchStream(chunks, { status = 200 } = {}) {
  global.fetch = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => ({ error: { code: "some_error", message: "bad" } }),
    body: {
      getReader() {
        let i = 0;
        return {
          read: async () =>
            i < chunks.length
              ? { done: false, value: new TextEncoder().encode(chunks[i++]) }
              : { done: true, value: undefined },
          cancel: async () => {},
        };
      },
    },
  }));
}

async function collect(gen) {
  const out = [];
  for await (const ev of gen) out.push(ev);
  return out;
}

describe("streamSSE", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("parses events split cleanly", async () => {
    mockFetchStream(['event: token\ndata: {"content":"hi"}\n\n', 'event: done\ndata: {}\n\n']);
    const events = await collect(streamSSE("/chat/stream", {}));
    expect(events).toEqual([
      { event: "token", data: { content: "hi" } },
      { event: "done", data: {} },
    ]);
  });

  it("handles one event split across network chunks", async () => {
    mockFetchStream(["event: tok", 'en\ndata: {"con', 'tent":"x"}\n\n']);
    const events = await collect(streamSSE("/p", {}));
    expect(events).toEqual([{ event: "token", data: { content: "x" } }]);
  });

  it("ignores heartbeat comments", async () => {
    mockFetchStream([": ping\n\n", 'event: token\ndata: {"content":"a"}\n\n']);
    const events = await collect(streamSSE("/p", {}));
    expect(events).toHaveLength(1);
  });

  it("handles multiple events in one chunk", async () => {
    mockFetchStream(['event: token\ndata: {"content":"a"}\n\nevent: token\ndata: {"content":"b"}\n\n']);
    const events = await collect(streamSSE("/p", {}));
    expect(events.map((e) => e.data.content)).toEqual(["a", "b"]);
  });

  it("throws ApiError with backend code on HTTP failure", async () => {
    mockFetchStream([], { status: 400 });
    await expect(collect(streamSSE("/p", {}))).rejects.toMatchObject({ code: "some_error" });
  });
});
