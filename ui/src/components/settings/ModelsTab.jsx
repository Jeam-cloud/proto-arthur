import React, { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useBackend } from "../../stores/backend";
import { useSettings } from "../../stores/settings";

export default function ModelsTab() {
  const { status, refreshStatus } = useBackend();
  const { values, update } = useSettings();
  const [hw, setHw] = useState(null);

  useEffect(() => { api.get("/system/hardware").then(setHw).catch(() => {}); }, []);

  const models = status?.models || [];

  return (
    <>
      <h2>Models</h2>
      <div className="section-sub">
        Everything runs through Ollama on this machine. Bigger models are smarter but slower.
      </div>

      {hw && (
        <div className="card">
          <div className="card-title">This machine</div>
          <div className="card-sub">
            {hw.ram_gb}GB RAM · {hw.cpu_count} cores{hw.gpu ? ` · ${hw.gpu.name} (${hw.gpu.vram_gb}GB VRAM)` : " · no NVIDIA GPU detected"}
            {" — recommended: "}<strong style={{ color: "var(--accent)" }}>{hw.recommendation.chat_model}</strong>
          </div>
        </div>
      )}

      {!status?.ollama_up && (
        <div className="card" style={{ borderColor: "rgba(248,113,113,0.4)" }}>
          <div className="card-title" style={{ color: "var(--red)" }}>Ollama is not running</div>
          <div className="card-sub">Start Ollama to see and use your models.</div>
        </div>
      )}

      {models.map((m) => (
        <div key={m.name} className="card card-row">
          <div className="grow">
            <div className="card-title">{m.name}</div>
            <div className="card-sub">
              {m.parameter_size || "?"} · {(m.size_bytes / 1e9).toFixed(1)}GB on disk
            </div>
          </div>
          {values?.default_model === m.name
            ? <span className="pill ok">default</span>
            : <button className="btn" onClick={() => update({ default_model: m.name })}>Use</button>}
        </div>
      ))}

      <div className="hint" style={{ marginTop: 10 }}>
        Install more models with <code>ollama pull &lt;name&gt;</code>, then hit refresh.
        <button className="btn" style={{ marginLeft: 8, padding: "3px 10px", fontSize: 11.5 }} onClick={refreshStatus}>
          Refresh
        </button>
      </div>

      <h2 style={{ fontSize: 14, margin: "22px 0 4px" }}>Model per mode</h2>
      <div className="section-sub" style={{ marginBottom: 10 }}>
        Use a stronger model where it matters (e.g. a bigger one for Research, a fast one
        for General). "Default" uses your default model above. The picker in the chat
        header overrides both for a single conversation.
      </div>
      {MODE_LIST.map((m) => (
        <div key={m.id} className="card card-row" style={{ padding: "10px 14px" }}>
          <span className="grow" style={{ fontSize: 13 }}>{m.label}</span>
          <select
            className="model-picker"
            value={(values?.mode_models || {})[m.id] || ""}
            onChange={(e) => update({
              mode_models: { ...(values?.mode_models || {}), [m.id]: e.target.value },
            })}
          >
            <option value="">Default</option>
            {models.map((mod) => (
              <option key={mod.name} value={mod.name}>{mod.name}</option>
            ))}
          </select>
        </div>
      ))}
    </>
  );
}

const MODE_LIST = [
  { id: "general", label: "General" },
  { id: "research", label: "Research" },
  { id: "code", label: "Code" },
  { id: "email", label: "Email" },
  { id: "finance", label: "Finance" },
  { id: "computer", label: "Computer" },
  { id: "design", label: "Design" },
];
