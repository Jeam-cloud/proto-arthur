// Toast rail. Announced to assistive tech, dismissible by hand, and able to
// carry an action (the Undo on a deleted chat lives here).
//
// `role="status"` + `aria-live="polite"` on the CONTAINER, not on each toast:
// the region has to exist in the DOM before content is inserted into it, or
// screen readers miss the insertion entirely. A live region created at the
// same moment as its first message announces nothing.
import React from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useToasts } from "../../stores/toasts";

const ICON = {
  success: CheckCircle2,
  error: XCircle,
  warn: AlertTriangle,
  info: Info,
};

export default function Toasts() {
  const toasts = useToasts((s) => s.toasts);
  const { dismiss, pause, resume, runAction } = useToasts();

  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => {
        const Icon = ICON[t.kind] || Info;
        return (
          <div
            key={t.id}
            className={`toast ${t.kind}`}
            onMouseEnter={() => pause(t.id)}
            onMouseLeave={() => resume(t.id)}
          >
            <Icon size={15} strokeWidth={2} className="toast-icon" />
            <span className="toast-text">{t.message}</span>
            {t.action && (
              <button className="toast-action" onClick={() => runAction(t.id)}>
                {t.action.label}
              </button>
            )}
            <button
              className="toast-close"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
            >
              <X size={12} strokeWidth={2.2} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
