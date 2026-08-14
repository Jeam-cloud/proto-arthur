// The folder bar: which files this chat can touch, stated where the work
// happens.
//
// WHY it lives here and not in Settings. The folder used to be one app-wide
// setting, the second field on the General tab, below "Text size" -- so the
// scope of everything Code mode can do was configured on a screen you visit
// once and forget. Worse, the only time you learned it mattered was when a
// tool call failed, and the error pointed at "Settings → Workspace", a tab
// that does not exist.
//
// A permission boundary should be visible from inside the thing it bounds.
//
// CHANGE IS A MENU, NOT A DIALOG. Switching projects used to mean the OS folder
// picker every single time, for every chat — which is the friction that kept
// everyone on one folder and made multi-project work not worth attempting. The
// recents list turns it into one click; "Browse…" is still there for a folder
// Arthur has not seen before.
import React, { useEffect, useState } from "react";
import { AlertTriangle, Check, Folder, FolderOpen } from "lucide-react";
import { useWorkspace } from "../../stores/workspace";

export default function WorkspaceBar({ conversationId }) {
  const { root, bound, exists, loading, load, choose, pick, recents, loadRecents } = useWorkspace();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => { load(conversationId); }, [conversationId, load]);
  useEffect(() => { loadRecents(); }, [loadRecents]);

  // Click-away. Registered only while the menu is open, so the app is not
  // running a document listener for a menu nobody has opened.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const away = (e) => { if (!e.target.closest?.("[data-ws-menu]")) setMenuOpen(false); };
    document.addEventListener("mousedown", away, true);
    return () => document.removeEventListener("mousedown", away, true);
  }, [menuOpen]);

  if (loading && !root) return null;

  const others = recents.filter((r) => r.root !== root);

  const menu = (
    <div className="ws-menu" data-ws-menu>
      {/* "Change folder", not "Change". On its own the verb sits next to a path
          and a mode badge and does not say what it acts on. */}
      <button className="btn tiny" onClick={() => setMenuOpen((v) => !v)}>
        {root ? "Change folder" : "Choose folder"}
      </button>
      {menuOpen && (
        <div className="ws-menu-pop">
          {others.length > 0 && <div className="ws-menu-head">Recent projects</div>}
          {others.map((r) => (
            <button
              key={r.root}
              className={`ws-menu-item${r.exists ? "" : " gone"}`}
              // A folder that has moved stays listed but is not offered: it is
              // still part of the user's history, and silently dropping it
              // would look like the app forgot the project.
              disabled={!r.exists}
              title={r.exists ? r.root : `${r.root} — not found`}
              onClick={() => { pick(r.root); setMenuOpen(false); }}
            >
              <span className="ws-menu-name">{lastSegment(r.root)}</span>
              <span className="ws-menu-path">{r.exists ? shorten(r.root, 34) : "not found"}</span>
            </button>
          ))}
          <button
            className="ws-menu-item browse"
            onClick={() => { setMenuOpen(false); choose(); }}
          >
            Browse…
          </button>
        </div>
      )}
    </div>
  );

  // Nothing chosen anywhere yet. This is the first-run state, and it is the
  // one that used to be a silent dead end.
  if (!root) {
    return (
      <div className="ws-bar empty">
        <Folder size={14} strokeWidth={1.9} />
        <span className="ws-bar-text">
          No folder yet. This chat can only read and write inside one folder you choose.
        </span>
        {menu}
      </div>
    );
  }

  // A remembered path that is not currently there -- an unplugged drive, a
  // renamed directory. Said plainly rather than failing later on a tool call.
  if (!exists) {
    return (
      <div className="ws-bar missing">
        <AlertTriangle size={14} strokeWidth={1.9} />
        <span className="ws-bar-text">
          Can't find <code>{root}</code> — it may have been moved, renamed, or be on a drive
          that isn't connected.
        </span>
        {menu}
      </div>
    );
  }

  return (
    <div className="ws-bar">
      <FolderOpen size={14} strokeWidth={1.9} />
      <span className="ws-bar-text" title={root}>
        <code>{shorten(root)}</code>
        {/* "Inherited" is worth saying: it tells you this chat is following
            the last folder you picked rather than one chosen for it, so
            changing it here will not affect the chat you set it in. */}
        {!bound && <span className="ws-bar-note">inherited</span>}
      </span>
      {bound && <Check size={13} strokeWidth={2.2} className="ws-bound" />}
      {menu}
    </div>
  );
}

function lastSegment(path) {
  return path.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
}

// Keeps the tail, which is the part that identifies the project. Truncating
// the end would leave every path reading "C:\Users\rian\Documents\…".
function shorten(path, max = 52) {
  if (path.length <= max) return path;
  const sep = path.includes("\\") ? "\\" : "/";
  const parts = path.split(sep);
  const out = [];
  let len = 0;
  for (let i = parts.length - 1; i >= 0; i--) {
    if (len + parts[i].length + 1 > max) break;
    out.unshift(parts[i]);
    len += parts[i].length + 1;
  }
  return `…${sep}${out.join(sep)}`;
}
