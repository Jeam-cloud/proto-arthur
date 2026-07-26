// Command palette (Ctrl/Cmd+K) -- new in this redesign, per the mockup.
// Searches conversations by title and offers quick jumps into each Settings
// tab. Kept deliberately dumb: no fuzzy-match library, just a case-insensitive
// substring test, since the result sets here are small (dozens of chats, a
// handful of tabs) and a real fuzzy matcher would be overkill.
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  MessageSquare, Sliders, Cpu, ChefHat, User, Brain, Shield, Plug,
} from "lucide-react";
import { useConversations } from "../stores/conversations";

const SETTINGS_ITEMS = [
  { id: "general", label: "General settings", icon: Sliders },
  { id: "system", label: "This computer", icon: Cpu },
  { id: "models", label: "Cookbook", icon: ChefHat },
  { id: "personas", label: "Personas", icon: User },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "security", label: "Security", icon: Shield },
  { id: "integrations", label: "Integrations", icon: Plug },
];

export default function CommandPalette({ onClose, onOpenConversation, onOpenSettingsTab }) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef(null);
  const { list } = useConversations();

  useEffect(() => { inputRef.current?.focus(); }, []);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const chats = list
      .filter((c) => !q || c.title.toLowerCase().includes(q))
      .slice(0, 8)
      .map((c) => ({ key: `chat-${c.id}`, title: c.title, sub: "Chat", icon: MessageSquare, onClick: () => onOpenConversation(c.id) }));
    const settings = SETTINGS_ITEMS
      .filter((s) => !q || s.label.toLowerCase().includes(q))
      .map((s) => ({ key: `set-${s.id}`, title: s.label, sub: "Settings", icon: s.icon, onClick: () => onOpenSettingsTab(s.id) }));

    const out = [];
    if (chats.length) out.push({ label: "Chats", items: chats });
    if (settings.length) out.push({ label: "Settings", items: settings });
    return out;
  }, [query, list, onOpenConversation, onOpenSettingsTab]);

  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(i + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); flat[index]?.onClick(); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <div className="palette-input-row">
          <MessageSquare size={18} strokeWidth={1.8} color="var(--tmut)" />
          <input
            ref={inputRef}
            type="text"
            placeholder="Search chats, models, settings..."
            value={query}
            onChange={(e) => { setQuery(e.target.value); setIndex(0); }}
            onKeyDown={onKeyDown}
          />
        </div>
        <div className="palette-results">
          {groups.map((g) => (
            <div key={g.label}>
              <div className="palette-group-label">{g.label}</div>
              {g.items.map((it) => {
                const Icon = it.icon;
                const selected = flat[index]?.key === it.key;
                return (
                  <div
                    key={it.key}
                    className={`palette-item ${selected ? "selected" : ""}`}
                    onClick={it.onClick}
                    onMouseEnter={() => setIndex(flat.findIndex((f) => f.key === it.key))}
                  >
                    <Icon size={17} strokeWidth={1.7} color="var(--tmut)" />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="palette-item-title">{it.title}</div>
                      <div className="palette-item-sub">{it.sub}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
          {flat.length === 0 && <div className="palette-empty">No matches for "{query}"</div>}
        </div>
        <div className="palette-footer">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><kbd>↑</kbd><kbd>↓</kbd> Navigate</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><kbd>↵</kbd> Open</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><kbd>esc</kbd> Close</span>
        </div>
      </div>
    </div>
  );
}
