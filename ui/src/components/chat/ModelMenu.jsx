// The composer's model chip — Claude-bar style, but mode-aware.
//
// Click the chip -> a panel opens UPWARD showing, for the CURRENT mode:
//   * recommended models best-first, each marked against this PC:
//       installed        -> click to use
//       fits, missing    -> "Get · X GB" downloads inline, then auto-selects
//       too big          -> dimmed with the reason (teaches what an upgrade buys)
//   * other installed models (still selectable — recommendations advise, never restrict)
//   * Auto — defer to Settings→Models per-mode assignment / the default
//
// Recommendations come from /models/recommendations (hardware-ranked, cached
// here for 5 min — hardware doesn't change mid-session, installs refresh it).
import React, { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Cpu, Download, Loader2 } from "lucide-react";
import { api } from "../../api/client";
import { pullModel } from "../../api/models";
import { useBackend } from "../../stores/backend";
import { useChat } from "../../stores/chat";
import { useSettings } from "../../stores/settings";
import { useToasts } from "../../stores/toasts";

let recsCache = { at: 0, data: null };

export default function ModelMenu({ conversationId, mode }) {
  const [open, setOpen] = useState(false);
  const [recs, setRecs] = useState(recsCache.data);
  const [pulling, setPulling] = useState(null); // {model, pct}
  const panelRef = useRef(null);
  const { status, refreshStatus } = useBackend();
  const settingsValues = useSettings((s) => s.values);
  const override = useChat((s) => s.modelOverride[conversationId] || "");
  const setOverride = useChat((s) => s.setModelOverride);
  const pushToast = useToasts((s) => s.push);

  useEffect(() => {
    if (!open) return;
    if (Date.now() - recsCache.at > 5 * 60_000) {
      api.get("/models/recommendations")
        .then((data) => { recsCache = { at: Date.now(), data }; setRecs(data); })
        .catch(() => {});
    }
    const onClickAway = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    };
    const onEsc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClickAway);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClickAway);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const installed = (status && status.models) || [];
  const modeRecs = (recs && recs.modes && recs.modes[mode]) || [];
  const recNames = new Set(modeRecs.map((r) => r.model.split("-")[0]));
  const otherInstalled = installed.filter((m) => !recNames.has(m.name.split("-")[0]));
  const autoLabel = (settingsValues?.mode_models || {})[mode]
    || settingsValues?.default_model || "auto";

  const choose = (model) => {
    setOverride(conversationId, model);
    setOpen(false);
  };

  const download = async (model) => {
    setPulling({ model, pct: 0 });
    try {
      await pullModel(model, (pct) => setPulling({ model, pct }));
      recsCache = { at: 0, data: recsCache.data }; // stale — installs changed
      await refreshStatus();
      setOverride(conversationId, model);
      pushToast(`${model} installed and selected.`, "success");
      setOpen(false);
    } catch (e) {
      pushToast(e.message, "error");
    } finally {
      setPulling(null);
    }
  };

  return (
    <div className="model-menu" ref={panelRef}>
      <button className="model-chip" title="Model for this conversation" onClick={() => setOpen(!open)}>
        {override || `Auto · ${autoLabel}`} <ChevronDown size={12} />
      </button>

      {open && (
        <div className="model-panel">
          <div className="model-panel-head">
            <Cpu size={12} /> Best for {mode}
            {recs && <span className="model-budget">{recs.budget_gb}GB usable</span>}
          </div>

          {modeRecs.length === 0 && (
            <div className="model-row dim"><span className="grow">Loading recommendations…</span></div>
          )}

          {modeRecs.map((r) => {
            const isCurrent = override === r.model;
            const isPulling = pulling && pulling.model === r.model;
            return (
              <div key={r.model} className={`model-row ${!r.fits && !r.installed ? "dim" : ""}`}>
                <div className="grow">
                  <div className="model-name">{r.model}{isCurrent && <Check size={12} />}</div>
                  <div className="model-note">
                    {r.fits || r.installed ? r.note : `Needs ~${Math.round(r.size_gb * 1.15)}GB free — more than this PC has`}
                  </div>
                </div>
                {r.installed ? (
                  <button className="btn model-use" onClick={() => choose(r.model)}>Use</button>
                ) : isPulling ? (
                  <span className="model-pull"><Loader2 size={12} className="spin" /> {pulling.pct}%</span>
                ) : r.fits ? (
                  <button className="btn model-use" disabled={!!pulling || !recs?.ollama_up}
                    onClick={() => download(r.model)}>
                    <Download size={11} /> {r.size_gb}GB
                  </button>
                ) : null}
              </div>
            );
          })}

          {otherInstalled.length > 0 && <div className="model-panel-head">Installed</div>}
          {otherInstalled.map((m) => (
            <div key={m.name} className="model-row">
              <div className="grow"><div className="model-name">{m.name}{override === m.name && <Check size={12} />}</div></div>
              <button className="btn model-use" onClick={() => choose(m.name)}>Use</button>
            </div>
          ))}

          <div className="model-row">
            <div className="grow">
              <div className="model-name">Auto{!override && <Check size={12} />}</div>
              <div className="model-note">Follow Settings → Models ({autoLabel})</div>
            </div>
            <button className="btn model-use" onClick={() => choose("")}>Use</button>
          </div>
        </div>
      )}
    </div>
  );
}
