// Model hub: a centered POPUP, opened from the box icon on the mode rail (or
// Ctrl+K > Model hub). Two tabs -- Discover (find something new) and Installed
// (manage what you have).
//
// WHY a popup and not a page: you open the hub to answer one question, then go
// back to what you were doing. A page navigation would throw away your chat
// scroll position to do that. As an overlay it floats above whatever view is
// already mounted, and Esc puts you back exactly where you were.
//
// WHY the CONCEPT is borrowed but no code is: Odysseus (AGPL,
// github.com/odysseus-dev/odysseus) has a "Cookbook" with the same
// tabbed-popup, search-and-score-against-your-hardware idea. Only the idea is
// taken here. Their version also manages remote SSH servers, vLLM/SGLang serve
// engines and raw HuggingFace/GGUF pulls, none of which apply to Arthur
// (single machine, Ollama only, no serving step).
//
// HONESTY NOTE, and the one place this deviates from the mockup on purpose:
// the mockup's table has a Speed (tokens/sec) column. Arthur never runs a
// model to time it, and real t/s depends on your exact GPU, quant and context
// length -- any number here would be invented. The column shows Ctx instead,
// which is a fact we can actually read off the model's library page. Same
// reasoning is why "Score" is described in-page as a fit calculation rather
// than a benchmark.
import React, { useEffect, useState } from "react";
import { Download, Loader2, Search, X, Play, Trash2, Box, HardDrive } from "lucide-react";
import { api } from "../api/client";
import { pullModel, deleteModel } from "../api/models";
import { useBackend } from "../stores/backend";
import { useSettings } from "../stores/settings";
import { useToasts } from "../stores/toasts";

const TYPES = [
  ["all", "All types"],
  ["general", "General"],
  ["code", "Code"],
  ["reasoning", "Reasoning"],
  ["embed", "Embedding"],
];

// Maps the backend's fit label to a colour. One lookup so the FIT cell and the
// SCORE cell can never disagree. Labels are produced by
// core/model_recs.catalog_search; keep the two sets in step.
const FIT_COLOR = {
  OPTIMAL: "var(--green)",
  SUITABLE: "#a7d8a0",
  MARGINAL: "var(--yellow)",
  UNSUITABLE: "var(--red)",
};

