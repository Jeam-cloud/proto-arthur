// The workspace, visible.
//
// Before this the folder was a path string in Settings and nothing else, so
// there was no way to confirm Arthur was pointed at the right place, and no
// way to reference a file without typing its path from memory. Clicking a file
// inserts its path into the composer, which is the whole point -- it turns
// "what was that file called" into a click.
import React from "react";
import { ChevronRight, File, Folder } from "lucide-react";
import { useWorkspace } from "../../stores/workspace";

export default function FileTree({ onPick }) {
  const tree = useWorkspace((s) => s.tree);
  const root = useWorkspace((s) => s.root);
  const truncated = useWorkspace((s) => s.truncated);
  const expanded = useWorkspace((s) => s.expanded);
  const toggleDir = useWorkspace((s) => s.toggleDir);

  if (!root) return null;

  return (
    <div className="filetree">
      <div className="filetree-head">Files</div>
      {tree.length === 0 ? (
        <div className="filetree-empty">This folder is empty, or everything in it is hidden.</div>
      ) : (
        <Nodes nodes={tree} depth={0} expanded={expanded} toggleDir={toggleDir} onPick={onPick} />
      )}
      {/* Said out loud rather than silently showing a partial tree. Pointing
          Arthur at a home directory by mistake is easy, and a tree that
          quietly stops is indistinguishable from a small project. */}
      {truncated && (
        <div className="filetree-empty">
          Only the first part of this folder is shown — it's very large. Arthur can still reach
          every file in it.
        </div>
      )}
    </div>
  );
}

function Nodes({ nodes, depth, expanded, toggleDir, onPick }) {
  return nodes.map((node) => {
    const open = !!expanded[node.path];
    if (node.dir) {
      return (
        <div key={node.path}>
          <button
            className="filetree-row"
            style={{ paddingLeft: 8 + depth * 13 }}
            onClick={() => toggleDir(node.path)}
          >
            <ChevronRight
              size={12} strokeWidth={2.2}
              className={`filetree-caret${open ? " open" : ""}`}
            />
            <Folder size={13} strokeWidth={1.8} />
            <span className="filetree-name">{node.name}</span>
          </button>
          {open && (
            <Nodes
              nodes={node.children || []} depth={depth + 1}
              expanded={expanded} toggleDir={toggleDir} onPick={onPick}
            />
          )}
        </div>
      );
    }
    return (
      <button
        key={node.path}
        className="filetree-row"
        style={{ paddingLeft: 8 + depth * 13 + 14 }}
        title={`${node.path} — click to reference in your message`}
        onClick={() => onPick && onPick(node.path)}
      >
        <File size={13} strokeWidth={1.8} />
        <span className="filetree-name">{node.name}</span>
      </button>
    );
  });
}
