// Cookbook: search any model in Arthur's catalog and see a live fit score
// against THIS machine, sortable, with one-click install.
//
// WHY this exists (July 2026, rian's request): Odysseus (AGPL,
// github.com/odysseus-dev/odysseus) has a "Cookbook" feature -- hardware-aware
// model recommendations, downloads, and serving, with a search-and-score
// table. Only the CONCEPT is borrowed here, no code: Odysseus also manages
// remote SSH servers, vLLM/SGLang serve engines, and raw HuggingFace/GGUF
// downloads with live benchmarked speed numbers, none of which apply to
// Arthur (single machine, Ollama-only, no serving step, no benchmark runner).
// "Score" here is honestly a fit calculation (core/model_recs.catalog_search),
// not a measured benchmark -- Arthur never runs a model just to time it.
//
// WHY search hits a curated catalog instead of live-scraping ollama.com:
// Ollama has no official search API, and a previous session already learned
// the hard way that a wrong model tag breaks onboarding (see model_recs.py's
// module docstring). A small hand-verified catalog beats an "any model"
// search that quietly returns a tag that doesn't actually exist.
import React, { useEffect, useState } from "react";
import { Check, Download, Loader2, RefreshCw, Search, ChevronUp, ChevronDown } from "lucide-react";
import { api } from "../../api/client";
import { pullModel } from "../../api/models";
import { useBackend } from "../../stores/backend";
import { useSettings } from "../../stores/settings";
import { useToasts } from "../../stores/toasts";

const MODE_LIST = [
  { id: "general", label: "General" },
  { id: "research", label: "Research" },
  { id: "code", label: "Code" },
  { id: "email", label: "Email" },
  { id: "finance", label: "Finance" },
  { id: "computer", label: "Computer" },
  { id: "design", label: "Design" },
];

const SORTERS = {
  model: (r) => r.model,
  params_b: (r) => r.params_b,
  size_gb: (r) => r.size_gb,
  score: (r) => r.score,
};

