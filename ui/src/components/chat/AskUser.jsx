// The model asked a question, and this is where it can actually be answered.
//
// WHY IT IS BUTTONS AND NOT PROSE. A question written into the reply looks
// identical to an answer that trails off, and the user has to retype the
// distinction. Rendered as choices, answering costs one click, and the click
// sends ordinary user text — so nothing downstream needs to know a question
// was ever asked.
//
// It sits where the next message would go, not in the transcript, because it is
// a live control: once answered it is replaced by the user's own message.
import React, { useState } from "react";
import { useChat } from "../../stores/chat";

export default function AskUser({ conversationId, mode }) {
  const ask = useChat((s) => s.slice(conversationId || "").ask);
  const send = useChat((s) => s.send);
  const [picked, setPicked] = useState([]);

  if (!ask) return null;
  const { question, options = [], multi } = ask;

  const answer = (labels) => {
    if (!labels.length) return;
    setPicked([]);
    send(conversationId, labels.join(", "), { mode });
  };

  const toggle = (label) =>
    setPicked((p) => (p.includes(label) ? p.filter((l) => l !== label) : [...p, label]));

  return (
    <div className="ask-user">
      <div className="ask-question">{question}</div>
      <div className="ask-options">
        {options.map((o) => {
          const on = picked.includes(o.label);
          return (
            <button
              key={o.label}
              className={`ask-option${on ? " on" : ""}`}
              onClick={() => (multi ? toggle(o.label) : answer([o.label]))}
              title={o.description || undefined}
            >
              <span className="ask-label">{o.label}</span>
              {o.description && <span className="ask-desc">{o.description}</span>}
            </button>
          );
        })}
      </div>
      {/* Only multi-select needs a confirm step; a single choice sends on click,
          because a second click to confirm one answer is a second click for
          nothing. */}
      {multi && (
        <button className="btn primary" disabled={!picked.length} onClick={() => answer(picked)}>
          {picked.length ? `Send ${picked.length} selected` : "Pick at least one"}
        </button>
      )}
      {/* Never a dead end: the question is a suggestion, not a gate, and typing
          something else must always stay available. */}
      <div className="ask-note">or just type your own answer below</div>
    </div>
  );
}
