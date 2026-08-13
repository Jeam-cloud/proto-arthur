// Settings > Models: manage what's ALREADY installed. Finding and downloading
// new models moved to the top-level Model hub page (components/ModelHub.jsx)
// in the July 2026 mockup update -- this tab is deliberately the boring half:
// which model is the default, which model each mode uses, and how much disk
// they're eating.
//
// The fit dot on each card answers "will this one actually run well here?"
// using the same budget math as the hub's score and the composer's model menu
// (core/hardware.py's budget, core/model_recs.py's 1.15x headroom rule), so a
// model can't read as comfortable in one place and tight in another.
import React, { useEffect, useState } from "react";
import { CircleCheck, CircleAlert, CircleX, Loader2, RefreshCw, Trash2, Library } from "lucide-react";
import { api } from "../../api/client";
import { deleteModel } from "../../api/models";
import { useBackend } from "../../stores/backend";
import { useConfirm } from "../../stores/confirm";
import { useSettings } from "../../stores/settings";
import { useToasts } from "../../stores/toasts";
import { MODES } from "../ModeRail";

// Three buckets, matching the mockup's fitOf(): comfortable below ~62% of
// budget, tight up to 100%, over budget past that. The 62% figure leaves room
// for the KV-cache and context window, which grow with conversation length.
//
// Wording is the coarse three-band version of the hub's four OPTIMAL /
// SUITABLE / MARGINAL / UNSUITABLE labels. Both describe the hardware
// relationship rather than grading the model.
function fitOf(gb, budget) {
  if (!gb || gb <= budget * 0.62) {
    return { icon: CircleCheck, color: "var(--green)", bg: "rgba(110,231,183,.12)", label: "Operates within budget" };
  }
  if (gb <= budget) {
    return { icon: CircleAlert, color: "var(--yellow)", bg: "rgba(252,211,77,.12)", label: "Within budget, limited headroom" };
  }
  return { icon: CircleX, color: "var(--red)", bg: "rgba(252,165,165,.12)", label: "Exceeds budget, will use system RAM" };
}

function FitDot({ fit, size = 14 }) {
  const Icon = fit.icon;
  return (
    <span
      title={fit.label}
      style={{
        width: 24, height: 24, borderRadius: 8, flexShrink: 0, display: "inline-flex",
        alignItems: "center", justifyContent: "center", color: fit.color, background: fit.bg,
      }}
    >
      <Icon size={size} strokeWidth={2.1} />
    </span>
  );
}

