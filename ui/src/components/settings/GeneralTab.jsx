import React from "react";
import { useSettings } from "../../stores/settings";
import { useToasts } from "../../stores/toasts";

export default function GeneralTab() {
  const { values, update } = useSettings();
  const pushToast = useToasts((s) => s.push);
  if (!values) return null;

  const pickWorkspace = async () => {
    if (!window.arthur) return pushToast("Folder picker needs the desktop app.", "error");
    const folder = await window.arthur.pickFolder();
    if (folder) {
      await update({ workspace_root: folder });
      pushToast("Workspace folder set.", "success");
    }
  };

  return (
    <>
      <h2>General</h2>
      <div className="section-sub">Basics and the coding workspace.</div>

      <div className="field">
        <label>Text size</label>
        <div className="field-row">
          <input
            type="range" min="0.85" max="1.3" step="0.05"
            value={values.font_scale || 1}
            onChange={(e) => update({ font_scale: Number(e.target.value) })}
            style={{ flex: 1 }}
          />
          <span style={{ fontSize: 12, color: "var(--mid)", width: 40 }}>
            {Math.round((values.font_scale || 1) * 100)}%
          </span>
        </div>
      </div>

      <div className="field">
        <label>Default folder for new chats</label>
        <div className="field-row">
          <input type="text" readOnly value={values.workspace_root || "Not set"} className="grow" />
          <button className="btn" onClick={pickWorkspace}>Choose…</button>
        </div>
        {/* Reworded because the meaning changed. Each chat now has its OWN
            folder (set from the bar at the top of Code mode); this setting is
            only what a NEW chat starts with, and changing it can no longer
            widen what an existing conversation is allowed to reach. */}
        <div className="hint">
          Each chat has its own folder, chosen at the top of Code mode. This is only what a new
          chat starts with — changing it never affects a conversation you've already set.
        </div>
      </div>

      <div className="field">
        <label>Memory</label>
        <div className="field-row">
          <span className="grow" style={{ fontSize: 13 }}>Automatically remember useful facts from chats</span>
          <label className="switch">
            <input
              type="checkbox"
              checked={!!values.memory_enabled}
              onChange={(e) => update({ memory_enabled: e.target.checked })}
            />
            <span className="track" /><span className="thumb" />
          </label>
        </div>
      </div>

      <div className="field">
        <label>Global hotkey</label>
        <div className="hint" style={{ marginTop: 0 }}>
          <strong style={{ color: "var(--text)" }}>Ctrl+Shift+A</strong> summons the quick widget from anywhere.
        </div>
      </div>
    </>
  );
}
