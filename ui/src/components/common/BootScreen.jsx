import React from "react";
import { LogoMark } from "../ModeRail";

export default function BootScreen() {
  return (
    <div className="boot">
      <div className="logo"><LogoMark size={26} /></div>
      <div className="spinner" />
      <div style={{ fontSize: 13 }}>Starting Arthur…</div>
    </div>
  );
}
