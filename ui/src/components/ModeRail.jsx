// Mode rail: the far-left icon column from the new design. Mode used to live
// as a row of chips inside the composer (Composer.jsx) -- the mockup moves
// mode selection up here instead, since it's really app-level state (it
// changes what the sidebar footer, chat header badge, and composer all show),
// not something that should reset when you switch conversations.
//
// WHY it stays a controlled prop from App.jsx rather than its own store: mode
// is read by three siblings (ModeRail, ChatView/Composer, Sidebar's status
// label) that all need to re-render together. Lifting a single small piece of
// state to their shared parent is simpler than adding a store for one value.
import React from "react";
import {
  MessageSquare, Search, Code2, Mail, LineChart, Monitor, PenTool, Settings,
} from "lucide-react";
import { useBackend } from "../stores/backend";

export const MODES = [
  { id: "general", label: "General", icon: MessageSquare },
  { id: "research", label: "Research", needsDocker: true, icon: Search },
  { id: "code", label: "Code", needsDocker: true, icon: Code2 },
  { id: "email", label: "Email", needsEmail: true, icon: Mail },
  { id: "finance", label: "Finance", needsDocker: true, icon: LineChart },
  { id: "computer", label: "Computer", icon: Monitor },
  { id: "design", label: "Design", icon: PenTool },
];

export default function ModeRail({ mode, setMode, onOpenSettings, settingsActive }) {
  const { status } = useBackend();
  const dockerOff = status && !status.docker_up;
  const emailOff = status && !status.email_configured;

  const primary = MODES.slice(0, 1); // General always pinned above the divider
  const rest = MODES.slice(1);

  const renderMode = (m) => {
    const disabled = (m.needsDocker && dockerOff) || (m.needsEmail && emailOff);
    const reason = m.needsDocker && dockerOff ? "Needs Docker running"
      : m.needsEmail && emailOff ? "Set up email in Settings, Integrations tab" : m.label;
    const Icon = m.icon;
    return (
      <button
        key={m.id}
        className={`rail-btn ${mode === m.id ? "active" : ""}`}
        disabled={disabled}
        title={reason}
        onClick={() => setMode(m.id)}
      >
        <Icon size={20} strokeWidth={1.8} />
      </button>
    );
  };

  return (
    <div className="mode-rail">
      <div className="logo">A</div>
      <div className="rail-sep" />
      {primary.map(renderMode)}
      <div style={{ height: 46, flexShrink: 0 }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {rest.map(renderMode)}
      </div>
      <div className="rail-spacer" />
      <button
        className={`rail-btn ${settingsActive ? "active" : ""}`}
        title="Settings"
        onClick={onOpenSettings}
      >
        <Settings size={20} strokeWidth={1.6} />
      </button>
    </div>
  );
}
