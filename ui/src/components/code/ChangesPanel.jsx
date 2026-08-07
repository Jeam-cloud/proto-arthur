// The review gate, rendered.
//
// This panel is Code mode's version of a citation. Research mode earns trust by
// showing you where a claim came from; Code mode earns it by showing you
// exactly what will change before it changes. Everything here serves that: the
// diff is unfolded by default for small changes, the destructive button is not
// the pretty one, and "Discard" is as easy to reach as "Apply" — a review where
// only approval is convenient is not a review.
import React from "react";
import { Check, ChevronDown, ChevronRight, FilePlus2, FileMinus2, FileDiff, X } from "lucide-react";
import { useChanges } from "../../stores/changes";

const KIND_ICON = { create: FilePlus2, delete: FileMinus2, modify: FileDiff };
// Diffs bigger than this stay folded. A 400-line rewrite pushes the buttons off
// screen, and a review you have to scroll past to act on gets skipped.
const AUTO_OPEN_LINES = 40;

export default function ChangesPanel({ conversationId }) {
  const {
    changes, files, additions, deletions, open, busy, expanded, selected,
    toggleOpen, toggleExpanded, toggleSelected, selectedPaths, apply, discard,
  } = useChanges();

  if (!files) return null;

  const chosen = selectedPaths();
  const partial = chosen.length !== changes.length;

  return (
    <div className={`changes-panel${open ? " open" : ""}`}>
      <button className="changes-head" onClick={toggleOpen}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="changes-title">
          {files} {files === 1 ? "file" : "files"} to review
        </span>
        <span className="diff-stat add">+{additions}</span>
        <span className="diff-stat del">−{deletions}</span>
        <span className="changes-hint">nothing has been saved yet</span>
      </button>

      {open && (
        <>
          <div className="changes-list">
            {changes.map((c) => {
              const Icon = KIND_ICON[c.kind] || FileDiff;
              const lines = c.diff ? c.diff.split("\n").length : 0;
              const isOpen = expanded[c.path] ?? lines <= AUTO_OPEN_LINES;
              const isSelected = selected[c.path] !== false;
              return (
                <div key={c.path} className={`change${isSelected ? "" : " deselected"}`}>
                  <div className="change-head">
                    <input
                      type="checkbox" checked={isSelected}
                      onChange={() => toggleSelected(c.path)}
                      title="Include this file when applying"
                    />
                    <Icon size={13} strokeWidth={1.9} className={`change-kind ${c.kind}`} />
                    <button className="change-path" onClick={() => toggleExpanded(c.path)}>
                      {c.path}
                    </button>
                    <span className="diff-stat add">+{c.additions}</span>
                    <span className="diff-stat del">−{c.deletions}</span>
                    <button className="btn tiny ghost" onClick={() => discard(conversationId, [c.path])}>
                      Discard
                    </button>
                  </div>
                  {isOpen && <Diff text={c.diff} />}
                </div>
              );
            })}
          </div>

          <div className="changes-actions">
            <button
              className="btn primary" disabled={busy || !chosen.length}
              onClick={() => apply(conversationId, chosen)}
            >
              <Check size={13} strokeWidth={2.2} />
              {partial ? `Apply ${chosen.length} of ${changes.length}` : "Apply all"}
            </button>
            <button className="btn" disabled={busy} onClick={() => discard(conversationId, null)}>
              <X size={13} strokeWidth={2.2} />
              Discard all
            </button>
            <span className="changes-note">
              Applying writes these files to your folder. Discarding leaves it untouched.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

// Unified diff, colourised per line.
//
// WHY colour AND a leading +/- rather than colour alone: roughly 1 in 12 men
// cannot reliably separate red from green, and this is the screen where getting
// an addition confused with a deletion costs the most.
function Diff({ text }) {
  if (!text) return <div className="diff empty">No textual change.</div>;
  return (
    <pre className="diff">
      {text.split("\n").map((line, i) => (
        <div key={i} className={`diff-line ${lineClass(line)}`}>{line || " "}</div>
      ))}
    </pre>
  );
}

function lineClass(line) {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "";
}
