import React, { useEffect, useRef } from "react";
import { BrainCircuit, Check, Compass, FileText, Image as ImageIcon, ShieldCheck } from "lucide-react";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";
import Composer from "./Composer";
import ModeBadge from "./ModeBadge";
import Markdown from "./Markdown";
import ActivityFeed from "./ActivityFeed";
import WorkspaceBar from "../code/WorkspaceBar";
import FileTree from "../code/FileTree";
import ChangesPanel from "../code/ChangesPanel";
import { useWorkspace } from "../../stores/workspace";
import { useChanges } from "../../stores/changes";
import { useAttachments } from "../../stores/attachments";
import { useFileDrop } from "../../lib/useFileDrop";

// Model selection lives in the composer bar (ModelMenu inside Composer),
// mode-aware recommendations, Claude-bar style. The header stays clean.

export default function ChatView({ mode }) {
  const { activeId, list, createNew } = useConversations();
  const conv = list.find((c) => c.id === activeId);
  const slice = useChat((s) => s.slice(activeId || ""));
  const loadMessages = useChat((s) => s.loadMessages);
  const requestInsert = useWorkspace((s) => s.requestInsert);
  const loadChanges = useChanges((s) => s.load);
  const pendingFiles = useChanges((s) => s.files);
  const hasFolder = useWorkspace((s) => !!s.root);
  // Just the last segment: "Working in atlas" is the project, "Working in
  // C:\Users\rian\OneDrive\…\atlas" is a path nobody reads.
  const folderName = useWorkspace((s) => (s.root ? s.root.replace(/[\\/]+$/, "").split(/[\\/]/).pop() : ""));
  const stop = useChat((s) => s.stop);
  const dropHandlers = useFileDrop();
  const dragging = useAttachments((s) => s.dragging);
  const bottomRef = useRef(null);

  useEffect(() => {
    if (activeId) loadMessages(activeId).catch(() => {});
  }, [activeId, loadMessages]);

  // Pending edits survive a mode switch and a chat switch (they live on the
  // backend for the life of the app), so the panel has to be re-hydrated on
  // arrival rather than only appearing when a stream happens to stage
  // something. Otherwise you leave Code mode with 4 files unreviewed, come
  // back, and the app looks like it forgot.
  useEffect(() => {
    if (mode === "code") loadChanges(activeId);
  }, [activeId, mode, loadChanges]);

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
                {/* The run block is for the LIVE turn in Code mode only.
                    Historical messages keep the plain list: once a turn is
                    over, "still working" framing and a Stop button are lies,
                    and the grouping that helps mid-run just hides detail you
                    may be scrolling back to find. */}
                <ActivityFeed
                  items={slice.activity}
                  variant={mode === "code" ? "run" : "list"}
                  startedAt={slice.startedAt}
                  folder={folderName}
                  onStop={() => stop(activeId)}
                />
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

      {/* Directly above the composer, so the last thing between "the agent
          finished" and "you type again" is the diff. Putting it in a sidebar
          would make reviewing an optional detour, and an optional review is
          how unreviewed code gets applied. */}
      {/* The guarantee, stated before it's needed rather than after. Without
          this a first-time user has no way to know Arthur won't just overwrite
          their project — the reassurance only ever arrived once edits were
          already staged, which is the wrong moment to learn it. */}
      {mode === "code" && hasFolder && !pendingFiles && (
        <div className="code-idle">
          <ShieldCheck size={15} strokeWidth={1.8} />
          <span>
            Arthur edits this folder on its own, then stages every change here for you to
            review. Nothing is written until you approve it.
          </span>
        </div>
      )}

      {mode === "code" && <ChangesPanel conversationId={activeId} />}

      <Composer conversationId={activeId} mode={mode} />
    </>
  );
}

function Message({ message }) {
  // The apply receipt: not a turn, a record. Styled as a note rather than a
  // bubble so it never reads as something Arthur said.
  if (message.role === "receipt") {
    return (
      <div className="message-row receipt">
        <div className="receipt-card">
          <Check size={16} strokeWidth={2.2} />
          <div>
            <div className="receipt-title">{message.content}</div>
            <div className="receipt-note">
              You reviewed every line before this happened. This receipt stays in the conversation.
            </div>
          </div>
        </div>
      </div>
    );
  }
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