export default function ModelHub({ onClose }) {
  const { status, refreshStatus } = useBackend();
  const { values, update } = useSettings();
  const pushToast = useToasts((s) => s.push);

  const [tab, setTab] = useState("discover");
  const [data, setData] = useState(null); // {budget_gb, ollama_up, hardware, results}
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const [directPull, setDirectPull] = useState("");
  const [expanded, setExpanded] = useState(null);
  const [pulling, setPulling] = useState(null); // {model, pct}
  const [deleting, setDeleting] = useState(null);
  const [scanning, setScanning] = useState(false);

  const load = (q = query, t = type) =>
    api
      .get(`/models/catalog?q=${encodeURIComponent(q)}&type=${encodeURIComponent(t)}`)
      .then(setData)
      .catch(() => {});

  useEffect(() => { load("", "all"); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced so typing doesn't fire a request per keystroke. Scores are
  // computed server-side against your real budget, so this can't be a purely
  // client-side filter.
  useEffect(() => {
    const t = setTimeout(() => load(query, type), 250);
    return () => clearTimeout(t);
  }, [query, type]); // eslint-disable-line react-hooks/exhaustive-deps

  // Esc closes, but only when a download isn't running -- losing the progress
  // bar mid-pull looks like the download died, even though Ollama keeps going.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape" && !pulling) { e.stopPropagation(); onClose(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, pulling]);

  const rescan = async () => {
    setScanning(true);
    await Promise.all([refreshStatus(), load()]);
    setScanning(false);
    pushToast("Hardware re-detected. Compatibility ratings updated.", "success");
  };

  // Ollama itself is the source of truth on whether a model name is real:
  // this just calls /models/pull and surfaces whatever comes back, rather
  // than Arthur pre-validating against its own (deliberately small) catalog.
  const install = async (model) => {
    if (pulling) return false;
    setPulling({ model, pct: 0 });
    try {
      await pullModel(model, (pct) => setPulling({ model, pct }));
      await Promise.all([refreshStatus(), load()]);
      pushToast(`${model} installed successfully.`, "success");
      return true;
    } catch (e) {
      pushToast(e.message, "error");
      return false;
    } finally {
      setPulling(null);
    }
  };

  const submitDirectPull = async () => {
    const name = directPull.trim();
    if (!name) return;
    if (await install(name)) setDirectPull("");
  };

  // Irreversible, so warn extra hard when the model is in active use, and
  // reload settings afterward: the backend clears default_model / a mode's
  // assignment itself when you delete the model they point at, and pushing
  // our stale local copy back would undo that.
  const doDelete = async (model) => {
    const usedAsDefault = values?.default_model === model;
    let warning = `Uninstall ${model}. Its disk space will be released and the action cannot be reversed.`;
    if (usedAsDefault) warning += "\n\nThis model is currently set as the default.";
    warning += "\n\nProceed.";
    if (!window.confirm(warning)) return;

    setDeleting(model);
    try {
      await deleteModel(model);
      await Promise.all([refreshStatus(), useSettings.getState().load(), load()]);
      pushToast(`${model} uninstalled.`, "success");
    } catch (e) {
      pushToast(e.message, "error");
    } finally {
      setDeleting(null);
    }
  };

  const use = (model) => {
    update({ default_model: model });
    pushToast(`Default model set to ${model}.`, "success");
  };

  const installed = status?.models || [];
  const totalGb = installed.reduce((sum, m) => sum + (m.size_bytes || 0), 0) / 1e9;
  const hw = data?.hardware;
  const chips = hw
    ? [hw.gpu, hw.vram_gb ? `${hw.vram_gb} GB VRAM` : null, `${hw.ram_gb} GB RAM`, `${hw.cpu_count} cores`]
        .filter(Boolean)
    : [];

  return (
    <div className="hub-backdrop" onClick={onClose}>
      <div className="hub-modal" onClick={(e) => e.stopPropagation()}>
        <div className="hub-header">
          <span className="hub-header-icon"><Box size={15} strokeWidth={1.8} /></span>
          <h2>Model hub</h2>
          {data && <span className="hub-budget">{data.budget_gb}GB budget</span>}
          {/* The INSTALLED badges go stale.
              `/models/catalog` computes them server-side against Ollama's list
              at request time, but the hub only ever loaded on mount and on a
              search keystroke -- so a model installed or removed from OUTSIDE
              Arthur (`ollama pull`/`rm` in a terminal, which is common) kept
              whatever badge it had when the panel opened. Refresh re-asks both
              Ollama and the catalog, which is also what makes the count on the
              Installed tab trustworthy. */}
          <button
            className="hub-close"
            title="Refresh installed models"
            disabled={refreshing || !!pulling}
            onClick={refresh}
          >
            <RefreshCw size={15} strokeWidth={1.9} className={refreshing ? "spin" : ""} />
          </button>
          <button className="hub-close" title="Close (Esc)" onClick={onClose}>
            <X size={16} strokeWidth={1.9} />
          </button>
        </div>

        <div className="hub-tabs">
          <button className={`hub-tab ${tab === "discover" ? "active" : ""}`} onClick={() => setTab("discover")}>
            <Search size={14} strokeWidth={1.8} /> Catalog
          </button>
          <button className={`hub-tab ${tab === "installed" ? "active" : ""}`} onClick={() => setTab("installed")}>
            <HardDrive size={14} strokeWidth={1.8} /> Installed
            <span className="hub-tab-count">{installed.length}</span>
          </button>
        </div>

        <div className="hub-body">
          <div className="hub-inner">
            {!status?.ollama_up && (
              <div className="card" style={{ borderColor: "rgba(252,165,165,0.4)" }}>
                <div className="card-title" style={{ color: "var(--red)" }}>Ollama is not running</div>
                <div className="card-sub">Ollama must be running before models can be installed or selected.</div>
              </div>
            )}

            {tab === "discover" && (
              <>
                <div className="card" style={{ padding: 16, marginBottom: 14 }}>
                  <div className="hub-card-title">Install by name</div>
                  <div className="hub-card-sub">
                    Enter any model identifier exactly as listed on{" "}
                    <a href="https://ollama.com/library" target="_blank" rel="noreferrer">ollama.com/library</a>.
                    Installation proceeds directly, without a catalog lookup.
                  </div>
                  <div className="hub-pull-row">
                    <input
                      type="text" placeholder="mistral-nemo:12b"
                      value={directPull} disabled={!!pulling}
                      onChange={(e) => setDirectPull(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") submitDirectPull(); }}
                    />
                    {pulling && pulling.model === directPull.trim() ? (
                      <span className="hub-pull-progress"><Loader2 size={13} className="spin" /> {pulling.pct}%</span>
                    ) : (
                      <button
                        className="btn primary" style={{ height: 40, padding: "0 18px", flexShrink: 0 }}
                        disabled={!directPull.trim() || !!pulling || !status?.ollama_up}
                        onClick={submitDirectPull}
                      >
                        Pull
                      </button>
                    )}
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 2 }}>
                  <div style={{ flex: 1 }}>
                    <div className="hub-card-title">Hardware compatibility</div>
                    <div className="hub-card-sub">
                      Each model is rated from 0 to 100 against this machine. The rating compares
                      the download size with the available memory; a higher value indicates a
                      better fit. This is a compatibility estimate, not a performance benchmark.
                    </div>
                  </div>
                  <button
                    className="btn" style={{ height: 32, padding: "0 13px", fontSize: 12, flexShrink: 0 }}
                    onClick={rescan} disabled={scanning}
                  >
                    {scanning ? <><Loader2 size={12} className="spin" /> Scanning…</> : "Rescan"}
                  </button>
                </div>

                <div className="hub-filters">
                  <select className="model-picker" value={type} onChange={(e) => setType(e.target.value)}>
                    {TYPES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                  </select>
                  <div className="hub-search">
                    <Search size={14} strokeWidth={1.8} color="var(--tmut)" />
                    <input
                      type="text" placeholder="Search models…"
                      value={query} onChange={(e) => setQuery(e.target.value)}
                    />
                  </div>
                </div>

                {chips.length > 0 && (
                  <div className="hub-chips">
                    <span className="hub-chips-label">Detected</span>
                    {chips.map((c) => <span key={c} className="hub-chip">{c}</span>)}
                  </div>
                )}

                <div className="hub-thead">
                  <span style={{ width: 56, flexShrink: 0 }}>Fit</span>
                  <span style={{ flex: 1, minWidth: 0 }}>Model</span>
                  <span style={{ width: 48, flexShrink: 0 }}>Param</span>
                  <span style={{ width: 52, flexShrink: 0 }}>Size</span>
                  <span style={{ width: 48, flexShrink: 0 }}>Ctx</span>
                  <span style={{ width: 44, flexShrink: 0, textAlign: "right" }}>Score</span>
                </div>

                {!data && <div className="hub-msg">Loading…</div>}
                {data && data.results.length === 0 && (
                  <div className="hub-msg">No matches{query ? ` for "${query}"` : ""}.</div>
                )}

                {(data?.results || []).map((r) => {
                  const isOpen = expanded === r.model;
                  const isPulling = pulling && pulling.model === r.model;
                  const isDefault = values?.default_model === r.model;
                  return (
                    <div key={r.model}>
                      <div
                        className={`hub-row ${isOpen ? "open" : ""}`}
                        onClick={() => setExpanded(isOpen ? null : r.model)}
                      >
                        <span className="hub-fit" style={{ color: FIT_COLOR[r.fit] }}>{r.fit}</span>
                        <span className="hub-name">
                          <span className="hub-name-text">{r.model}</span>
                          {r.moe && <span className="hub-tag">MoE</span>}
                          {r.installed && <span className="hub-tag installed">INSTALLED</span>}
                        </span>
                        <span className="hub-cell" style={{ width: 48 }}>{r.params_b}B</span>
                        <span className="hub-cell" style={{ width: 52 }}>{r.size_gb}G</span>
                        <span className="hub-cell" style={{ width: 48 }}>{r.ctx}</span>
                        <span className="hub-score" style={{ color: FIT_COLOR[r.fit] }}>{r.score}</span>
                      </div>

                      {isOpen && (
                        <div className="hub-detail">
                          <div className="hub-detail-head">
                            <span className="hub-detail-name">{r.org} / {r.model}</span>
                            <span className="hub-detail-source">ollama</span>
                          </div>
                          <div className="hub-detail-sub">{r.desc}</div>
                          <div className="hub-chips" style={{ marginTop: 11, marginBottom: 13 }}>
                            <span className="hub-chip">{r.type}</span>
                            <span className="hub-chip">ctx {r.ctx}</span>
                            <span className="hub-chip">runs {r.runs}</span>
                            <span className="hub-chip">{r.size_gb}GB download</span>
                          </div>
                          <div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>
                            {!r.installed && (
                              <button
                                className="btn primary" style={{ height: 36 }}
                                disabled={!!pulling || !status?.ollama_up}
                                title={r.fits ? "" : "Exceeds the available memory budget. It will run, but slowly."}
                                onClick={(e) => { e.stopPropagation(); install(r.model); }}
                              >
                                {isPulling
                                  ? <><Loader2 size={13} className="spin" /> {pulling.pct}%</>
                                  : <><Download size={13} /> Install</>}
                              </button>
                            )}
                            {r.installed && !isDefault && (
                              <button className="btn primary" style={{ height: 36 }} onClick={(e) => { e.stopPropagation(); use(r.model); }}>
                                <Play size={13} /> Set as default
                              </button>
                            )}
                            {isDefault && <span className="pill ok" style={{ alignSelf: "center" }}>default</span>}
                            {r.installed && (
                              <button
                                className="btn danger" style={{ height: 36 }}
                                disabled={deleting === r.model}
                                onClick={(e) => { e.stopPropagation(); doDelete(r.model); }}
                              >
                                {deleting === r.model ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />} Uninstall
                              </button>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}

                <div className="hint" style={{ marginTop: 14 }}>
                  This table covers Arthur's verified catalog rather than the complete Ollama
                  library. Models outside the catalog can be installed by name above.
                </div>
              </>
            )}

            {tab === "installed" && (
              <>
                <div className="hub-card-sub" style={{ marginTop: 0, marginBottom: 14 }}>
                  {installed.length > 0
                    ? <><strong>{totalGb.toFixed(1)}GB</strong> of disk in use across {installed.length} model{installed.length === 1 ? "" : "s"}. Per-mode assignments are configured in Settings, Models.</>
                    : "No models are currently installed. The Catalog tab lists options compatible with this machine."}
                </div>

                {installed.map((m) => {
                  const gb = (m.size_bytes || 0) / 1e9;
                  const isDefault = values?.default_model === m.name;
                  return (
                    <div key={m.name} className="card card-row">
                      <div className="grow">
                        <div style={{ fontFamily: "var(--mono)", fontSize: 13.5 }}>{m.name}</div>
                        <div className="card-sub">{m.parameter_size || "?"} · {gb.toFixed(1)}GB on disk</div>
                      </div>
                      {isDefault
                        ? <span className="pill ok">default</span>
                        : <button className="btn" style={{ height: 34, padding: "0 15px", fontSize: 12.5 }} onClick={() => use(m.name)}>Set as default</button>}
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
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
