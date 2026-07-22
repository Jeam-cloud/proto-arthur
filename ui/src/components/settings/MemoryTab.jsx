// The transparency promise made real (tracker p2t14): everything Arthur
// remembers, listed, editable, deletable. Nothing hidden.
import React, { useEffect, useState } from "react";
import { Pencil, Trash2, Check, X, Plus } from "lucide-react";
import { api } from "../../api/client";
import { useToasts } from "../../stores/toasts";

export default function MemoryTab() {
  const [memories, setMemories] = useState(null);
  const [editing, setEditing] = useState(null); // id
  const [editText, setEditText] = useState("");
  const [newText, setNewText] = useState("");
  const pushToast = useToasts((s) => s.push);

  const load = () => api.get("/memory").then(setMemories).catch((e) => pushToast(e.message, "error"));
  useEffect(() => { load(); }, []);

  const saveEdit = async (id) => {
    try {
      await api.patch(`/memory/${id}`, { text: editText });
      setEditing(null);
      load();
    } catch (e) { pushToast(e.message, "error"); }
  };

  const toggle = async (m) => {
    await api.patch(`/memory/${m.id}`, { enabled: !m.enabled });
    load();
  };

  const remove = async (id) => {
    await api.del(`/memory/${id}`);
    load();
  };

  const add = async () => {
    if (newText.trim().length < 3) return;
    try {
      await api.post("/memory", { text: newText.trim() });
      setNewText("");
      load();
    } catch (e) { pushToast(e.message, "error"); }
  };

  return (
    <>
      <h2>Memory</h2>
      <div className="section-sub">
        Facts Arthur has learned about you. He recalls the relevant ones automatically.
        Toggle one off to keep it without using it; delete removes it permanently.
      </div>

      <div className="field-row" style={{ marginBottom: 16 }}>
        <input
          type="text" className="grow" placeholder="Teach Arthur something… (e.g. 'I go by Ri')"
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          style={{ background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: 8, padding: "9px 12px", fontSize: 13, outline: "none" }}
        />
        <button className="btn" onClick={add}><Plus size={13} /> Add</button>
      </div>

      {memories === null && <div className="hint">Loading…</div>}
      {memories && memories.length === 0 && (
        <div className="empty-state" style={{ flex: "none", padding: "30px 0" }}>
          <h3>Nothing remembered yet</h3>
          <p>As you chat, Arthur saves useful facts here — always visible, never hidden.</p>
        </div>
      )}

      {memories && memories.map((m) => (
        <div key={m.id} className="card card-row" style={{ opacity: m.enabled ? 1 : 0.5 }}>
          <div className="grow">
            {editing === m.id ? (
              <input
                type="text" value={editText} autoFocus
                onChange={(e) => setEditText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && saveEdit(m.id)}
                style={{ width: "100%", background: "var(--surface3)", border: "1px solid var(--accent)", borderRadius: 6, padding: "6px 9px", fontSize: 13, outline: "none" }}
              />
            ) : (
              <>
                <div style={{ fontSize: 13 }}>{m.text}</div>
                <div className="card-sub"><span className="pill cat">{m.category}</span></div>
              </>
            )}
          </div>
          {editing === m.id ? (
            <>
              <button className="icon-btn" onClick={() => saveEdit(m.id)}><Check size={14} /></button>
              <button className="icon-btn" onClick={() => setEditing(null)}><X size={14} /></button>
            </>
          ) : (
            <>
              <label className="switch" title={m.enabled ? "In use" : "Paused"}>
                <input type="checkbox" checked={!!m.enabled} onChange={() => toggle(m)} />
                <span className="track" /><span className="thumb" />
              </label>
              <button className="icon-btn" title="Edit" onClick={() => { setEditing(m.id); setEditText(m.text); }}>
                <Pencil size={14} />
              </button>
              <button className="icon-btn" title="Delete" onClick={() => remove(m.id)}>
                <Trash2 size={14} />
              </button>
            </>
          )}
        </div>
      ))}
    </>
  );
}
