// Live tool activity inside the assistant bubble: "researching…", "read 4
// pages", flagged-content warnings. Transparency is the product here: the
// user should always see what Arthur is doing on their machine.
import React from "react";
import { Loader2, CheckCircle2, XCircle, ShieldAlert } from "lucide-react";

export default function ActivityFeed({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="activity">
      {items.map((a) => (
        <div key={a.key} className={`activity-item ${a.flagged ? "flagged" : ""} ${a.ok === false ? "failed" : ""}`}>
          {a.running ? <Loader2 size={13} className="spin" />
            : a.flagged ? <ShieldAlert size={13} />
            : a.ok === false ? <XCircle size={13} />
            : <CheckCircle2 size={13} />}
          <span><strong>{a.name}</strong>{a.summary ? `: ${a.summary}` : ""}</span>
        </div>
      ))}
    </div>
  );
}
