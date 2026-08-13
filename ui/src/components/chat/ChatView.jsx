import React, { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDown, BrainCircuit, Check, Compass, FileText, Image as ImageIcon, ShieldCheck, Square } from "lucide-react";
import { useChat } from "../../stores/chat";
import { useConversations } from "../../stores/conversations";
import Composer from "./Composer";
import ModeBadge from "./ModeBadge";
import Markdown from "./Markdown";
import ActivityFeed from "./ActivityFeed";
import AskUser from "./AskUser";
import WorkspaceBar from "../code/WorkspaceBar";
import FileTree from "../code/FileTree";
import ChangesPanel from "../code/ChangesPanel";
import { useWorkspace } from "../../stores/workspace";
import { useChanges } from "../../stores/changes";
import { useAttachments } from "../../stores/attachments";
import { useSettings } from "../../stores/settings";
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
  const reviewGateOn = useSettings((s) => !!s.values?.code_review_before_apply);
  // Just the last segment: "Working in atlas" is the project, "Working in
  // C:\Users\rian\OneDrive\…\atlas" is a path nobody reads.
  const folderName = useWorkspace((s) => (s.root ? s.root.replace(/[\\/]+$/, "").split(/[\\/]/).pop() : ""));
  const stop = useChat((s) => s.stop);
  const dropHandlers = useFileDrop();
  const dragging = useAttachments((s) => s.dragging);
  const bottomRef = useRef(null);
  const listRef = useRef(null);
  // "Is the user reading the newest text?" — the question the autoscroll should
  // have been asking all along. Ref, not state: it is read inside a scroll
  // handler on every frame and must not cause a render when it flips.
  const pinnedRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

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

  // How far from the bottom still counts as "at the bottom". Generous on
  // purpose: a line of tokens can land between the scroll event and this
  // check, and a 1px definition would unpin the user for scrolling nowhere.
  const PIN_SLACK_PX = 80;

  const jumpToLatest = useCallback(() => {
    pinnedRef.current = true;
    setShowJump(false);
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  // Scrolling up is a decision, and it used to be overruled ~30 times a second.
  //
  // The old effect called scrollIntoView on every token, so reading back
  // through a long reply while it was still being written was impossible: the
  // view snapped to the bottom before you could finish a sentence. Now the
  // stream only follows the user if the user is already at the bottom, and
  // otherwise says there is more below and waits to be asked.
  const onListScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= PIN_SLACK_PX;
    pinnedRef.current = atBottom;
    if (atBottom) setShowJump(false);
  }, []);

  useEffect(() => {
    if (pinnedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    } else if (slice.streaming || slice.draft) {
      // Only advertise unread content while something is actually arriving.
      // Scrolling up through a FINISHED conversation is just reading, and a
      // "new messages" pill there would be pointing at nothing new.
      setShowJump(true);
    }
  }, [slice.messages.length, slice.draft?.content?.length, slice.streaming]);

  // A new conversation starts at the bottom by definition — otherwise the pin
  // state carries over from the chat you just left, and switching to a fresh
  // one can open it scrolled up with a pill offering to show you the only
  // message it has.
  useEffect(() => {
    pinnedRef.current = true;
    setShowJump(false);
  }, [activeId]);

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
        <div className="message-list" ref={listRef} onScroll={onListScroll}>
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
          {/* A stopped turn gets a plain grey note, not the red error box.
              Whatever Arthur had already written stays above it — it was
              really written, and deleting it would punish the user for
              stopping. */}
          {slice.stopped && (
            <div className="message-row assistant">
              <div className="stopped-note">
                <Square size={11} strokeWidth={2.4} />
                Stopped. Arthur was still writing when you cancelled.
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
      {/* OUTSIDE .message-list on purpose. Inside the scroller it would scroll
          away with the content it is advertising — visible only once you had
          already scrolled back to where you no longer needed it. */}
      {showJump && (
        <button className="jump-latest" onClick={jumpToLatest}>
          <ArrowDown size={13} strokeWidth={2.2} />
          Arthur is still writing — jump to latest
        </button>
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
            {reviewGateOn
              ? "Arthur edits this folder on its own, then stages every change here for you to review. Nothing is written until you approve it."
              // The old copy always described the review-first flow, even with
              // the setting off — telling a user "nothing is written until you
              // approve it" in the one mode where that's no longer true. This
              // reads the same setting the banner is actually promising.
              : "Arthur writes to this folder directly — review is off. Every change still lands as an undoable receipt."}
          </span>
        </div>
      )}

      {mode === "code" && <ChangesPanel conversationId={activeId} />}

      {/* Directly above the composer: the question is about what to type next,
          so it belongs where typing happens rather than up in the transcript. */}
      <AskUser conversationId={activeId} mode={mode} />

      <Composer conversationId={activeId} mode={mode} />
    </>
  );
}

function Message({ message }) {
  // The apply receipt: not a turn, a record. Styled as a note rather than a
  // bubble so it never reads as something Arthur said.
  if (message.role === "receipt") {
    // `reviewed` comes from the apply that produced this receipt, not from
    // whatever the review-gate setting happens to be NOW. A message written
    // last week under auto-apply has to keep saying so even if the setting
    // was flipped back on since — the receipt is a record of what happened,
    // not a live readout of current settings.
    const wasReviewed = !!message.reviewed;
    return (
      <div className="message-row receipt">
        <div className="receipt-card">
          <Check size={16} strokeWidth={2.2} />
          <div>
            <div className="receipt-title">{message.content}</div>
            <div className="receipt-note">
              {wasReviewed
                ? "You reviewed every line before this happened. This receipt stays in the conversation."
                : "Applied automatically — review was off. This receipt stays in the conversation."}
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
        {/* The stream ended without a `done` event. "stopped early" was too
            vague to act on — it reads like the model chose to stop, when it
            actually means the connection to the backend ended mid-reply
            (Stop pressed, window closed, dev server reloaded, backend crashed).
            Naming the cause is what tells you whether to retry or go look at a
            log. */}
        {message.partial && (
          <div style={{ fontSize: 11, color: "var(--tmut)", marginTop: 6 }}>
            Cut off — the connection to Arthur ended before this reply finished.
          </div>
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
