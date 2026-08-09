// Mode rail: the far-left icon column from the new design. Mode used to live
// as a row of chips inside the composer (Composer.jsx) -- the mockup moves
// mode selection up here instead, since it's really app-level state (it
// changes what the sidebar footer, chat header badge, and composer all show),
// not something that should reset when you switch conversations.
//
// THE RAIL IS A LAUNCHER, NOT A SWITCH.
//
// Clicking a mode starts a NEW chat in it; it does not re-flag the chat you are
// currently reading. Mode is a property of the conversation now (migration 5),
// so re-flagging would mean a conversation could change what tools it has
// mid-thread -- and a Code chat holding staged edits could quietly become a
// General chat that cannot apply them.
//
// The highlighted icon therefore shows what the CURRENT chat is, which is also
// what you would get by clicking it. Those two readings agree, so one highlight
// serves both.
import React from "react";
import {
  MessageSquare, Search, Code2, Mail, LineChart, Monitor, PenTool, Settings, Box,
} from "lucide-react";
import { useBackend } from "../stores/backend";

// Arthur's mark: a peak with a crossbar, drawn inline rather than imported so
// the rail badge, the boot screen and the onboarding card all render the exact
// same path at whatever size they need. It's also what build/icon.png and
// build/tray.png are generated from, so those stay visually identical.
export function LogoMark({ size = 18 }) {
  return (
    <svg
      viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"
    >
      <path d="M5 20 12 5l7 15" />
      <path d="M8.6 14.6h6.8" />
    </svg>
  );
}

export const MODES = [
  { id: "general", label: "General", icon: MessageSquare },
  // Research deliberately does NOT list needsDocker. Docker only sandboxes the
  // page-FETCHING step; without it, search snippets and paper abstracts still
  // work, so the honest behaviour is to run degraded and say so (the banner in
  // ResearchView) rather than lock the whole mode out. Code and Finance keep
  // the requirement because their tools genuinely execute inside a container.
  { id: "research", label: "Research", icon: Search },
  // Code follows the same rule as Research, and for the same reason. It used
  // to list needsDocker, from when every Code tool executed in a container.
  // That stopped being true with the changeset layer: searching, reading,
  // editing, staging and the whole diff review touch no container at all --
  // only run_python does. Gating the mode on Docker meant a user without it
  // could not edit a single file, for no reason. run_python disables itself
  // and says why (RunPythonTool checks the sandbox itself); the rest works.
  { id: "code", label: "Code", icon: Code2 },
  { id: "email", label: "Email", needsEmail: true, icon: Mail },
  { id: "finance", label: "Finance", needsDocker: true, icon: LineChart },
  { id: "computer", label: "Computer", icon: Monitor },
  { id: "design", label: "Design", icon: PenTool },
];

export default function ModeRail({ mode, onStart, onOpenSettings, settingsActive, onOpenHub, hubActive }) {
  const { status } = useBackend();
  const dockerOff = status && !status.docker_up;
  const emailOff = status && !status.email_configured;

  const primary = MODES.slice(0, 1); // General always pinned above the divider
  const rest = MODES.slice(1);

  const renderMode = (m) => {
    const disabled = (m.needsDocker && dockerOff) || (m.needsEmail && emailOff);
    const reason = m.needsDocker && dockerOff ? "Needs Docker running"
      : m.needsEmail && emailOff ? "Set up email in Settings, Integrations tab"
      : `New ${m.label.toLowerCase()} chat`;
    const Icon = m.icon;
    return (
      <button
        key={m.id}
        className={`rail-btn ${mode === m.id ? "active" : ""}`}
        disabled={disabled}
        title={reason}
        onClick={() => onStart(m.id)}
      >
        <Icon size={20} strokeWidth={1.8} />
      </button>
    );
  };

  return (
    <div className="mode-rail">
      <div className="logo"><LogoMark size={18} /></div>
      <div className="rail-sep" />
      {primary.map(renderMode)}
      <div style={{ height: 46, flexShrink: 0 }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {rest.map(renderMode)}
      </div>
      <div className="rail-spacer" />
      <button
        className={`rail-btn ${hubActive ? "active" : ""}`}
        title="Model hub"
        onClick={onOpenHub}
      >
        <Box size={20} strokeWidth={1.8} />
      </button>
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
