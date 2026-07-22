// Persona manager (tracker p2t15/16): system prompt + few-shot examples,
// multiple saved personalities, one active.
import React, { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { useToasts } from "../../stores/toasts";

export default function PersonasTab() {
  const [personas, setPersonas] = useState([]);
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState(null);
  const pushToast = useToasts((s) => s.push);

  const load = async () => {
    const list = await api.get("/personas");
    setPersonas(list);
    if (!selected && list.length) select(list.find((p) => p.is_active) || list[0]);
  };
  useEffect(() => { load(); }, []);

  const select = (p) => {
    setSelected(p.id);
    setDraft({ name: p.name, system_prompt: p.system_prompt, few_shots: p.few_shots, builtin: !!p.builtin, is_active: !!p.is_active });
  };

  const save = async () => {
    try {
      const body = { name: draft.name, system_prompt: draft.system_prompt, few_shots: draft.few_shots };
      await api.put(`/personas/${selected}`, body);
      pushToast("Persona saved.", "success");
      load();
    } catch (e) { pushToast(e.message, "error"); }
  };

  const create = async () => {
    const p = await api.post("/personas", {
      name: "New persona",
      system_prompt: "You are Arthur, a helpful local assistant.",
      few_shots: [],
    });
    await load();
    select({ ...p, builtin: 0, is_active: 0, few_shots: [] });
  };

  const activate = async () => {
    await api.post(`/personas/${selected}/activate`);
    pushToast("Persona activated.", "success");
    load();
  };

  const remove = async () => {
    await api.del(`/personas/${selected}`);
    setSelected(null); setDraft(null);
    load();
  };

  return (
    <>
      <h2>Personas</h2>
      <div className="section-sub">
        The system prompt shapes tone and behavior; example exchanges (few-shots) steer small
        local models far more reliably than instructions alone.
      </div>

      <div className="field-row" style={{ marginBottom: 14, flexWrap: "wrap" }}>
        {personas.map((p) => (
          <button
            key={p.id}
            className={`mode-chip ${selected === p.id ? "active" : ""}`}
            onClick={() => select(p)}
          >
            {p.name}{p.is_active ? " ●" : ""}
          </button>
        ))}
        <button className="mode-chip" onClick={create}><Plus size={11} /> New</button>
      </div>

      {draft && (
        <>
          <div className="field">
            <label>Name</label>
            <input type="text" value={draft.name} disabled={draft.builtin}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </div>
          <div className="field">
            <label>System prompt</label>
            <textarea value={draft.system_prompt}
              onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })} />
          </div>

          <div className="field">
            <label>Few-shot examples</label>
            {draft.few_shots.map((shot, i) => (
              <div key={i} className="card">
                <div className="field" style={{ marginBottom: 8 }}>
                  <label>User says</label>
                  <input type="text" value={shot.user || ""}
                    onChange={(e) => updateShot(i, "user", e.target.value)} />
                </div>
                <div className="field" style={{ marginBottom: 8 }}>
                  <label>Arthur replies</label>
                  <input type="text" value={shot.assistant || ""}
                    onChange={(e) => updateShot(i, "assistant", e.target.value)} />
                </div>
                <button className="btn danger" onClick={() => setDraft({ ...draft, few_shots: draft.few_shots.filter((_, j) => j !== i) })}>
                  Remove example
                </button>
              </div>
            ))}
            {draft.few_shots.length < 8 && (
              <button className="btn" onClick={() => setDraft({ ...draft, few_shots: [...draft.few_shots, { user: "", assistant: "" }] })}>
                <Plus size={13} /> Add example
              </button>
            )}
          </div>

          <div className="modal-actions" style={{ justifyContent: "flex-start" }}>
            <button className="btn primary" onClick={save}>Save</button>
            {!draft.is_active && <button className="btn" onClick={activate}>Make active</button>}
            {!draft.builtin && <button className="btn danger" onClick={remove}><Trash2 size={13} /> Delete</button>}
          </div>
        </>
      )}
    </>
  );

  function updateShot(i, key, value) {
    const shots = draft.few_shots.map((s, j) => (j === i ? { ...s, [key]: value } : s));
    setDraft({ ...draft, few_shots: shots });
  }
}
