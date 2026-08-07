// The review gate, rendered.
//
// This panel is Code mode's version of a citation. Research mode earns trust by
// showing you where a claim came from; Code mode earns it by showing you
// exactly what will change before it changes.
//
// Built to the approved design (Arthur.dc prototype, 7 Aug). Three things in it
// are not decoration and should survive any restyle:
//   * the conflict notice lives ON the file's card, not in a toast — a warning
//     that fades leaves the offending file looking identical to the ones that
//     applied cleanly;
//   * "cut short" is called out ABOVE the files, because applying a partial
//     changeset is the one mistake this screen exists to prevent;
//   * Discard sits next to Apply at the same weight. A review where only
//     approval is convenient is not a review.
import React from "react";
import {
  AlertCircle, Check, ChevronRight, FileDiff, FileMinus2, FilePlus2, X,
} from "lucide-react";
import { useChanges } from "../../stores/changes";

const KIND_ICON = { create: FilePlus2, delete: FileMinus2, modify: FileDiff };
// Diffs longer than this collapse behind "Show diff". A 400-line rewrite pushes
// the Apply buttons off screen, and a review you must scroll past to act on
// gets skipped.
const LONG_DIFF_LINES = 40;

export default function ChangesPanel({ conversationId }) {
  const {
    changes, files, additions, deletions, expanded, busy, capped, flash,
    diffOpen, toggleOpen, toggleDiff, toggleSelected, selected, selectedPaths,
    apply, discard, reread, continueRun, registerCard,
  } = useChanges();

  if (!files) return null;

  const chosen = selectedPaths();
  const partial = chosen.length !== changes.length;
  const applyLabel = busy ? "Applying…"
    : capped ? "Apply anyway"
    : partial ? `Apply ${chosen.length} of ${changes.length}` : "Apply all";

  return (
    <div className={`changes-panel${expanded ? " expanded" : ""}`}>
      <button className="changes-head" onClick={toggleOpen}>
        <ChevronRight size={14} strokeWidth={2} className={`chev${expanded ? " open" : ""}`} />
        <span className="changes-title">
          {capped
            ? `${files} ${files === 1 ? "file" : "files"} staged so far`
            : `${files} ${files === 1 ? "file" : "files"} to review`}
        </span>
        <span className="diff-stat add">+{additions}</span>
        <span className="diff-stat del">−{deletions}</span>
        <span className="changes-hint">nothing has been saved yet</span>
      </button>

      {expanded && (
        <>
          <div className="changes-list">
            {capped && (
              <div className="notice warn">
                <AlertCircle size={15} strokeWidth={1.9} />
                <div>
                  <div className="notice-title">This review is incomplete.</div>
                  <p>
                    Arthur hit the tool-use limit for one message and stopped part-way.
                    What's below is real, but it isn't the whole change — applying it now
                    leaves the work half-done.
                  </p>
                  <button className="btn tiny warn" onClick={() => continueRun(conversationId)}>
                    Let it continue
                  </button>
                </div>
              </div>
            )}

            {changes.map((c) => {
              const Icon = KIND_ICON[c.kind] || FileDiff;
              const lines = c.diff ? c.diff.split("\n").length : 0;
              const isLong = lines > LONG_DIFF_LINES;
              const showDiff = diffOpen[c.path] ?? !isLong;
              // A conflicted file is force-deselected: it cannot apply, and a
              // ticked box promising otherwise would be a lie.
              const isSelected = !c.conflict && selected[c.path] !== false;
              const dir = c.path.includes("/") ? c.path.slice(0, c.path.lastIndexOf("/") + 1) : "";
              const name = c.path.slice(dir.length);
              return (
                <div
                  key={c.path}
                  ref={(el) => registerCard(c.path, el)}
                  className={`change${c.conflict ? " conflict" : isSelected ? "" : " deselected"}`
                    + (flash === c.path ? " flash" : "")}
                >
                  <div className="change-head">
                    <button
                      className={`tickbox${isSelected ? " on" : ""}`}
                      onClick={() => toggleSelected(c.path)}
                      disabled={c.conflict}
                      title={c.conflict ? "This file can't apply until it's re-read"
                        : "Include this file when applying"}
                    >
                      {isSelected && <Check size={11} strokeWidth={3} />}
                    </button>
                    <Icon size={15} strokeWidth={1.7} className={`change-kind ${c.kind}`} />
                    {/* Path split so the FILENAME never truncates: the directory
                        is context you can lose, the name is what identifies it. */}
                    <span className="change-path" title={c.path}>
                      <span className="dir">{dir}</span><span className="name">{name}</span>
                    </span>
                    {c.additions > 0 && <span className="diff-stat add">+{c.additions}</span>}
                    {c.deletions > 0 && <span className="diff-stat del">−{c.deletions}</span>}
                    <button
                      className="icon-btn" title="Discard this file"
                      onClick={() => discard(conversationId, [c.path])}
                    >
                      <X size={13} strokeWidth={1.9} />
                    </button>
                  </div>

                  {c.conflict && (
                    <div className="change-conflict">
                      <AlertCircle size={15} strokeWidth={1.9} />
                      <span>
                        You changed this file after Arthur read it. Your version was kept and
                        this edit was skipped — the other files still apply.
                      </span>
                      <button className="btn tiny warn" onClick={() => reread(conversationId, c.path)}>
                        Re-read and retry
                      </button>
                    </div>
                  )}

                  <div className="change-body">
                    {isLong && (
                      <button className="fold" onClick={() => toggleDiff(c.path)}>
                        {showDiff ? "Hide diff" : `Show diff · ${lines} lines`}
                      </button>
                    )}
                    {showDiff && <Diff text={c.diff} />}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="changes-actions">
            <button
              className={`btn ${capped ? "" : "primary"}`}
              disabled={busy || !chosen.length}
              onClick={() => apply(conversationId, chosen)}
            >
              {busy && <span className="spinner" />}
              {applyLabel}
            </button>
            <button className="btn" disabled={busy} onClick={() => discard(conversationId, null)}>
              Discard all
            </button>
            <span
              className="changes-note"
              title="Applying writes these files to your folder. Discarding leaves it untouched. Running code still asks separately — a diff can't show what code does."
            >
              Applying writes these files. Discarding leaves the folder untouched.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

// Unified diff with real line numbers.
//
// The backend sends a plain unified diff, so the numbers are derived here by
// reading each `@@ -a,b +c,d @@` header and counting forward. Worth the twenty
// lines: "line 41" is how people talk about code, and a diff without numbers
// can't be matched against the file open in their editor.
function Diff({ text }) {
  if (!text) return <div className="diff empty">No textual change.</div>;
  let oldNo = 0;
  let newNo = 0;
  const rows = [];
  for (const line of text.split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) continue;  // file headers: noise here
    if (line.startsWith("@@")) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (m) { oldNo = +m[1]; newNo = +m[2]; }
      rows.push({ cls: "hunk", no: "", sign: "", text: line });
      continue;
    }
    if (line.startsWith("+")) rows.push({ cls: "add", no: newNo++, sign: "+", text: line.slice(1) });
    else if (line.startsWith("-")) rows.push({ cls: "del", no: oldNo++, sign: "−", text: line.slice(1) });
    else { rows.push({ cls: "", no: oldNo, sign: " ", text: line.slice(1) }); oldNo++; newNo++; }
  }
  return (
    <div className="diff">
      {rows.map((r, i) => (
        <div key={i} className={`diff-line ${r.cls}`}>
          <span className="gutter">{r.no}</span>
          {/* Sign AND colour: roughly 1 in 12 men can't separate red from green
              reliably, and this is the screen where confusing an addition with
              a deletion costs the most. */}
          <span className="sign">{r.sign}</span>
          <span className="code">{r.text || " "}</span>
        </div>
      ))}
    </div>
  );
}
