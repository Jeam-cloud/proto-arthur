// The report: a document, not a chat reply.
//
// Two ideas are doing the work on this screen.
//
// CITATIONS ARE LIVE. Every [n] in the prose is a control, not decoration.
// Hovering highlights the source card; clicking scrolls the panel to it. A
// claim you cannot trace in one click is a claim you have to take on faith,
// which is the exact thing this mode exists to avoid.
//
// CONFIDENCE IS QUIET. Marking is a 2px rail in the margin and one small line
// of text, and only on the blocks that earned it. A report covered in warnings
// trains people to ignore warnings, so the default state -- well supported --
// is deliberately unmarked. The rule that assigns the marks lives in
// python/research/engine.py and is arithmetic, not the model's opinion of
// itself: two independent publishers means supported, one means thin, none
// means unverified.
import React, { useState } from "react";
import { Download, Copy, PanelRight, Check, Undo2 } from "lucide-react";
import { useResearch } from "../../stores/research";
import { useToasts } from "../../stores/toasts";
import EvidencePanel from "./EvidencePanel";

const CONF = {
  thin: {
    label: "Thin support — one source",
    why: "One source backed this statement, or every source that did came from the same publisher. It is probably right, but a second independent source would settle it.",
  },
  unverified: {
    label: "Unverified — no source support",
    why: "The model asserted this without a retrieved source behind it. Nothing in the evidence panel covers the claim, so verify it before relying on it.",
  },
};

export default function ResearchReport() {
  const blocks = useResearch((s) => s.blocks);
  const evidence = useResearch((s) => s.evidence);
  const question = useResearch((s) => s.question);
  const showEvidence = useResearch((s) => s.showEvidence);
  const cursorBlock = useResearch((s) => s.cursorBlock);
  const explain = useResearch((s) => s.explain);
  const {
    toggleEvidencePanel, setCursor, setHoverCite, setExplain,
    acceptBlock, revertBlock, editBlock, toggleEv,
  } = useResearch();
  const pushToast = useToasts((s) => s.push);
  const [editing, setEditing] = useState(null);

  const asMarkdown = () =>
    [
      `# ${question}`,
      "",
      ...blocks.map((b) => (b.type === "h" ? `## ${b.text}` : b.type === "q" ? `> ${b.text}` : b.text)),
      "",
      "## Sources",
      ...evidence.map((e) =>
        `[${e.n}] ${e.title} — ${e.kind === "paper" ? `${e.authors} (${e.year}), ${e.venue}${e.doi ? `, doi:${e.doi}` : ""}` : e.domain} — ${e.url}`,
      ),
    ].join("\n\n");

  const onExport = () => {
    // Blob download rather than a backend write: the report is the user's
    // document and should land wherever they keep documents, not in Arthur's
    // data directory where they would have to go hunting for it.
    const blob = new Blob([asMarkdown()], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${(question || "investigation").slice(0, 60).replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-")}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    pushToast("Report exported as Markdown.", "success");
  };

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(asMarkdown());
      pushToast("Report copied to clipboard.", "success");
    } catch {
      pushToast("Could not reach the clipboard.", "error");
    }
  };

  return (
    <div className="research-report">
      <div className="research-doc-pane">
        <div className="research-doc-bar">
          <span className="research-doc-status">Report · you and Arthur are both editing</span>
          <button className="btn tiny" onClick={onExport}><Download size={13} strokeWidth={1.8} /> Export</button>
          <button className="btn tiny" onClick={onCopy}><Copy size={13} strokeWidth={1.8} /> Copy</button>
          <button className={`btn tiny${showEvidence ? " active" : ""}`} onClick={toggleEvidencePanel}>
            <PanelRight size={13} strokeWidth={1.8} /> Evidence
          </button>
        </div>

        <div className="research-doc">
          <div className="research-doc-col">
            {blocks.map((b) => {
              const held = b.id === cursorBlock;
              const conf = CONF[b.conf];
              return (
                <div key={b.id} className="research-block" onClick={() => setCursor(b.id)}>
                  <span className={`research-conf-rail ${b.conf}`} />
                  <div className={`research-block-body${b.fresh ? " fresh" : ""}${held ? " held" : ""}`}>
                    {editing === b.id ? (
                      <textarea
                        className="research-block-edit"
                        autoFocus
                        defaultValue={b.text}
                        onBlur={(e) => { editBlock(b.id, e.target.value); setEditing(null); }}
                      />
                    ) : (
                      <div
                        className={`research-block-text ${b.type}`}
                        onDoubleClick={() => setEditing(b.id)}
                        title="Double-click to edit"
                      >
                        <Cited text={b.text} onHover={setHoverCite} onOpen={toggleEv} />
                      </div>
                    )}

                    <div className="research-block-foot">
                      {conf && (
                        <button
                          className="research-conf-btn"
                          onClick={(e) => { e.stopPropagation(); setExplain(b.conf); }}
                        >
                          {conf.label}
                        </button>
                      )}
                      {b.ai && (
                        <>
                          <span className="research-attrib">Arthur wrote this</span>
                          <button className="btn tiny" onClick={(e) => { e.stopPropagation(); acceptBlock(b.id); }}>
                            <Check size={11} strokeWidth={2} /> Accept
                          </button>
                          <button className="btn tiny ghost" onClick={(e) => { e.stopPropagation(); revertBlock(b.id); }}>
                            <Undo2 size={11} strokeWidth={2} /> Revert
                          </button>
                        </>
                      )}
                      {held && <span className="research-attrib">Your cursor — Arthur will not edit here</span>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {showEvidence && <EvidencePanel variant="report" />}

      {explain && (
        <div className="modal-backdrop" onClick={() => setExplain(null)}>
          <div className="modal narrow" onClick={(e) => e.stopPropagation()}>
            <h3>{CONF[explain].label.split(" — ")[0]}</h3>
            <p>{CONF[explain].why}</p>
            <div className="research-actions end">
              <button className="btn primary" onClick={() => setExplain(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Splits "…restricts redistribution [1] above 700M users [3]." into text runs
// and clickable pills. Kept as a pure function of the block text so an edited
// block re-renders its citations without any extra bookkeeping.
function Cited({ text, onHover, onOpen }) {
  const parts = String(text || "").split(/(\[\d+\])/g);
  const hoverCite = useResearch((s) => s.hoverCite);
  const evidence = useResearch((s) => s.evidence);

  return parts.map((part, i) => {
    const m = /^\[(\d+)\]$/.exec(part);
    if (!m) return <React.Fragment key={i}>{part}</React.Fragment>;
    const n = Number(m[1]);
    const src = evidence.find((e) => e.n === n);
    if (!src) return <React.Fragment key={i}>{part}</React.Fragment>;
    return (
      <span
        key={i}
        className={`cite-pill${hoverCite === src.id ? " hot" : ""}`}
        onMouseEnter={() => onHover(src.id)}
        onMouseLeave={() => onHover(null)}
        onClick={(e) => { e.stopPropagation(); onHover(src.id); onOpen(src.id); }}
        title={src.title}
      >
        {n}
      </span>
    );
  });
}
