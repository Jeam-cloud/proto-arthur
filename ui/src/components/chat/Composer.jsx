// Composer: mode chips (privilege separation made visible), model picker,
// push-to-talk mic, send/stop.
//
// The mode selector is a SECURITY control disguised as a feature: the mode
// chosen here decides which tools the backend even offers the model. Making
// it explicit UI (instead of silent auto-routing) means the user always
// knows what Arthur is currently allowed to do — and modes needing Docker
// are visibly disabled when Docker is off, with the reason in the tooltip.
import React, { useEffect, useRef, useState } from "react";
import { Mic, Send, Square, Loader2 } from "lucide-react";
import { useBackend } from "../../stores/backend";
import { useChat } from "../../stores/chat";
import { useToasts } from "../../stores/toasts";
import ModelMenu from "./ModelMenu";
import { useRecorder } from "./useRecorder";

const MODES = [
  { id: "general", label: "General" },
  { id: "research", label: "Research", needsDocker: true },
  { id: "code", label: "Code", needsDocker: true },
  { id: "email", label: "Email", needsEmail: true },
  { id: "finance", label: "Finance", needsDocker: true },
  { id: "computer", label: "Computer" },
  { id: "design", label: "Design" },
];

// "email jane@x.com that..." typed (or spoken) in General mode should just
// work — detect the intent and switch the mode chip for this send. The switch
// is VISIBLE (chip highlights + toast), keeping privilege separation explicit
// rather than silently granting tools.
const EMAIL_INTENT = /\b(e-?mail|send (an? )?e-?mail|reply to .*e-?mail|check my inbox|unread e-?mails?)\b/i;

export default function Composer({ conversationId }) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState("general");
  const textareaRef = useRef(null);
  // Mirror of `text` for callbacks that outlive a render (the recorder's
  // onText fires long after the closure that created it — reading state
  // directly there would see a stale value).
  const textRef = useRef("");
  textRef.current = text;
  const { status } = useBackend();
  const { send, stop } = useChat();
  const streaming = useChat((s) => s.slice(conversationId).streaming);
  const pushToast = useToasts((s) => s.push);

  // dispatch: shared send path for typed AND spoken input — mode auto-switch
  // and model resolution behave identically either way.
  const dispatch = (raw) => {
    const trimmed = raw.trim();
    if (!trimmed || streaming) return false;

    let sendMode = mode;
    if (mode === "general" && EMAIL_INTENT.test(trimmed)) {
      if (status?.email_configured) {
        sendMode = "email";
        setMode("email");
        pushToast("Switched to Email mode for this request.", "info");
      } else {
        pushToast("Email isn't set up yet — add it in Settings → Integrations.", "error");
      }
    }

    send(conversationId, trimmed, {
      mode: sendMode,
      // "" lets the backend resolve: chip override > mode's model > default
      model: useChat.getState().modelOverride[conversationId] || "",
      provider: "local",
    });
    return true;
  };

  const recorder = useRecorder({
    // Voice is hands-free end-to-end: an empty composer means the spoken words
    // ARE the message — send immediately. If the user was mid-typing (or a
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

  const dockerOff = status && !status.docker_up;
  const emailOff = status && !status.email_configured;

  const submit = () => {
    if (dispatch(text)) setText("");
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer-wrap">
      <div className="mode-row">
        {MODES.map((m) => {
          const disabled = (m.needsDocker && dockerOff) || (m.needsEmail && emailOff);
          const reason = m.needsDocker && dockerOff ? "Needs Docker running"
            : m.needsEmail && emailOff ? "Set up email in Settings → Integrations" : "";
          return (
            <button
              key={m.id}
              className={`mode-chip ${mode === m.id ? "active" : ""}`}
              disabled={disabled}
              title={reason}
              onClick={() => setMode(m.id)}
            >
              {m.label}
            </button>
          );
        })}
      </div>

      <div className="composer">
        <textarea
          ref={textareaRef}
          rows={1}
          placeholder={
            mode === "computer"
              ? "Describe what to do on your computer — every action will ask first…"
              : "Message Arthur…"
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <ModelMenu conversationId={conversationId} mode={mode} />
        <button
          className={`icon-btn ${recorder.state === "recording" ? "recording" : ""}`}
          title={recorder.state === "denied" ? "Mic access denied — see system settings" : "Hold to talk"}
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
          <button className="icon-btn primary" disabled={!text.trim()} title="Send" onClick={submit}>
            <Send size={15} />
          </button>
        )}
      </div>

      <div className="composer-hint">
        {mode === "computer"
          ? <span className="warn">Computer mode: Arthur can see your screen and control mouse/keyboard — each action needs your OK. Slam the mouse into the top-left corner to abort instantly.</span>
          : "Enter to send · Shift+Enter for a new line · hold mic to talk"}
      </div>
    </div>
  );
}
