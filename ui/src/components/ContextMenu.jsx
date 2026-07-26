// Generic right-click menu: positioned at the cursor, closes on outside
// click/Escape/scroll. Used for both chat rows and folder rows in the
// sidebar so there's one place that handles viewport-edge clamping instead
// of duplicating that math per menu.
import React, { useEffect, useRef, useState } from "react";

export default function ContextMenu({ x, y, items, onClose }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ x, y });

  useEffect(() => {
    // Clamp so the menu never renders off the right/bottom edge of the window.
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const clampedX = Math.min(x, window.innerWidth - rect.width - 8);
    const clampedY = Math.min(y, window.innerHeight - rect.height - 8);
    setPos({ x: Math.max(8, clampedX), y: Math.max(8, clampedY) });
  }, [x, y]);

  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    document.addEventListener("scroll", onClose, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("scroll", onClose, true);
    };
  }, [onClose]);

  return (
    <div ref={ref} className="context-menu" style={{ left: pos.x, top: pos.y }}>
      {items.map((it, i) => (
        it.divider ? <div key={i} className="context-menu-divider" /> : (
          <button
            key={it.label}
            className={`context-menu-item ${it.danger ? "danger" : ""}`}
            onClick={() => { it.onClick(); onClose(); }}
          >
            <it.icon size={14} strokeWidth={1.8} />
            {it.label}
          </button>
        )
      ))}
    </div>
  );
}
