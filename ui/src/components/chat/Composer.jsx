// Composer: mode badge (privilege separation made visible), model picker,
// push-to-talk mic, send/stop.
//
// The mode selector is a SECURITY control disguised as a feature: the mode
// decides which tools the backend even offers the model. Mode selection now
// lives in ModeRail.jsx (app-level, since the chat header and sidebar also
// need to show it) -- this component just reads the current mode and shows
// it as a badge, same as the mockup's composer bar.
import React, { useEffect, useRef, useState } from "react";
import { Mic, Paperclip, Send, Square, Loader2 } from "lucide-react";
import { useBackend } from "../../stores/backend";
import { useChat } from "../../stores/chat";
import { useToasts } from "../../stores/toasts";
import { useWorkspace } from "../../stores/workspace";
import { useAttachments } from "../../stores/attachments";
import { useSettings } from "../../stores/settings";
import { useFileDrop } from "../../lib/useFileDrop";
import AttachmentTray from "./AttachmentTray";
import ModelMenu from "./ModelMenu";
import ModeBadge from "./ModeBadge";
import { useRecorder } from "./useRecorder";

// "email jane@x.com that..." typed (or spoken) in General mode should just
// work -- detect the intent and switch to Email mode for this send. The
// switch is VISIBLE (rail highlights + toast), keeping privilege separation
// explicit rather than silently granting tools.
const EMAIL_INTENT = /\b(e-?mail|send (an? )?e-?mail|reply to .*e-?mail|check my inbox|unread e-?mails?)\b/i;

