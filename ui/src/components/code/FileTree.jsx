// The workspace, visible — and decorated.
//
// ONE tree, the whole project, changes marked IN PLACE. The rejected
// alternative was a "staged" list above an "untouched" list, which sounds
// tidier and is worse: a file leaves the tree the moment Arthur edits it, so
// the tree stops being the project and becomes "the project minus the
// interesting parts". You lose the one thing a tree is for — seeing a change
// next to the files it sits beside. VS Code decorates in place for the same
// reason, and this follows it.
//
// Colour is doubled by a letter badge (A/M/D). Roughly 1 in 12 men can't
// separate red from green reliably, and "created" vs "deleted" are the two
// states where confusing them costs most.
import React from "react";
import { ChevronRight, ChevronsDownUp, File, Folder } from "lucide-react";
import { useWorkspace } from "../../stores/workspace";
import { useChanges } from "../../stores/changes";

const DEC = {
  create: { cls: "add", badge: "A" },
  modify: { cls: "mod", badge: "M" },
  delete: { cls: "del", badge: "D" },
};

export default function FileTree({ onPick }) {
  const { tree, root, truncated, expanded, toggleDir, collapseAll, treeOpen, toggleTree } =
    useWorkspace();
  const changes = useChanges((s) => s.changes);
  const jumpTo = useChanges((s) => s.jumpTo);

  if (!root) return null;

  const byPath = {};
  for (const c of changes) byPath[c.path] = c;

  if (!treeOpen) {
    return (
      <div className="filetree collapsed">
        <button className="icon-btn" title="Show files" onClick={toggleTree}>
          <ChevronRight size={15} strokeWidth={1.9} style={{ transform: "rotate(180deg)" }} />
        </button>
        {changes.length > 0 && <span className="tree-count">{changes.length}</span>}
      </div>
    );
  }

  return (
    <div className="filetree">
      <div className="filetree-head">
        <span className="filetree-title">Files</span>
        {changes.length > 0 && <span className="tree-count">{changes.length}</span>}
        <button className="icon-btn" title="Collapse all folders" onClick={collapseAll}>
          <ChevronsDownUp size={14} strokeWidth={1.9} />
        </button>
        <button className="icon-btn" title="Hide panel" onClick={toggleTree}>
          <ChevronRight size={14} strokeWidth={1.9} />
        </button>
      </div>

      <div className="filetree-body">
        {tree.length === 0 ? (
          <div className="filetree-empty">This folder is empty, or everything in it is hidden.</div>
        ) : (
          <Nodes
            nodes={tree} depth={0} expanded={expanded} toggleDir={toggleDir}
            byPath={byPath} jumpTo={jumpTo} onPick={onPick}
          />
        )}
        {/* Said out loud rather than silently showing a partial tree. Pointing
            Arthur at a home directory by mistake is easy, and a tree that
            quietly stops is indistinguishable from a small project. */}
        {truncated && (
          <div className="filetree-more">…more files not shown — this folder is very large</div>
        )}
      </div>
    </div>
  );
}

// Does anything under this folder have a pending change? Used to tint a
// COLLAPSED folder, so folding `ui/` away never hides the fact that four of the
// five edits are inside it.
function hasChangeUnder(prefix, byPath) {
  const p = prefix.endsWith("/") ? prefix : prefix + "/";
  return Object.keys(byPath).some((k) => k.startsWith(p));
}

function Nodes({ nodes, depth, expanded, toggleDir, byPath, jumpTo, onPick }) {
  return nodes.map((node) => {
    const open = !!expanded[node.path];
    // Indent guides: faint verticals, one per level. Four levels of plain
    // whitespace is where an indented list stops reading as a tree.
    const guides = Array.from({ length: depth }, (_, i) => (
      <span key={i} className="tree-guide" style={{ left: 10 + i * 11 }} />
    ));
    const pad = { paddingLeft: 6 + depth * 11 };

    if (node.dir) {
      const changed = hasChangeUnder(node.path, byPath);
      return (
        <div key={node.path}>
          <button
            className={`tree-row dir${changed ? " under" : ""}`} style={pad}
            onClick={() => toggleDir(node.path)} title={node.path}
          >
            {guides}
            <ChevronRight size={12} strokeWidth={2.1} className={`chev${open ? " open" : ""}`} />
            <Folder size={13} strokeWidth={1.7} className="tree-icon" />
            <span className="tree-name">{node.name}</span>
          </button>
          {open && (
            <Nodes
              nodes={node.children || []} depth={depth + 1} expanded={expanded}
              toggleDir={toggleDir} byPath={byPath} jumpTo={jumpTo} onPick={onPick}
            />
          )}
        </div>
      );
    }

    const ch = byPath[node.path];
    const dec = ch ? DEC[ch.kind] : null;
    return (
      <button
        key={node.path}
        className={`tree-row file${dec ? " " + dec.cls : ""}${ch?.conflict ? " conflict" : ""}`}
        style={pad}
        title={ch ? `${node.path} — click to see the diff` : node.path}
        // A changed file jumps to its diff; an untouched one falls back to the
        // old behaviour of referencing it in the message. Secondary now that
        // Arthur finds files by search, but harmless and occasionally handy.
        onClick={() => (ch ? jumpTo(node.path) : onPick && onPick(node.path))}
      >
        {guides}
        <span className="chev-space" />
        <File size={13} strokeWidth={1.7} className="tree-icon" />
        <span className="tree-name">{node.name}</span>
        {ch && (
          <>
            <span className="tree-stat">
              {ch.kind === "delete" ? `−${ch.deletions}` : `+${ch.additions}`}
            </span>
            <span className="tree-badge">{dec.badge}</span>
          </>
        )}
      </button>
    );
  });
}