export default function ModelsTab({ onOpenHub }) {
  const { status, refreshStatus } = useBackend();
  const { values, update } = useSettings();
  const pushToast = useToasts((s) => s.push);
  const ask = useConfirm((s) => s.ask);

  const [budget, setBudget] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  // Budget comes from the same /models/catalog endpoint the hub uses, rather
  // than a second hardware call, so both screens always agree on the number.
  useEffect(() => {
    api.get("/models/catalog?q=__none__").then((d) => setBudget(d.budget_gb)).catch(() => {});
  }, []);

  const models = status?.models || [];
  const totalGb = models.reduce((sum, m) => sum + (m.size_bytes || 0), 0) / 1e9;

  const refresh = async () => {
    setRefreshing(true);
    await refreshStatus();
    setRefreshing(false);
    pushToast("Model list refreshed.", "info");
  };

  // Deletion is irreversible, so we warn extra hard when the model is in
  // active use, and reload settings afterward: the backend clears
  // default_model / a mode's assignment itself when you delete the model
  // they point at, and pushing our stale local copy back would undo that.
  const doDelete = (name) => {
    const usedAsDefault = values?.default_model === name;
    const usedByModes = MODES.filter((m) => (values?.mode_models || {})[m.id] === name).map((m) => m.label);
    let body = `${name} is removed from this computer and its disk space released. `
      + "Downloading it again means fetching the whole model.";
    if (usedAsDefault) body += " It is currently your default model.";
    if (usedByModes.length) body += ` It is assigned to: ${usedByModes.join(", ")}.`;

    ask({
      title: `Uninstall ${name}?`,
      body,
      confirmLabel: "Uninstall",
      onConfirm: async () => {
        setDeleting(name);
        try {
          await deleteModel(name);
          await Promise.all([refreshStatus(), useSettings.getState().load()]);
          pushToast(`${name} uninstalled.`, "success");
        } catch (e) {
          pushToast(e.message, "error");
        } finally {
          setDeleting(null);
        }
      },
    });
  };

  const b = budget || 8;
  const legend = [
    { gb: b * 0.3, text: "Within budget" },
    { gb: b * 0.9, text: "Limited headroom" },
    { gb: b * 3, text: "Exceeds budget" },
  ];

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <h2 style={{ marginBottom: 0 }}>Models</h2>
        <button
          className="btn" style={{ marginLeft: "auto", padding: "6px 12px", fontSize: 12, height: 32 }}
          onClick={refresh} disabled={refreshing}
        >
          <RefreshCw size={12} className={refreshing ? "spin" : ""} /> Refresh
        </button>
      </div>
      <div className="section-sub">
        All models run locally through Ollama. The indicator on each entry reflects its fit
        against this machine's memory budget. Additional models are available in the Model hub.
      </div>

      {!status?.ollama_up && (
        <div className="card" style={{ borderColor: "rgba(252,165,165,0.4)" }}>
          <div className="card-title" style={{ color: "var(--red)" }}>Ollama is not running</div>
          <div className="card-sub">Ollama must be running before models can be used or managed.</div>
        </div>
      )}

      <div
        className="card card-row"
        style={{ gap: 16, flexWrap: "wrap", fontSize: 11.5, color: "var(--tmut)", padding: "11px 15px" }}
      >
        {legend.map((l) => {
          const fit = fitOf(l.gb, b);
          return (
            <span key={l.text} style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
              <FitDot fit={fit} size={12} /> {l.text}
            </span>
          );
        })}
      </div>

      {models.map((m) => {
        const gb = (m.size_bytes || 0) / 1e9;
        const fit = fitOf(gb, b);
        const isDefault = values?.default_model === m.name;
        return (
          <div key={m.name} className="card card-row">
            <FitDot fit={fit} />
            <div className="grow">
              <div style={{ fontFamily: "var(--mono)", fontSize: 13.5 }}>{m.name}</div>
              <div className="card-sub">{m.parameter_size || "?"} · {gb.toFixed(1)}GB on disk</div>
            </div>
            {isDefault
              ? <span className="pill ok">default</span>
              : <button className="btn" style={{ height: 34, padding: "0 15px", fontSize: 12.5 }} onClick={() => update({ default_model: m.name })}>Set as default</button>}
            <button
              className="btn danger" style={{ height: 34, padding: "0 10px" }}
              disabled={deleting === m.name} title={`Uninstall ${m.name}`}
              onClick={() => doDelete(m.name)}
            >
              {deleting === m.name ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />}
            </button>
          </div>
        );
      })}

      {models.length === 0 && (
        <div className="hint">No models are currently installed. The Model hub lists options compatible with this machine.</div>
      )}

      <div className="hint" style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        {models.length > 0 && (
          <span>
            <strong>{totalGb.toFixed(1)}GB</strong> of disk in use across {models.length} model{models.length === 1 ? "" : "s"}
            {budget ? ` · ${budget}GB budget per model` : ""}
          </span>
        )}
        {onOpenHub && (
          <button className="btn" style={{ height: 30, padding: "0 12px", fontSize: 12 }} onClick={onOpenHub}>
            <Library size={12} /> Open Model hub
          </button>
        )}
      </div>

      <h2 style={{ fontSize: 15, margin: "28px 0 6px" }}>Model per mode</h2>
      <div className="section-sub" style={{ marginBottom: 15 }}>
        Assign a more capable model to the modes that require one. "Default" applies the global
        default selected above. The picker in the composer overrides both for a single conversation.
      </div>
      {MODES.map((m) => (
        <div key={m.id} className="card card-row" style={{ padding: "11px 16px", marginBottom: 9 }}>
          <span className="grow" style={{ fontSize: 13.5 }}>{m.label}</span>
          <select
            className="model-picker"
            value={(values?.mode_models || {})[m.id] || ""}
            onChange={(e) => update({
              mode_models: { ...(values?.mode_models || {}), [m.id]: e.target.value },
            })}
          >
            <option value="">Default</option>
            {models.map((mod) => <option key={mod.name} value={mod.name}>{mod.name}</option>)}
          </select>
        </div>
      ))}
    </>
  );
}
