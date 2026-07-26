// Conversations sidebar. Mode selection lives in ModeRail.jsx; this panel is
// new chat, search (opens the command palette), folders (local-only, see
// stores/organize.js), pinned/recents, drag-to-file chats into folders, and
// a right-click menu (rename/pin/clone/archive/delete) on every row.
import React, { useState } from "react";
import {
  SquarePen, Search, FolderPlus, ChevronRight, Folder as FolderIcon,
  Pin, PinOff, Trash2, PanelLeftClose, PanelLeftOpen, Pencil, Copy, Archive,
} from "lucide-react";
import { useConversations } from "../stores/conversations";
import { useOrganize } from "../stores/organize";
import { useBackend } from "../stores/backend";
import { useSettings } from "../stores/settings";
import ContextMenu from "./ContextMenu";

const MIN_WIDTH = 200;
const MAX_WIDTH = 420;

export default function Sidebar({ view, mode, setView, onOpenPalette }) {
  const { list, activeId, select, createNew, remove, rename, archive, clone } = useConversations();
  const {
    pinned, folders, convFolder, openFolders,
    togglePin, addFolder, toggleFolder, renameFolder, deleteFolder, setConvFolder,
  } = useOrganize();
  const { status } = useBackend();
  const settings = useSettings((s) => s.values);
  const [collapsed, setCollapsed] = useState(false);
  const [width, setWidth] = useState(260);
  const [menu, setMenu] = useState(null); // {type:'chat'|'folder', id, x, y}
  const [renamingId, setRenamingId] = useState(null); // conv id being renamed inline
  const [renamingFolderId, setRenamingFolderId] = useState(null);
  const [draft, setDraft] = useState("");
  const [dragOverFolder, setDragOverFolder] = useState(null);
  const [draggingId, setDraggingId] = useState(null);

  const open = (id) => { select(id); setView("chat"); };

  const startResize = (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = width;
    const onMove = (ev) => {
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + (ev.clientX - startX)));
      setWidth(next);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const commitRename = (id) => {
    if (draft.trim()) rename(id, draft);
    setRenamingId(null);
  };
  const commitFolderRename = (id) => {
    if (draft.trim()) renameFolder(id, draft);
    setRenamingFolderId(null);
  };

  if (collapsed) {
    return (
      <div className="sidebar-collapsed">
        <button className="rail-btn" title="Expand sidebar" onClick={() => setCollapsed(false)}>
          <PanelLeftOpen size={17} strokeWidth={1.7} />
        </button>
        <button className="rail-btn" title="Search (Ctrl+K)" onClick={onOpenPalette}>
          <Search size={16} strokeWidth={1.8} />
        </button>
      </div>
    );
  }

  const pinnedConvs = list.filter((c) => pinned.includes(c.id));
  const unpinned = list.filter((c) => !pinned.includes(c.id));
  const recents = unpinned.filter((c) => !convFolder[c.id]);
  const inFolder = (folderId) => unpinned.filter((c) => convFolder[c.id] === folderId);

  const modeLabel = mode.charAt(0).toUpperCase() + mode.slice(1);

  const chatMenuItems = (c) => [
    { label: "Rename", icon: Pencil, onClick: () => { setRenamingId(c.id); setDraft(c.title); } },
    {
      label: pinned.includes(c.id) ? "Unpin" : "Pin", icon: pinned.includes(c.id) ? PinOff : Pin,
      onClick: () => togglePin(c.id),
    },
    { label: "Clone", icon: Copy, onClick: () => clone(c.id) },
    { label: "Archive", icon: Archive, onClick: () => archive(c.id) },
    { divider: true },
    { label: "Delete", icon: Trash2, danger: true, onClick: () => remove(c.id) },
  ];
  const folderMenuItems = (f) => [
    { label: "Rename folder", icon: Pencil, onClick: () => { setRenamingFolderId(f.id); setDraft(f.name); } },
    { label: "Delete folder", icon: Trash2, danger: true, onClick: () => deleteFolder(f.id) },
  ];

  const row = (c) => {
    const isRenaming = renamingId === c.id;
    return (
      <div
        key={c.id}
        className={`conv-item ${c.id === activeId && view === "chat" ? "active" : ""} ${draggingId === c.id ? "dragging" : ""}`}
        draggable={!isRenaming}
        onDragStart={(e) => { setDraggingId(c.id); e.dataTransfer.setData("text/arthur-conv-id", c.id); }}
        onDragEnd={() => setDraggingId(null)}
        onClick={() => !isRenaming && open(c.id)}
        onContextMenu={(e) => { e.preventDefault(); setMenu({ type: "chat", id: c.id, x: e.clientX, y: e.clientY }); }}
      >
        {isRenaming ? (
          <input
            autoFocus className="conv-rename-input" value={draft}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => commitRename(c.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename(c.id);
              if (e.key === "Escape") setRenamingId(null);
            }}
          />
        ) : (
          <span className="conv-title">{c.title}</span>
        )}
        <button
          className="conv-action"
          title={pinned.includes(c.id) ? "Unpin" : "Pin"}
          onClick={(e) => { e.stopPropagation(); togglePin(c.id); }}
        >
          {pinned.includes(c.id) ? <PinOff size={13} /> : <Pin size={13} />}
        </button>
        <button
          className="conv-action danger"
          title="Delete conversation"
          onClick={(e) => { e.stopPropagation(); remove(c.id); }}
        >
          <Trash2 size={13} />
        </button>
      </div>
    );
  };

  return (
    <div className="sidebar" style={{ width }}>
      <div className="sidebar-header">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="sidebar-title">Arthur</div>
          <div className="sidebar-mode-label">{modeLabel} mode</div>
        </div>
        <button className="rail-btn" style={{ width: 28, height: 28 }} title="Collapse sidebar" onClick={() => setCollapsed(true)}>
          <PanelLeftClose size={16} strokeWidth={1.7} />
        </button>
      </div>

      <div className="sidebar-actions">
        <button className="sidebar-action-btn primary" onClick={() => { createNew(); setView("chat"); }}>
          <SquarePen size={16} /> New chat<span className="sidebar-action-shortcut">Ctrl+N</span>
        </button>
        <button className="sidebar-action-btn" onClick={onOpenPalette}>
          <Search size={16} /> Search<span className="sidebar-action-shortcut">Ctrl+K</span>
        </button>
        <button className="sidebar-action-btn" onClick={() => { const id = addFolder("New folder"); setRenamingFolderId(id); setDraft("New folder"); }}>
          <FolderPlus size={16} /> New folder
        </button>
      </div>

      <div className="conv-list">
        {pinnedConvs.length > 0 && (
          <>
            <div className="conv-section-label"><Pin size={12} /> Pinned</div>
            {pinnedConvs.map((c) => row(c))}
          </>
        )}

        {folders.length > 0 && <div className="conv-section-label">Folders</div>}
        {folders.map((f) => {
          const chats = inFolder(f.id);
          const isOpen = !!openFolders[f.id];
          const isRenamingFolder = renamingFolderId === f.id;
          return (
            <div key={f.id}>
              <div
                className={`folder-row ${dragOverFolder === f.id ? "drop-target" : ""}`}
                onClick={() => !isRenamingFolder && toggleFolder(f.id)}
                onContextMenu={(e) => { e.preventDefault(); setMenu({ type: "folder", id: f.id, x: e.clientX, y: e.clientY }); }}
                onDragOver={(e) => { e.preventDefault(); setDragOverFolder(f.id); }}
                onDragLeave={() => setDragOverFolder((cur) => (cur === f.id ? null : cur))}
                onDrop={(e) => {
                  e.preventDefault();
                  const convId = e.dataTransfer.getData("text/arthur-conv-id");
                  if (convId) setConvFolder(convId, f.id);
                  setDragOverFolder(null);
                }}
              >
                <ChevronRight size={13} className={`folder-caret ${isOpen ? "open" : ""}`} />
                <FolderIcon size={15} strokeWidth={1.7} />
                {isRenamingFolder ? (
                  <input
                    autoFocus className="conv-rename-input" value={draft}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => commitFolderRename(f.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitFolderRename(f.id);
                      if (e.key === "Escape") setRenamingFolderId(null);
                    }}
                  />
                ) : (
                  <span className="folder-name">{f.name}</span>
                )}
                <span className="folder-count">{chats.length}</span>
              </div>
              {isOpen && (
                <div className="folder-children">
                  {chats.map((c) => row(c))}
                  {chats.length === 0 && (
                    <div style={{ padding: "6px 10px", color: "var(--tmut)", fontSize: 12 }}>
                      Empty, drag a chat here
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}

        <div
          className="conv-section-label"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const convId = e.dataTransfer.getData("text/arthur-conv-id");
            if (convId) setConvFolder(convId, null);
          }}
        >
          Recents
        </div>
        {recents.map((c) => row(c))}
        {list.length === 0 && (
          <div style={{ padding: "14px 10px", color: "var(--tmut)", fontSize: 12.5 }}>
            No conversations yet.
          </div>
        )}
      </div>

      {/* Footer answers two questions at a glance: is the runtime alive (dot),
          and which model am I actually talking to (sub). The mockup shows the
          model name rather than the Ollama version -- the version is trivia,
          the model is the thing you change several times a day. */}
      <div className="sidebar-footer">
        <span className={`status-dot ${status?.ollama_up ? "" : "off"}`} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="sidebar-footer-name">Ollama</div>
          <div className="sidebar-footer-sub" title={settings?.default_model || ""}>
            {status?.ollama_up
              ? (settings?.default_model || "no model selected")
              : "not running"}
          </div>
        </div>
      </div>
      <div className="sidebar-resize-handle" onMouseDown={startResize} title="Drag to resize" />

      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y}
          items={menu.type === "chat"
            ? chatMenuItems(list.find((c) => c.id === menu.id) || { id: menu.id, title: "" })
            : folderMenuItems(folders.find((f) => f.id === menu.id) || { id: menu.id, name: "" })}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  );
}
