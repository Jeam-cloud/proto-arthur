// Human-confirmation dialog (tracker p2t22), designed for non-technical users.
//
// The security rule is unchanged: the dialog shows EXACTLY what will happen,
// from the validated arguments — never the model's paraphrase. What changed is
// the presentation: each action type gets a natural layout (an email looks
// like an email, code looks like code, a click says where) instead of a JSON
// dump. Same information, no syntax. Unknown/future tools fall back to
// readable label→value rows, so nothing ever renders as raw JSON.
import React, { useEffect, useState } from "react";
import {
  AppWindow, CalendarPlus, FileEdit, Keyboard, Mail, MousePointerClick,
  ShieldAlert, TerminalSquare, Type,
} from "lucide-react";
import { useApprovals } from "../stores/approvals";

const TIMEOUT_S = 120;

// tool -> friendly framing. `allow` is the action verb on the button: people
// confirm "Send it", not an abstract "Allow once".
const TOOL_META = {
  email_send: { icon: Mail, title: "Send this email?", allow: "Send it" },
  calendar_create: { icon: CalendarPlus, title: "Add this to your calendar?", allow: "Add it" },
  write_file: { icon: FileEdit, title: "Save this file?", allow: "Save it" },
  run_python: { icon: TerminalSquare, title: "Run this code? (isolated sandbox, no internet)", allow: "Run it" },
  open_app: { icon: AppWindow, title: "Open this app?", allow: "Open it" },
  mouse_click: { icon: MousePointerClick, title: "Click your mouse here?", allow: "Click" },
  type_text: { icon: Type, title: "Type this for you?", allow: "Type it" },
  press_keys: { icon: Keyboard, title: "Press this shortcut?", allow: "Press it" },
};

const FIELD_LABELS = {
  to: "To", subject: "Subject", body: "Message", path: "File", content: "Contents",
  code: "Code", name: "App", text: "Text", keys: "Keys", x: "Position X", y: "Position Y",
  button: "Button", start_iso: "Starts", end_iso: "Ends", location: "Where",
  timezone_name: "Time zone", query: "Search for", count: "How many",
};

function label(key) {
  return FIELD_LABELS[key] || key.replaceAll("_", " ");
}

function ArgsView({ tool, args }) {
  if (tool === "email_send") {
    const hasList = (v) => v && String(v) !== "[]";
    return (
      <div className="approval-doc">
        <div className="approval-field"><span>To</span><strong>{args.to}</strong></div>
        {hasList(args.cc) && <div className="approval-field"><span>Cc</span><strong>{args.cc}</strong></div>}
        {hasList(args.bcc) && <div className="approval-field"><span>Bcc</span><strong>{args.bcc}</strong></div>}
        {hasList(args.attachments) && <div className="approval-field"><span>Attach</span><strong>{args.attachments}</strong></div>}
        <div className="approval-field"><span>Subject</span><strong>{args.subject}</strong></div>
        <div className="approval-message">{args.body}</div>
      </div>
    );
  }
  if (tool === "run_python" || tool === "write_file") {
    return (
      <div className="approval-doc">
        {args.path && <div className="approval-field"><span>File</span><strong>{args.path}</strong></div>}
        <pre className="approval-code">{args.code || args.content}</pre>
      </div>
    );
  }
  if (tool === "type_text") {
    return <div className="approval-doc"><div className="approval-message">“{args.text}”</div></div>;
  }
  // generic: clean label -> value rows, never JSON
  return (
    <div className="approval-doc">
      {Object.entries(args).map(([k, v]) => (
        <div key={k} className="approval-field">
          <span>{label(k)}</span><strong>{String(v)}</strong>
        </div>
      ))}
    </div>
  );
}

export default function ApprovalModal() {
  const { queue, decide } = useApprovals();
  const current = queue[0];
  const [remaining, setRemaining] = useState(TIMEOUT_S);

  useEffect(() => {
    if (!current) return;
    setRemaining(TIMEOUT_S);
    const timer = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) {
          clearInterval(timer);
          useApprovals.getState().dismiss(current.id); // backend already denied
          return 0;
        }
        return r - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [current && current.id]);

  if (!current) return null;

  const meta = TOOL_META[current.tool] || {
    icon: ShieldAlert, title: "Let Arthur do this?", allow: "Allow",
  };
  const Icon = meta.icon;

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h3><Icon size={17} color="var(--accent)" /> {meta.title}</h3>
        <div className="modal-sub">
          Nothing happens until you decide. This is exactly what Arthur will do:
        </div>
        <ArgsView tool={current.tool} args={current.args_preview || {}} />
        <div className="modal-actions">
          <span className="countdown">cancels itself in {remaining}s</span>
          <button className="btn danger" onClick={() => decide(current.id, false)}>No, don't</button>
          <button className="btn primary" onClick={() => decide(current.id, true)}>{meta.allow}</button>
        </div>
      </div>
    </div>
  );
}
