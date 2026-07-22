import React from "react";
import { MessageSquarePlus, Settings, Trash2 } from "lucide-react";
import { useConversations } from "../stores/conversations";

export default function Sidebar({ view, setView }) {
  const { list, activeId, select, createNew, remove } = useConversations();

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <div className="logo">A</div>
        <div>
          <div className="app-name">ARTHUR</div>
          <div className="app-sub">local assistant</div>
        </div>
      </div>

      <button className="new-chat-btn" onClick={() => { createNew(); setView("chat"); }}>
        <MessageSquarePlus size={15} /> New chat
      </button>

      <div className="conv-list">
        {list.map((c) => (
          <div
            key={c.id}
            className={`conv-item ${c.id === activeId && view === "chat" ? "active" : ""}`}
            onClick={() => { select(c.id); setView("chat"); }}
          >
            <span className="conv-title">{c.title}</span>
            <button
              className="conv-del"
              title="Delete conversation"
              onClick={(e) => { e.stopPropagation(); remove(c.id); }}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        {list.length === 0 && (
          <div style={{ padding: "14px 10px", color: "var(--dim)", fontSize: 12.5 }}>
            No conversations yet.
          </div>
        )}
      </div>

      <div className="sidebar-footer">
        <button
          className={`nav-btn ${view === "settings" ? "active" : ""}`}
          onClick={() => setView("settings")}
        >
          <Settings size={15} /> Settings
        </button>
      </div>
    </div>
  );
}
