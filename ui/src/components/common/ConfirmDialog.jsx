// The app's one confirmation modal. Mounted once in App.jsx; opened from
// anywhere via useConfirm.ask(). See stores/confirm.js for why it is a store.
//
// The title is a QUESTION and the body states the consequence, because "Are
// you sure?" over "OK / Cancel" asks the user to remember what they clicked.
// The confirm button repeats the verb ("Clear the log", not "OK") so the
// dialog is still answerable if you read only the buttons.
import React, { useEffect, useRef } from "react";
import { useConfirm } from "../../stores/confirm";

export default function ConfirmDialog() {
  const pending = useConfirm((s) => s.pending);
  const cancel = useConfirm((s) => s.cancel);
  const run = useConfirm((s) => s.run);
  const confirmRef = useRef(null);

  // Escape cancels. Bound while open only, and NOT to the same handler as the
  // app-wide Escape in App.jsx — this one has to win, so it stops propagation.
  useEffect(() => {
    if (!pending) return;
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); cancel(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [pending, cancel]);

  // Focus lands on the confirm button so the dialog is operable from the
  // keyboard the moment it appears. Deliberate that it is the CONFIRM and not
  // Cancel: the user asked for this action, and Escape is always the way out.
  useEffect(() => {
    if (pending) confirmRef.current?.focus();
  }, [pending]);

  if (!pending) return null;
  const { title, body, confirmLabel, danger, onConfirm } = pending;
  // NO onConfirm MEANS THERE IS NOTHING TO CONFIRM. Some things raised here
  // only need explaining (why a figure looks wrong), and offering "Cancel"
  // against an explanation implies declining it would change something.
  const informational = !onConfirm;

  return (
    <div className="modal-backdrop" onClick={cancel}>
      <div
        className="modal narrow"
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h3>{title}</h3>
        <p className="modal-sub">{body}</p>
        <div className="modal-actions">
          {!informational && <button className="btn" onClick={cancel}>Cancel</button>}
          <button
            ref={confirmRef}
            className={`btn ${informational ? "primary" : danger ? "danger" : "primary"}`}
            onClick={run}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