export default function Composer({ conversationId, mode }) {
  const [text, setText] = useState("");
  const textareaRef = useRef(null);
  // Mirror of `text` for callbacks that outlive a render (the recorder's
  // onText fires long after the closure that created it -- reading state
  // directly there would see a stale value).
  const textRef = useRef("");
  textRef.current = text;
  const { status } = useBackend();
  const { send, stop } = useChat();
  const streaming = useChat((s) => s.slice(conversationId).streaming);
  const pushToast = useToasts((s) => s.push);

  const attachments = useAttachments((s) => s.items);
  const uploading = useAttachments((s) => s.uploading);
  const addFiles = useAttachments((s) => s.addFiles);
  const addFromPicker = useAttachments((s) => s.addFromPicker);
  const clearAttachments = useAttachments((s) => s.clear);
  const loadAttachments = useAttachments((s) => s.load);
  const loadCaps = useAttachments((s) => s.loadCaps);

  // Staged files belong to a conversation, so switching chats must reload the
  // tray rather than carrying the previous chat's attachments across.
  useEffect(() => { loadAttachments(conversationId); }, [conversationId, loadAttachments]);

  // Which model is really in play, resolved the same way the backend does:
  // per-conversation override first, then the mode assignment, then the global
  // default. Asking about the wrong model would warn about the wrong thing.
  const override = useChat((s) => s.modelOverride[conversationId] || "");
  const settingsValues = useSettings((s) => s.values);
  const effectiveModel = override
    || (settingsValues?.mode_models || {})[mode]
    || settingsValues?.default_model
    || "";
  useEffect(() => { loadCaps(effectiveModel); }, [effectiveModel, loadCaps]);

  // A file clicked in the Code mode tree lands in the draft here. It appends
  // rather than replaces, and focuses, because the path is the part that was
  // hard to remember -- the sentence around it is still the user's to write.
  const pendingInsert = useWorkspace((s) => s.pendingInsert);
  const clearInsert = useWorkspace((s) => s.clearInsert);
  useEffect(() => {
    if (!pendingInsert) return;
    setText((prev) => (prev && !prev.endsWith(" ") ? `${prev} ` : prev) + pendingInsert.text);
    clearInsert();
    textareaRef.current?.focus();
  }, [pendingInsert, clearInsert]);

  // dispatch: shared send path for typed AND spoken input -- mode auto-switch
  // and model resolution behave identically either way.
  const dispatch = (raw) => {
    const trimmed = raw.trim();
    // Attachments count as content. Dropping a screenshot and pressing send
    // with no words is a complete request -- "look at this" is implied -- but
    // the empty-text guard refused it and returned false, so the send button
    // appeared enabled and did nothing at all.
    if ((!trimmed && !useAttachments.getState().items.length) || streaming) return false;

    // The email-intent nudge used to silently switch this chat to Email mode
    // mid-conversation. That is exactly the behaviour we removed: a chat's mode
    // is fixed at creation, so changing it under the user would make the mode
    // badge, the tools and the transcript disagree with each other. Point at
    // the rail instead and let them decide.
    const sendMode = mode;
    if (mode === "general" && EMAIL_INTENT.test(trimmed)) {
      pushToast(
        status?.email_configured
          ? "Sending email needs an Email chat — start one from the left rail."
          : "Email isn't set up yet, add it in Settings, Integrations tab.",
        "info",
      );
    }

    send(conversationId, trimmed, {
      mode: sendMode,
      // "" lets the backend resolve: chip override > mode's model > default
      model: useChat.getState().modelOverride[conversationId] || "",
      provider: "local",
      // Read from the store, not the render closure: dispatch is also called
      // from the voice recorder's callback, which outlives the render that
      // created it and would capture a stale list.
      attachments: useAttachments.getState().items,
    });
    return true;
  };

  const recorder = useRecorder({
    // Voice is hands-free end-to-end: an empty composer means the spoken words
    // ARE the message, send immediately. If the user was mid-typing (or a
    // reply is still streaming), append instead of hijacking their draft.
    onText: (t) => {
      if (!textRef.current.trim() && !streaming) {
        if (!dispatch(t)) setText(t); // dispatch refused (edge) -> keep the words
      } else {
        setText((prev) => (prev ? prev + " " : "") + t);
      }
    },
    onError: (msg) => pushToast(msg, "error"),
  });

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 180) + "px";
    }
  }, [text]);

  const submit = () => {
    // A message carrying only attachments is a real message -- "here is the
    // contract" with a PDF and no words is a complete request. So the send
    // gate is text OR attachments, not text alone.
    if (!text.trim() && !attachments.length) return;
    if (dispatch(text)) {
      setText("");
      // The backend has bound these to the message that was just sent, so the
      // composer tray must empty or the next message would appear to carry
      // them again.
      clearAttachments();
    }
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  // The drag STATE is shared -- the whole conversation is the drop target now
  // (see ChatView), and this is only where the highlight is drawn.
  const dragging = useAttachments((s) => s.dragging);
  const dropHandlers = useFileDrop();

  // A paste carrying files (a screenshot from the clipboard) attaches them; a
  // paste carrying text must fall through to the textarea untouched.
  const onPaste = (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  };

  // Still a drop target itself, so dropping ON the composer works even though
  // the whole conversation now accepts files too.
  return (
    <div className={`composer-wrap${dragging ? " dropping" : ""}`} {...dropHandlers}>
      <AttachmentTray />
      {dragging && (
        <div className="composer-dropzone">
          <Paperclip size={15} strokeWidth={1.9} />
          <span>Drop files or folders to attach them</span>
        </div>
      )}
      <div className="composer">
        <ModeBadge mode={mode} />
        <button
          className="icon-btn"
          title="Attach files"
          disabled={uploading}
          onClick={addFromPicker}
        >
          {uploading ? <Loader2 size={16} className="spin" /> : <Paperclip size={16} />}
        </button>
        <textarea
          ref={textareaRef}
          rows={1}
          placeholder={
            mode === "computer"
              ? "Describe what to do on your computer, every action will ask first..."
              : "Message Arthur..."
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
        />
        <ModelMenu conversationId={conversationId} mode={mode} />
        <button
          className={`icon-btn ${recorder.state === "recording" ? "recording" : ""}`}
          title={recorder.state === "denied" ? "Mic access denied, see system settings" : "Hold to talk"}
          onMouseDown={recorder.start}
          onMouseUp={recorder.stop}
          onMouseLeave={recorder.stop}
        >
          {recorder.state === "transcribing" ? <Loader2 size={16} className="spin" /> : <Mic size={16} />}
        </button>
        {streaming ? (
          <button className="icon-btn danger" title="Stop generating" onClick={() => stop(conversationId)}>
            <Square size={14} />
          </button>
        ) : (
          <button
            className="icon-btn primary"
            disabled={!text.trim() && !attachments.length}
            title="Send"
            onClick={submit}
          >
            <Send size={15} />
          </button>
        )}
      </div>

      <div className="composer-hint">
        {mode === "computer"
          ? <span className="warn">Computer mode: Arthur can see your screen and control mouse/keyboard, each action needs your OK. Slam the mouse into the top-left corner to abort instantly.</span>
          : "Enter to send, Shift+Enter for a new line, hold mic to talk"}
      </div>
    </div>
  );
}
