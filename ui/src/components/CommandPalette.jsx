// Command palette (Ctrl/Cmd+K) -- new in this redesign, per the mockup.
// Searches conversations by title and offers quick jumps into each Settings
// tab. Kept deliberately dumb: no fuzzy-match library, just a case-insensitive
// substring test, since the result sets here are small (dozens of chats, a
// handful of tabs) and a real fuzzy matcher would be overkill.
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  MessageSquare, Sliders, Cpu, Box, Library, User, Brain, Shield, Plug, Plus, Search,
} from "lucide-react";
import { useConversations } from "../stores/conversations";
import { MODES } from "./ModeRail";

const SETTINGS_ITEMS = [
  { id: "general", label: "General", icon: Sliders },
  { id: "system", label: "This computer", icon: Cpu },
  { id: "models", label: "Models", icon: Box },
  { id: "personas", label: "Personas", icon: User },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "security", label: "Security", icon: Shield },
  { id: "integrations", label: "Integrations", icon: Plug },
];

export default function CommandPalette({
  onClose, onOpenConversation, onOpenSettingsTab, onOpenHub, onSetMode, onNewChat,
}) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef(null);
  const { list } = useConversations();

  useEffect(() => { inputRef.current?.focus(); }, []);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const match = (title, sub) =>
      !q || title.toLowerCase().includes(q) || (sub || "").toLowerCase().includes(q);

    // "Go to" first: with an empty query the palette should read as a launcher
    // (what can I do?) rather than a chat list, which is what it looked like
    // before this group existed.
    const goTo = [
      { key: "go-new", title: "New chat", sub: "Ctrl+N", icon: Plus, onClick: onNewChat },
      { key: "go-hub", title: "Model hub", sub: "Find and download models", icon: Library, onClick: onOpenHub },
      { key: "go-models", title: "Models", sub: "Your installed models", icon: Box, onClick: () => onOpenSettingsTab("models") },
      { key: "go-system", title: "This computer", sub: "Your PC specs and model fit", icon: Cpu, onClick: () => onOpenSettingsTab("system") },
    ].filter((it) => match(it.title, it.sub));

    const settings = SETTINGS_ITEMS
      .filter((s) => match(s.label, "Settings"))
      .map((s) => ({ key: `set-${s.id}`, title: s.label, sub: "Settings", icon: s.icon, onClick: () => onOpenSettingsTab(s.id) }));

    const modes = MODES
      .filter((m) => match(m.label, "Mode"))
      .map((m) => ({ key: `mode-${m.id}`, title: m.label, sub: "Mode", icon: m.icon, onClick: () => onSetMode(m.id) }));

    const chats = list
      .filter((c) => match(c.title, "Conversation"))
      .slice(0, 8)
      .map((c) => ({ key: `chat-${c.id}`, title: c.title, sub: "Conversation", icon: MessageSquare, onClick: () => onOpenConversation(c.id) }));

    const out = [];
    if (goTo.length) out.push({ label: "Go to", items: goTo });
    if (settings.length) out.push({ label: "Settings", items: settings });
    if (modes.length) out.push({ label: "Switch mode", items: modes });
    if (chats.length) out.push({ label: "Chats", items: chats });
    return out;
  }, [query, list, onOpenConversation, onOpenSettingsTab, onOpenHub, onSetMode, onNewChat]);

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
          <Search size={18} strokeWidth={1.8} color="var(--tmut)" />
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
