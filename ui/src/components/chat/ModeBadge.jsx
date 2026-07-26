// Small read-only pill showing the active mode -- used in both the composer
// bar and the chat header, so the icon set lives here once instead of twice.
import React from "react";
import { MODES } from "../ModeRail";

export default function ModeBadge({ mode }) {
  const m = MODES.find((x) => x.id === mode) || MODES[0];
  const Icon = m.icon;
  return (
    <span className="mode-badge">
      <Icon size={13} strokeWidth={1.7} />
      {m.label}
    </span>
  );
}
