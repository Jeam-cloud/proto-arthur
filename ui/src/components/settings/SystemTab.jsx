// "This computer" -- split out of ModelsTab so hardware facts (what Arthur
// read off your machine) live separately from model management (what you do
// about it), matching the reference screenshots rian shared.
//
// WHY only 3 cards: rian asked to only show specs that actually feed model
// recommendations. python/core/hardware.py's budget formula only looks at
// CPU core count, RAM, and GPU/VRAM -- OS name doesn't affect which model
// Arthur suggests, so it's cut rather than shown as filler.
//
// WHY no VRAM-used gauge: /system/hardware doesn't track live VRAM usage,
// only static specs. Rather than invent a number the backend doesn't have,
// this tab sticks to what it can show honestly.
import React, { useEffect, useState } from "react";
import { Cpu, MemoryStick, Microchip, RefreshCw } from "lucide-react";
import { api } from "../../api/client";

// Mirrors core/hardware.py MODEL_TIERS (min_budget_gb, tier label, what it
// buys you) -- kept in sync by hand since the frontend has no reason to fetch
// this table over the wire, it's tiny and rarely changes.
const TIERS = [
  { min: 32, label: "32GB+ budget", note: "Runs the largest local models comfortably" },
  { min: 16, label: "16 - 32GB budget", note: "Strong mid-size models, a good balance of speed and quality" },
  { min: 8, label: "8 - 16GB budget", note: "Solid all-rounders that respond fast" },
  { min: 0, label: "Under 8GB budget", note: "Compact models -- still capable, especially for quick tasks" },
];

export default function SystemTab() {
  const [hw, setHw] = useState(null);
  const [loading, setLoading] = useState(true);

  const scan = () => {
    setLoading(true);
    api.get("/system/hardware").then(setHw).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(() => { scan(); }, []);

  if (!hw) {
    return (
      <>
        <h2>This computer</h2>
        <div className="section-sub">{loading ? "Reading straight off your machine…" : "Couldn't read your hardware."}</div>
      </>
    );
  }

  // Mirrors the budget formula in core/hardware.py detect(): VRAM if there's
  // a discrete GPU, otherwise system RAM minus headroom for the OS.
  const budgetGb = hw.gpu ? hw.gpu.vram_gb : Math.max(hw.ram_gb - 4, 2);
  const currentTier = TIERS.find((t) => budgetGb >= t.min);

  const cards = [
    { icon: Cpu, label: "Processor", value: `${hw.cpu_count} cores`, sub: "Physical cores detected" },
    { icon: MemoryStick, label: "Memory", value: `${hw.ram_gb} GB`, sub: "System RAM" },
    { icon: Microchip, label: "Graphics", value: hw.gpu ? hw.gpu.name : "No NVIDIA GPU detected", sub: hw.gpu ? `${hw.gpu.vram_gb} GB VRAM` : "Models run on CPU/RAM instead" },
  ];

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <h2 style={{ marginBottom: 0 }}>This computer</h2>
        <button className="btn" style={{ marginLeft: "auto", padding: "6px 12px", fontSize: 12 }} onClick={scan} disabled={loading}>
          <RefreshCw size={12} className={loading ? "spin" : ""} /> {loading ? "Scanning…" : "Rescan"}
        </button>
      </div>
      <div className="section-sub">
        Read straight off your machine. Arthur uses these numbers to decide which models
        it recommends and which it warns you about. Rescan after adding RAM, plugging in a
        different GPU, or moving to another machine.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 11, marginBottom: 14 }}>
        {cards.map((c) => (
          <div key={c.label} className="card">
            <div style={{ display: "flex", alignItems: "center", gap: 7, color: "var(--tmut)", marginBottom: 9 }}>
              <c.icon size={14} strokeWidth={1.7} />
              <span style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.07em" }}>{c.label}</span>
            </div>
            <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em" }}>{c.value}</div>
            <div style={{ fontSize: 11.5, color: "var(--tmut)", marginTop: 3, lineHeight: 1.45 }}>{c.sub}</div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-title">Model budget</div>
        <div className="card-sub">
          Comfortable ceiling for a single model on this hardware: about <strong>{budgetGb}GB</strong>.
          {hw.gpu ? " Based on your GPU's VRAM." : " Based on system RAM, with headroom left for the OS."}
        </div>
      </div>

      <div className="card" style={{ marginTop: 11 }}>
        <div className="card-title" style={{ marginBottom: 4 }}>What that buys you</div>
        <div className="card-sub" style={{ marginBottom: 13 }}>
          General guidance by budget tier, not tied to any one model, see the Model hub for
          specific picks and their fit against your hardware.
        </div>
        {TIERS.map((t) => (
          <div key={t.label} style={{ display: "flex", alignItems: "center", gap: 11, padding: "9px 0", borderBottom: "1px solid var(--border)" }}>
            <span
              className={`pill ${t === currentTier ? "ok" : "off"}`}
              style={{ width: 8, height: 8, borderRadius: "50%", padding: 0, flexShrink: 0, opacity: t === currentTier ? 1 : 0.35 }}
            />
            <span style={{ width: 140, flexShrink: 0, fontSize: 12.5, fontWeight: t === currentTier ? 600 : 400, color: t === currentTier ? "var(--text)" : "var(--tmut)" }}>
              {t.label}{t === currentTier ? " (you)" : ""}
            </span>
            <span style={{ fontSize: 12, color: "var(--tmut)" }}>{t.note}</span>
          </div>
        ))}
      </div>
    </>
  );
}
