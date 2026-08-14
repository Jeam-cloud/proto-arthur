import React from "react";
import { LogoMark } from "../ModeRail";

// `slow` is not an error state — it is the same boot, told honestly. The first
// launch after an install or update has to warm a lot of Python that later
// launches get from the OS file cache, and a spinner that says nothing for 90
// seconds is indistinguishable from one that has hung.
export default function BootScreen({ slow = false }) {
  return (
    <div className="boot">
      <LogoMark size={44} />
      <div className="spinner" />
      <div style={{ fontSize: 13 }}>Starting Arthur…</div>
      {slow && (
        <div style={{ fontSize: 12, color: "var(--tmut)", maxWidth: 340, textAlign: "center" }}>
          Still starting — the first launch after an update takes longer while
          Arthur loads its local models.
        </div>
      )}
    </div>
  );
}
