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

// Splits "a@x.com, b@x.com" -> ["a@x.com", "b@x.com"], dropping empties from
// trailing commas or stray whitespace. This is the inverse of how the list
// gets joined for display below.
function splitEmails(s) {
  return String(s || "").split(",").map((x) => x.trim()).filter(Boolean);
}

// Draft email: To/Subject/Body (and Cc/Bcc, only if the original call used
// them) are plain inputs, not a read-only preview -- the person approving is
// often the best-placed editor (a typo'd address, a line the model got
// slightly wrong) and re-typing the whole request to fix it would be worse
// than just fixing it here. The Send button still says exactly what will go
// out; nothing here bypasses the "you see exactly what happens" rule, it just
// lets you change what that is first.
function EmailDraft({ args, draft, setField }) {
  const hadCc = args.cc && String(args.cc) !== "[]" && args.cc.length > 0;
  const hadBcc = args.bcc && String(args.bcc) !== "[]" && args.bcc.length > 0;
  const hasAttachments = args.attachments && args.attachments.length > 0;
  return (
    <div className="approval-doc approval-doc-edit">
      <label className="approval-edit-field">
        <span>To</span>
        <input
          type="text"
          value={draft.to}
          onChange={(e) => setField("to", e.target.value)}
          placeholder="name@example.com"
        />
      </label>
      {hadCc && (
        <label className="approval-edit-field">
          <span>Cc</span>
          <input type="text" value={draft.cc} onChange={(e) => setField("cc", e.target.value)} />
        </label>
      )}
      {hadBcc && (
        <label className="approval-edit-field">
          <span>Bcc</span>
          <input type="text" value={draft.bcc} onChange={(e) => setField("bcc", e.target.value)} />
        </label>
      )}
      {hasAttachments && (
        // Attachments are file paths chosen earlier in the conversation, not
        // something to retype by hand here -- shown for visibility only.
        <div className="approval-field"><span>Attach</span><strong>{args.attachments.join(", ")}</strong></div>
      )}
      <label className="approval-edit-field">
        <span>Subject</span>
        <input type="text" value={draft.subject} onChange={(e) => setField("subject", e.target.value)} />
      </label>
      <label className="approval-edit-field stack">
        <span>Message</span>
        <textarea rows={7} value={draft.body} onChange={(e) => setField("body", e.target.value)} />
      </label>
    </div>
  );
}

function ArgsView({ tool, args }) {
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

const EMPTY_DRAFT = { to: "", cc: "", bcc: "", subject: "", body: "" };

export default function ApprovalModal() {
  const { queue, decide } = useApprovals();
  const current = queue[0];
  const [remaining, setRemaining] = useState(TIMEOUT_S);
  const [draft, setDraft] = useState(EMPTY_DRAFT);

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

  // Seed the editable draft from the REAL args (current.args), not the
  // display-only args_preview -- to/cc/bcc arrive here as actual arrays and
  // get joined for the input; splitEmails() undoes that on submit.
  useEffect(() => {
    if (!current || current.tool !== "email_send") return;
    const a = current.args || {};
    setDraft({
      to: (a.to || []).join(", "),
      cc: (a.cc || []).join(", "),
      bcc: (a.bcc || []).join(", "),
      subject: a.subject || "",
      body: a.body || "",
    });
  }, [current && current.id]);

  if (!current) return null;

  const meta = TOOL_META[current.tool] || {
    icon: ShieldAlert, title: "Let Arthur do this?", allow: "Allow",
  };
  const Icon = meta.icon;
  const isEmail = current.tool === "email_send";

  const onAllow = () => {
    if (!isEmail) return decide(current.id, true);
    // Reconstruct the args shape the backend expects (to/cc/bcc as arrays).
    // The server re-validates this through the same Pydantic model the
    // model's own call went through -- see agent/loop.py -- so a typo'd
    // address here fails the same clean way it would from the model.
    const a = current.args || {};
    decide(current.id, true, {
      ...a,
      to: splitEmails(draft.to),
      cc: splitEmails(draft.cc),
      bcc: splitEmails(draft.bcc),
      subject: draft.subject,
      body: draft.body,
    });
  };

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h3><Icon size={17} color="var(--accent)" /> {meta.title}</h3>
        <div className="modal-sub">
          {isEmail
            ? "Nothing sends until you decide. Edit anything below, then send it."
            : "Nothing happens until you decide. This is exactly what Arthur will do:"}
        </div>
        {isEmail
          ? <EmailDraft args={current.args || {}} draft={draft} setField={(k, v) => setDraft((d) => ({ ...d, [k]: v }))} />
          : <ArgsView tool={current.tool} args={current.args_preview || {}} />}
        <div className="modal-actions">
          <span className="countdown">cancels itself in {remaining}s</span>
          <button className="btn danger" onClick={() => decide(current.id, false)}>No, don't</button>
          <button className="btn primary" disabled={isEmail && !splitEmails(draft.to).length} onClick={onAllow}>
            {meta.allow}
          </button>
        </div>
      </div>
    </div>
  );
}