export default function ModelsTab() {
  const { status, refreshStatus } = useBackend();
  const { values, update } = useSettings();
  const pushToast = useToasts((s) => s.push);

  const [catalog, setCatalog] = useState(null); // {budget_gb, ollama_up, results}
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState("score");
  const [sortDir, setSortDir] = useState("desc");
  const [pulling, setPulling] = useState(null); // {model, pct}

  const loadCatalog = (q = query) => {
    api.get(`/models/catalog?q=${encodeURIComponent(q)}`).then(setCatalog).catch(() => {});
  };
  useEffect(() => { loadCatalog(""); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced search: re-query the backend (score is computed server-side,
  // against your real budget) 250ms after typing stops.
  useEffect(() => {
    const t = setTimeout(() => loadCatalog(query), 250);
    return () => clearTimeout(t);
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  const models = status?.models || [];
  const results = [...(catalog?.results || [])].sort((a, b) => {
    const dir = sortDir === "asc" ? 1 : -1;
    const av = SORTERS[sortKey](a), bv = SORTERS[sortKey](b);
    return av < bv ? -dir : av > bv ? dir : 0;
  });

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  const install = async (model) => {
    setPulling({ model, pct: 0 });
    try {
      await pullModel(model, (pct) => setPulling({ model, pct }));
      await refreshStatus();
      loadCatalog();
      update({ default_model: model });
      pushToast(`${model} installed and set as default.`, "success");
    } catch (e) {
      pushToast(e.message, "error");
    } finally {
      setPulling(null);
    }
  };

  const SortHead = ({ children, sortKeyName, style }) => (
    <th style={style}>
      <button className="data-table-sort" onClick={() => toggleSort(sortKeyName)}>
        {children}
        {sortKey === sortKeyName && (sortDir === "desc" ? <ChevronDown size={11} /> : <ChevronUp size={11} />)}
      </button>
    </th>
  );

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <h2 style={{ marginBottom: 0 }}>Cookbook</h2>
        <button className="btn" style={{ marginLeft: "auto", padding: "6px 12px", fontSize: 12 }} onClick={() => { refreshStatus(); loadCatalog(); }}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>
      <div className="section-sub">
        Search any model in Arthur's catalog. Score is how comfortably it fits THIS
        machine, not a speed benchmark, higher means faster and more headroom to spare.
      </div>

      {!status?.ollama_up && (
        <div className="card" style={{ borderColor: "rgba(252,165,165,0.4)" }}>
          <div className="card-title" style={{ color: "var(--red)" }}>Ollama is not running</div>
          <div className="card-sub">Start Ollama to install or use models.</div>
        </div>
      )}

      <div className="cookbook-search">
        <Search size={15} strokeWidth={1.8} color="var(--tmut)" />
        <input
          type="text" placeholder="Search by name, family, or purpose (e.g. code, reasoning, small)…"
          value={query} onChange={(e) => setQuery(e.target.value)}
        />
        {catalog && <span className="cookbook-budget">{catalog.budget_gb}GB budget</span>}
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th style={{ width: 20 }}></th>
            <SortHead sortKeyName="model">Model</SortHead>
            <SortHead sortKeyName="params_b" style={{ width: 80 }}>Params</SortHead>
            <SortHead sortKeyName="size_gb" style={{ width: 80 }}>Size</SortHead>
            <th style={{ width: 90 }}>Fit</th>
            <SortHead sortKeyName="score" style={{ width: 70 }}>Score</SortHead>
            <th style={{ width: 120 }}></th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => {
            const isPulling = pulling && pulling.model === r.model;
            const isDefault = values?.default_model === r.model;
            return (
              <tr key={r.model}>
                <td>
                  <span
                    title={r.installed ? "Installed" : r.fits ? "Fits this machine" : "Over budget, will be slow"}
                    className={`pill ${r.installed ? "ok" : r.fits ? "warn" : "off"}`}
                    style={{ width: 8, height: 8, borderRadius: "50%", padding: 0, display: "inline-block" }}
                  />
                </td>
                <td>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
                    {r.model}{isDefault && <Check size={13} color="var(--green)" />}
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--tmut)", marginTop: 2 }}>{r.desc}</div>
                </td>
                <td style={{ fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--tmut)" }}>{r.params_b}B</td>
                <td style={{ fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--tmut)" }}>{r.size_gb}GB</td>
                <td>
                  <span className={`pill ${r.installed ? "ok" : r.fits ? "warn" : "off"}`}>
                    {r.installed ? "installed" : r.fits ? "fits" : "over budget"}
                  </span>
                </td>
                <td style={{ fontFamily: "var(--mono)", fontWeight: 600, fontSize: 13 }}>{r.score}</td>
                <td>
                  {isDefault ? (
                    <span className="pill ok">default</span>
                  ) : r.installed ? (
                    <button className="btn" style={{ padding: "5px 11px", fontSize: 12 }} onClick={() => update({ default_model: r.model })}>Use</button>
                  ) : isPulling ? (
                    <span style={{ fontSize: 11.5, color: "var(--tmut)", fontFamily: "var(--mono)", display: "flex", alignItems: "center", gap: 5 }}>
                      <Loader2 size={12} className="spin" /> {pulling.pct}%
                    </span>
                  ) : (
                    <button
                      className="btn" style={{ padding: "5px 11px", fontSize: 12 }}
                      disabled={!!pulling || !status?.ollama_up}
                      title={r.fits ? "" : "This will run, but slowly, more than your budget"}
                      onClick={() => install(r.model)}
                    >
                      <Download size={11} /> Get
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
          {catalog && results.length === 0 && (
            <tr><td colSpan={7} style={{ color: "var(--tmut)", padding: "16px 8px" }}>No matches for "{query}".</td></tr>
          )}
          {!catalog && (
            <tr><td colSpan={7} style={{ color: "var(--tmut)", padding: "16px 8px" }}>Loading…</td></tr>
          )}
        </tbody>
      </table>

      <h2 style={{ fontSize: 15, margin: "26px 0 4px" }}>Installed on this machine</h2>
      <div className="section-sub" style={{ marginBottom: 10 }}>
        The global default, used by any mode without its own pick below.
      </div>
      {models.map((m) => (
        <div key={m.name} className="card card-row">
          <div className="grow">
            <div className="card-title" style={{ fontFamily: "var(--mono)" }}>{m.name}</div>
            <div className="card-sub">
              {m.parameter_size || "?"} · {(m.size_bytes / 1e9).toFixed(1)}GB on disk
            </div>
          </div>
          {values?.default_model === m.name
            ? <span className="pill ok">default</span>
            : <button className="btn" onClick={() => update({ default_model: m.name })}>Use</button>}
        </div>
      ))}
      {models.length === 0 && <div className="hint">No models installed yet, install one from the table above.</div>}

      <h2 style={{ fontSize: 15, margin: "26px 0 4px" }}>Model per mode</h2>
      <div className="section-sub" style={{ marginBottom: 10 }}>
        Use a stronger model where it matters. "Default" uses the global default above.
        The picker in the chat header overrides both for a single conversation.
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

      <div className="hint" style={{ marginTop: 10 }}>
        Install anything else with <code>ollama pull &lt;name&gt;</code>, then hit refresh.
      </div>
    </>
  );
}
