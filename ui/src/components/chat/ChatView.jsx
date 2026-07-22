import React, { useEffect, useRef } from "react";
import { BrainCircuit } from "lucide-react";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";
import Composer from "./Composer";
import Markdown from "./Markdown";
import ActivityFeed from "./ActivityFeed";

// Model selection lives in the composer bar (ModelMenu inside Composer) —
// mode-aware recommendations, Claude-bar style. The header stays clean.

export default function ChatView() {
  const { activeId, list, createNew } = useConversations();
  const conv = list.find((c) => c.id === activeId);
  const slice = useChat((s) => s.slice(activeId || ""));
  const loadMessages = useChat((s) => s.loadMessages);
  const bottomRef = useRef(null);

  useEffect(() => {
    if (activeId) loadMessages(activeId).catch(() => {});
  }, [activeId, loadMessages]);

  // pin to bottom while streaming
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [slice.messages.length, slice.draft?.content?.length]);

  if (!activeId) {
    return (
      <div className="empty-state">
        <h3>Your assistant, entirely on your machine</h3>
        <p>No cloud account, no subscription, no data leaving your computer by default.</p>
        <button className="btn primary" onClick={createNew}>Start a conversation</button>
      </div>
    );
  }

  const showSuggestions = slice.loaded && slice.messages.length === 0 && !slice.draft;

  return (
    <>
      <div className="chat-header">
        <h2>{conv ? conv.title : "Chat"}</h2>
      </div>

      {showSuggestions ? (
        <EmptyChat conversationId={activeId} />
      ) : (
        <div className="message-list">
          {slice.memoryUsed.length > 0 && (
            <div className="memory-chip">
              <BrainCircuit size={12} />
              using {slice.memoryUsed.length} {slice.memoryUsed.length === 1 ? "memory" : "memories"}
            </div>
          )}
          {slice.messages.map((m) => <Message key={m.id} message={m} />)}
          {slice.draft && (
            <div className="message-row assistant">
              <div className="bubble">
                {slice.draft.provider !== "local" && <span className="provider-badge">cloud · {slice.draft.provider}</span>}
                <ActivityFeed items={slice.activity} />
                {slice.draft.content
                  ? <><Markdown>{slice.draft.content}</Markdown><span className="cursor-blink" /></>
                  : !slice.activity.length && <span className="cursor-blink" />}
              </div>
            </div>
          )}
          {slice.error && (
            <div className="message-row assistant">
              <div className="bubble" style={{ borderColor: "rgba(248,113,113,0.4)" }}>
                <strong style={{ color: "var(--red)" }}>
                  {slice.error.code === "security_blocked" ? "Blocked by the security gateway" : "Something went wrong"}
                </strong>
                <p style={{ marginTop: 4, color: "var(--mid)", fontSize: 12.5 }}>{slice.error.message}</p>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      )}

      <Composer conversationId={activeId} />
    </>
  );
}

function Message({ message }) {
  return (
    <div className={`message-row ${message.role}`}>
      <div className="bubble">
        {message.provider && message.provider !== "local" && (
          <span className="provider-badge">cloud · {message.provider}</span>
        )}
        {message.activity && <ActivityFeed items={message.activity} />}
        {message.role === "assistant"
          ? <Markdown>{message.content}</Markdown>
          : message.content}
        {message.partial && (
          <div style={{ fontSize: 11, color: "var(--dim)", marginTop: 6 }}>— stopped early</div>
        )}
      </div>
    </div>
  );
}

function EmptyChat({ conversationId }) {
  const send = useChat((s) => s.send);
  const suggestions = [
    "What can you do?",
    "Remember that I prefer short, direct answers",
    "Summarize what's on my screen",
  ];
  return (
    <div className="empty-state">
      <h3>What can I help with?</h3>
      <p>I remember useful facts across conversations, and I only get tools for the mode you pick below.</p>
      <div className="suggestions">
        {suggestions.map((s) => (
          <button key={s} className="suggestion" onClick={() => send(conversationId, s, { mode: "general" })}>
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
