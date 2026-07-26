// Entry for the Ctrl+Shift+A quick widget, a frameless always-on-top wide
// bar (Spotlight-style rectangle; window size set in electron/main.js).
// One question, typed or SPOKEN, one streamed answer, an escape hatch to
// the full app. Voice matches the main composer's contract: empty input +
// finished transcript = auto-send; existing text = append, don't hijack.
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Loader2, Maximize2, Mic, Send, X } from "lucide-react";
import { api, initApi } from "./api/client";
import { streamSSE } from "./api/sse";
import Markdown from "./components/chat/Markdown";
import { useRecorder } from "./components/chat/useRecorder";
import "./styles/global.css";
import "highlight.js/styles/github-dark.css";

function QuickWidget() {
  const [ready, setReady] = useState(false);
  const [text, setText] = useState("");
  const [answer, setAnswer] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  const convRef = useRef(null);
  const inputRef = useRef(null);
  const textRef = useRef("");
  textRef.current = text;

  useEffect(() => {
    initApi().then(() => setReady(true)).catch((e) => setError(e.message));
    const onKey = (e) => e.key === "Escape" && window.arthur?.hideQuickWidget();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const askWith = async (q) => {
    q = q.trim();
    if (!q || streaming) return;
    setText(""); setAnswer(""); setError(null); setStreaming(true);
    try {
      if (!convRef.current) {
        const conv = await api.post("/conversations");
        convRef.current = conv.id;
      }
      const stream = streamSSE("/chat/stream", {
        conversation_id: convRef.current, message: q,
        mode: "general", model: "", provider: "local", // "" -> backend resolves mode/default model
      });
      for await (const { event, data } of stream) {
        if (event === "token") setAnswer((a) => a + data.content);
        if (event === "error") setError(data.message);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setStreaming(false);
      inputRef.current?.focus();
    }
  };

  const recorder = useRecorder({
    onText: (t) => {
      if (!textRef.current.trim() && !streaming) askWith(t); // hands-free: speak -> send
      else setText((prev) => (prev ? prev + " " : "") + t);
    },
    onError: (msg) => setError(msg),
  });

  return (
    <div className="quick">
      <div className="quick-titlebar">
        <div className="logo" style={{ width: 18, height: 18, fontSize: 10 }}>A</div>
        <span style={{ flex: 1 }}>Arthur, quick ask</span>
        <button className="icon-btn" title="Open full app"
          onClick={() => { window.arthur?.openMainWindow(); window.arthur?.hideQuickWidget(); }}>
          <Maximize2 size={13} />
        </button>
        <button className="icon-btn" title="Hide (Esc)" onClick={() => window.arthur?.hideQuickWidget()}>
          <X size={14} />
        </button>
      </div>

      <div className="quick-body">
        {error && <div style={{ color: "var(--red)", fontSize: 12.5 }}>{error}</div>}
        {!error && !answer && !streaming && (
          <div style={{ color: "var(--dim)", fontSize: 12.5 }}>
            Type, or hold the mic and just talk. I'll answer as soon as you let go.
          </div>
        )}
        {answer && <Markdown>{answer}</Markdown>}
        {streaming && <span className="cursor-blink" />}
      </div>

      <div className="quick-footer">
        <div className="composer">
          <textarea
            ref={inputRef} rows={1} autoFocus placeholder={ready ? "Ask Arthur…" : "Connecting…"}
            disabled={!ready} value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askWith(text); } }}
          />
          <button
            className={`icon-btn ${recorder.state === "recording" ? "recording" : ""}`}
            title={recorder.state === "denied" ? "Mic access denied" : "Hold to talk, sends when you release"}
            disabled={!ready}
            onMouseDown={recorder.start}
            onMouseUp={recorder.stop}
            onMouseLeave={recorder.stop}
          >
            {recorder.state === "transcribing" ? <Loader2 size={15} className="spin" /> : <Mic size={15} />}
          </button>
          <button className="icon-btn primary" disabled={!text.trim() || streaming} onClick={() => askWith(text)}>
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<QuickWidget />);
