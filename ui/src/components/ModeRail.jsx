// Mode rail: the far-left column. Icons WITH their names.
//
// THE RAIL IS A LAUNCHER, NOT A SWITCH.
//
// Clicking a mode starts a NEW chat in it; it does not re-flag the chat you are
// currently reading. Mode is a property of the conversation now (migration 5),
// so re-flagging would mean a conversation could change what tools it has
// mid-thread -- and a Code chat holding staged edits could quietly become a
// General chat that cannot apply them.
//
// The highlighted item therefore shows what the CURRENT chat is, which is also
// what you would get by clicking it. Those two readings agree, so one highlight
// serves both.
//
// WHY THE NAMES ARE ALWAYS ON. Seven monochrome glyphs in a 62px column were
// doing labels' work: a magnifier reads as "search" rather than Research, a
// chart could be Finance or analytics, and the only explanation was a native
// `title` tooltip with a ~1s delay -- worst exactly where it mattered, on the
// modes that were disabled and needed to say why. 34px of width buys the names
// outright, and nothing has to be hovered, discovered, or waited for.
//
// TWO SIGNALS FOR ACTIVE, not one. The background alone was `--emph` over
// `--bg2` -- a 9% white wash, which is nearly invisible and made the whole rail
// read as inert. The 2px tick at the left edge is the part you actually see;
// the surface is the supporting half.
import React, { useCallback, useEffect, useRef, useState } from "react";
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
  // `soon` rather than quietly shipping it: Design has no tools of its own
  // (TaskMode.DESIGN appears nowhere in the backend), so choosing it produced
  // a General chat wearing a different label.
  { id: "design", label: "Design", icon: PenTool, soon: true },
];

// Long enough that sweeping the pointer down the rail does not fire seven
// tooltips on the way past.
const TIP_DELAY_MS = 300;

export default function ModeRail({
  mode, onStart, onOpenSettings, settingsActive, onOpenHub, hubActive,
}) {
  const { status } = useBackend();
  const dockerOff = status && !status.docker_up;
  const emailOff = status && !status.email_configured;
  // { label, sub, y } — y is the vertical centre of the item it belongs to.
  const [tip, setTip] = useState(null);
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const showTip = useCallback((label, sub, el) => {
    clearTimeout(timer.current);
    const rect = el.getBoundingClientRect();
    const y = rect.top + rect.height / 2;
    timer.current = setTimeout(() => setTip({ label, sub, y }), TIP_DELAY_MS);
  }, []);
  const hideTip = useCallback(() => {
    clearTimeout(timer.current);
    setTip(null);
  }, []);

  const stateOf = (m) => {
    if (m.soon) {
      // No dot: a dot means "you can fix this", and there is nothing the user
      // can do about a mode that has not been built yet.
      return { disabled: true, reason: "Coming soon, no tools for this mode yet", dot: false };
    }
    if (m.needsDocker && dockerOff) {
      return { disabled: true, reason: "Needs Docker running", dot: true };
    }
    if (m.needsEmail && emailOff) {
      return { disabled: true, reason: "Set up email in Settings, Integrations", dot: true };
    }
    return { disabled: false, reason: "", dot: false };
  };

  const renderMode = (m, index) => {
    const { disabled, reason, dot } = stateOf(m);
    const Icon = m.icon;
    return (
      <button
        key={m.id}
        className={`rail-btn ${mode === m.id ? "active" : ""}`}
        disabled={disabled}
        onMouseEnter={(e) => showTip(m.label, reason || `Ctrl+${index + 1}`, e.currentTarget)}
        onMouseLeave={hideTip}
        onFocus={(e) => showTip(m.label, reason || `Ctrl+${index + 1}`, e.currentTarget)}
        onBlur={hideTip}
        onClick={() => { onStart(m.id); hideTip(); }}
      >
        <span className="rail-tick" />
        <Icon size={19} strokeWidth={1.8} />
        <span className="rail-label">{m.label}</span>
        {/* Amber, and only when there is something to go and do about it. */}
        {dot && <span className="rail-dot" />}
      </button>
    );
  };

  return (
    <div className="mode-rail">
      <div className="logo"><LogoMark size={17} /></div>
      <div className="rail-sep" />

      <div className="rail-items">
        {MODES.map(renderMode)}
      </div>

      <div className="rail-foot">
        <button
          className={`rail-btn ${hubActive ? "active" : ""}`}
          onMouseEnter={(e) => showTip("Model hub", "Browse and install models", e.currentTarget)}
          onMouseLeave={hideTip}
          onClick={() => { onOpenHub(); hideTip(); }}
        >
          <span className="rail-tick" />
          <Box size={18} strokeWidth={1.8} />
          <span className="rail-label">Models</span>
        </button>
        <button
          className={`rail-btn ${settingsActive ? "active" : ""}`}
          onMouseEnter={(e) => showTip("Settings", "Ctrl+,", e.currentTarget)}
          onMouseLeave={hideTip}
          onClick={() => { onOpenSettings(); hideTip(); }}
        >
          <span className="rail-tick" />
          <Settings size={18} strokeWidth={1.6} />
          <span className="rail-label">Settings</span>
        </button>
      </div>

      {/* Replaces `title=`, which had a ~1s delay, could not be styled, and —
          the part that actually mattered — does not render at all on a disabled
          button in most browsers. That was precisely where the explanation was
          needed: a greyed-out Email icon with no way to find out why. */}
      {tip && (
        <div className="rail-tip" role="tooltip" style={{ top: tip.y }}>
          <div className="rail-tip-label">{tip.label}</div>
          {tip.sub && <div className="rail-tip-sub">{tip.sub}</div>}
        </div>
      )}
    </div>
  );
}
