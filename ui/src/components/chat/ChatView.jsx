import React, { useEffect, useRef } from "react";
import { BrainCircuit, Compass } from "lucide-react";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";
import Composer from "./Composer";
import ModeBadge from "./ModeBadge";
import Markdown from "./Markdown";
import ActivityFeed from "./ActivityFeed";

// Model selection lives in the composer bar (ModelMenu inside Composer),
// mode-aware recommendations, Claude-bar style. The header stays clean.

export default function ChatView({ mode, setMode }) {
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
        <div className="empty-state-icon"><Compass size={24} strokeWidth={2} /></div>
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
        <ModeBadge mode={mode} />
      </div>

      {showSuggestions ? (
        <EmptyChat conversationId={activeId} mode={mode} />
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
              <div className="bubble error">
                <strong style={{ color: "var(--red)", fontSize: "1.05em" }}>
                  {slice.error.code === "security_blocked" ? "Blocked by the security gateway" : "Something went wrong"}
                </strong>
                <p style={{ marginTop: 7, color: "var(--tmut)", fontSize: "0.92em", lineHeight: 1.55 }}>{slice.error.message}</p>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      )}

      <Composer conversationId={activeId} mode={mode} setMode={setMode} />
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
          <div style={{ fontSize: 11, color: "var(--tmut)", marginTop: 6 }}>stopped early</div>
        )}
      </div>
    </div>
  );
}

function EmptyChat({ conversationId, mode }) {
  const send = useChat((s) => s.send);
  const suggestions = [
    "What can you do?",
    "Remember that I prefer short, direct answers",
    "Summarize what's on my screen",
  ];
  return (
    <div className="empty-state">
      <div className="empty-state-icon"><Compass size={24} strokeWidth={2} /></div>
      <h3>What can I help with?</h3>
      <p>I remember useful facts across conversations, and I only get the tools for the mode you pick on the left.</p>
      <div className="suggestions">
        {suggestions.map((s) => (
          <button key={s} className="suggestion" onClick={() => send(conversationId, s, { mode })}>
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
