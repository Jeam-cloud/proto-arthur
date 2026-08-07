import React, { useEffect, useRef } from "react";
import { BrainCircuit, Compass, FileText, Image as ImageIcon } from "lucide-react";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";
import Composer from "./Composer";
import ModeBadge from "./ModeBadge";
import Markdown from "./Markdown";
import ActivityFeed from "./ActivityFeed";
import WorkspaceBar from "../code/WorkspaceBar";
import FileTree from "../code/FileTree";
import { useWorkspace } from "../../stores/workspace";
import { useAttachments } from "../../stores/attachments";
import { useFileDrop } from "../../lib/useFileDrop";

// Model selection lives in the composer bar (ModelMenu inside Composer),
// mode-aware recommendations, Claude-bar style. The header stays clean.

export default function ChatView({ mode, setMode }) {
  const { activeId, list, createNew } = useConversations();
  const conv = list.find((c) => c.id === activeId);
  const slice = useChat((s) => s.slice(activeId || ""));
  const loadMessages = useChat((s) => s.loadMessages);
  const requestInsert = useWorkspace((s) => s.requestInsert);
  const dropHandlers = useFileDrop();
  const dragging = useAttachments((s) => s.dragging);
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

      {/* Code mode only. Every other mode either touches no files or resolves
          its own paths, so a folder bar would be chrome asking a question that
          mode never has to answer. */}
      {mode === "code" && <WorkspaceBar conversationId={activeId} />}

      {/* ALWAYS classed, never a bare div. `.message-list` is `flex: 1`, which
          only works while it is a flex child of the full-height chat column --
          wrapping it in an unstyled div collapsed that chain, so the list sized
          to its content and the composer floated up under the last message
          instead of sitting at the bottom of the window. */}
      {/* The CONVERSATION is a drop target, not just the composer.
          Dragging a file over the messages -- which is most of the window and
          the obvious place to aim -- used to show the "no drop" cursor and do
          nothing, because only .composer-wrap called preventDefault on
          dragover. The highlight still appears on the composer, since that is
          where the files actually land. */}
      <div
        className={`chat-body${mode === "code" ? " with-files" : ""}${dragging ? " dropping" : ""}`}
        {...dropHandlers}
      >
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
      {/* Clicking a file appends its path to the draft rather than sending
          anything: the path is the hard part to remember, the sentence around
          it is the user's. */}
      {mode === "code" && <FileTree onPick={(p) => requestInsert(`\`${p}\` `)} />}
      </div>

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
        {/* Attachments, on the message they were sent with.
            They were bound to the message in the database from the start but
            never read back, so scrolling up showed the question with no sign of
            the file it was about -- and a message carrying ONLY a screenshot
            rendered as an empty bubble. */}
        {message.attachments?.length > 0 && (
          <div className="msg-attachments">
            {message.attachments.map((a) => (
              <span key={a.id} className={`msg-attachment${a.error ? " bad" : ""}`} title={a.error || a.filename}>
                {a.kind === "image"
                  ? <ImageIcon size={12} strokeWidth={1.8} />
                  : <FileText size={12} strokeWidth={1.8} />}
                {a.filename}
              </span>
            ))}
          </div>
        )}
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
