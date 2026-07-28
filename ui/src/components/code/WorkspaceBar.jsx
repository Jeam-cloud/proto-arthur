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
import React, { useEffect } from "react";
import { AlertTriangle, Folder, FolderOpen } from "lucide-react";
import { useWorkspace } from "../../stores/workspace";

export default function WorkspaceBar({ conversationId }) {
  const { root, bound, exists, loading, load, choose } = useWorkspace();

  useEffect(() => { load(conversationId); }, [conversationId, load]);

  if (loading && !root) return null;

  // Nothing chosen anywhere yet. This is the first-run state, and it is the
  // one that used to be a silent dead end.
  if (!root) {
    return (
      <div className="ws-bar empty">
        <Folder size={14} strokeWidth={1.9} />
        <span className="ws-bar-text">
          No folder yet. Code mode can only read and write inside one folder you choose.
        </span>
        <button className="btn tiny primary" onClick={choose}>Choose folder</button>
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
        <button className="btn tiny" onClick={choose}>Choose another</button>
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
      <button className="btn tiny" onClick={choose}>Change</button>
    </div>
  );
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
