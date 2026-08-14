// Mode rail: the far-left icon column, plus the panel that names what the
// icons mean.
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
//
// WHY THE FLYOUT EXISTS. Seven monochrome glyphs in a 62px column were doing
// labels' work: a magnifier reads as "search" rather than Research, a chart
// could be Finance or analytics, and the only explanation was a native `title`
// tooltip with a ~1s delay -- worst exactly where it mattered, on the modes
// that were disabled and needed to say why. The panel names every mode, says
// in one line what it can reach, and shows its shortcut.
//
// IT OVERLAYS RATHER THAN WIDENING THE RAIL. If the rail itself grew, the
// sidebar and the whole conversation would shift sideways every time the
// pointer crossed it. Nothing reflows here: the 62px column stays exactly
// where it is and the panel floats over the content beside it.
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

// `blurb` is what the mode can REACH, not what it is for. "Research" already
// says what it is for; what a user cannot guess from the name is that it will
// go to the network, or that Code can write to their disk. One line each,
// because the panel is 186px wide and a second line pushes the list past a
// glance.
export const MODES = [
  { id: "general", label: "General", icon: MessageSquare, blurb: "plain chat, no tools" },
  // Research deliberately does NOT list needsDocker. Docker only sandboxes the
  // page-FETCHING step; without it, search snippets and paper abstracts still
  // work, so the honest behaviour is to run degraded and say so (the banner in
  // ResearchView) rather than lock the whole mode out. Code and Finance keep
  // the requirement because their tools genuinely execute inside a container.
  { id: "research", label: "Research", icon: Search, blurb: "reads the web, cites sources" },
  // Code follows the same rule as Research, and for the same reason. It used
  // to list needsDocker, from when every Code tool executed in a container.
  // That stopped being true with the changeset layer: searching, reading,
  // editing, staging and the whole diff review touch no container at all --
  // only run_python does. Gating the mode on Docker meant a user without it
  // could not edit a single file, for no reason. run_python disables itself
  // and says why (RunPythonTool checks the sandbox itself); the rest works.
  { id: "code", label: "Code", icon: Code2, blurb: "edits files in one folder" },
  { id: "email", label: "Email", needsEmail: true, icon: Mail, blurb: "drafts and sends, you confirm" },
  { id: "finance", label: "Finance", needsDocker: true, icon: LineChart, blurb: "market data, ~15min delayed" },
  { id: "computer", label: "Computer", icon: Monitor, blurb: "sees your screen, controls input" },
  // `soon` rather than quietly shipping it: Design has no tools of its own
  // (TaskMode.DESIGN appears nowhere in the backend), so choosing it produced
  // a General chat wearing a different label.
  { id: "design", label: "Design", icon: PenTool, soon: true, blurb: "no tools yet" },
];

// Long enough to survive a diagonal mouse path from an icon to the row beside
// it, short enough that leaving on purpose feels immediate.
const CLOSE_DELAY_MS = 160;
// A brief hesitation before opening, so sweeping the pointer across the rail on
// the way somewhere else does not flash the panel open behind you.
const OPEN_DELAY_MS = 110;

export default function ModeRail({
  mode, onStart, onOpenSettings, settingsActive, onOpenHub, hubActive,
}) {
  const { status } = useBackend();
  const dockerOff = status && !status.docker_up;
  const emailOff = status && !status.email_configured;
  const [open, setOpen] = useState(false);
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const scheduleOpen = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(true), OPEN_DELAY_MS);
  }, []);
  const scheduleClose = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(false), CLOSE_DELAY_MS);
  }, []);
  // Acting on a row should dismiss immediately — waiting out the close delay
  // after a click leaves the panel hanging over the screen you just asked for.
  const closeNow = useCallback(() => {
    clearTimeout(timer.current);
    setOpen(false);
  }, []);

  const stateOf = (m) => {
    if (m.soon) return { disabled: true, reason: "coming soon, no tools yet" };
    if (m.needsDocker && dockerOff) return { disabled: true, reason: "needs Docker running" };
    if (m.needsEmail && emailOff) return { disabled: true, reason: "set up email in Settings" };
    return { disabled: false, reason: "" };
  };

  const primary = MODES.slice(0, 1);   // General, pinned above the divider
  const rest = MODES.slice(1);

  const renderIcon = (m) => {
    const { disabled, reason } = stateOf(m);
    const Icon = m.icon;
    return (
      <button
        key={m.id}
        className={`rail-btn ${mode === m.id ? "active" : ""}`}
        disabled={disabled}
        // Kept as a fallback for anyone who reaches the rail by keyboard, where
        // there is no hover to open the panel with.
        title={disabled ? `${m.label}, ${reason}` : `New ${m.label.toLowerCase()} chat`}
        onClick={() => { onStart(m.id); closeNow(); }}
      >
        <Icon size={19} strokeWidth={1.8} />
      </button>
    );
  };

  const renderRow = (m, index) => {
    const { disabled, reason } = stateOf(m);
    const active = mode === m.id;
    return (
      <button
        key={m.id}
        className={`rail-row ${active ? "active" : ""}`}
        disabled={disabled}
        onClick={() => { onStart(m.id); closeNow(); }}
        // Rows fade in one after another rather than all at once. The delay is
        // per-item and tiny; the effect is that the list reads top-to-bottom
        // instead of appearing as a block.
        style={{ animationDelay: `${index * 18}ms` }}
      >
        <span className="rail-row-label">{m.label}</span>
        {disabled
          ? <span className="rail-row-reason">{reason}</span>
          : <span className="rail-row-blurb">{m.blurb}</span>}
        {!disabled && <kbd className="rail-row-key">Ctrl+{index + 1}</kbd>}
      </button>
    );
  };

  return (
    <div
      className="rail-zone"
      onMouseEnter={scheduleOpen}
      onMouseLeave={scheduleClose}
    >
      <div className="mode-rail">
        <div className="logo"><LogoMark size={18} /></div>
        <div className="rail-sep" />
        {primary.map(renderIcon)}
        <div className="rail-sep subtle" />
        <div className="rail-group">{rest.map(renderIcon)}</div>
        <div className="rail-spacer" />
        <button
          className={`rail-btn ${hubActive ? "active" : ""}`}
          title="Model hub"
          onClick={() => { onOpenHub(); closeNow(); }}
        >
          <Box size={19} strokeWidth={1.8} />
        </button>
        <button
          className={`rail-btn ${settingsActive ? "active" : ""}`}
          title="Settings"
          onClick={() => { onOpenSettings(); closeNow(); }}
        >
          <Settings size={19} strokeWidth={1.7} />
        </button>
      </div>

      {/* Mounted only while open so the entry animation replays every time.
          Kept inside .rail-zone so moving the pointer from an icon onto a row
          never leaves the hover region and never triggers the close timer. */}
      {open && (
        <div className="rail-flyout">
          <div className="rail-flyout-head">Start a new chat in</div>
          <div className="rail-rows">{MODES.map(renderRow)}</div>
          <div className="rail-spacer" />
          <button className="rail-row" onClick={() => { onOpenHub(); closeNow(); }}>
            <span className="rail-row-label">Model hub</span>
          </button>
          <button className="rail-row" onClick={() => { onOpenSettings(); closeNow(); }}>
            <span className="rail-row-label">Settings</span>
            <kbd className="rail-row-key">Ctrl+,</kbd>
          </button>
        </div>
      )}
    </div>
  );
}
