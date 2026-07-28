// The paper. This is the centre of Research mode and the thing the whole
// pipeline exists to produce.
//
// It is a DOCUMENT, not a chat reply: title, abstract, headed sections,
// continuous prose, and a reference list. Everything in it is editable in
// place, because the person who commissioned it is the one who knows where it
// is wrong, and making them re-prompt to fix a sentence would be absurd.
//
// THE CITATION SYSTEM IS THE POINT. Every [n] the model wrote is rendered here
// as a live pill formatted in the chosen style. Hovering one lights up the
// matching source in the sidebar; clicking scrolls to it. The reverse works
// too (see ResearchPaper's focusSource effect and EvidencePanel): clicking a
// source in the sidebar scrolls the paper to the paragraph that cites it and
// highlights every paragraph that uses it. A claim you cannot trace in one
// click is a claim you have to take on faith.
//
// Switching APA -> MLA re-renders instantly and offline. The model never wrote
// the citations, so there is nothing to regenerate -- see lib/citeFormat.js.
import React, { useEffect, useRef, useState } from "react";
import {
  AlertCircle, ClipboardCheck, Copy, Download, FileText, Loader2, PanelRight,
  Plus, Search, Trash2,
} from "lucide-react";
import { useResearch } from "../../stores/research";
import { STYLES, HEADINGS, inTextLabel, isNumericStyle, referenceLine, orderReferences }
  from "../../lib/citeFormat";
import EvidencePanel from "./EvidencePanel";

const CONF = {
  thin: {
    label: "Thin support",
    why: "One source backed this, or every source that did came from the same publisher. It is probably right, but a second independent source would settle it.",
  },
  unverified: {
    label: "Unverified",
    why: "This was asserted without a retrieved source behind it. Nothing in the evidence panel covers the claim, so verify it before relying on it.",
  },
};

export default function ResearchPaper() {
  const paper = useResearch((s) => s.paper);
  const sections = useResearch((s) => s.sections);
  const evidence = useResearch((s) => s.evidence);
  // Read once, at the top. Calling a hook inside the paragraph .map() below
  // would break the rules of hooks (variable hook count per render) AND
  // subscribe every paragraph separately, re-rendering the whole paper on
  // every mouse move across the sidebar.
  const hoverCite = useResearch((s) => s.hoverCite);
  const style = useResearch((s) => s.style);
  const customStyle = useResearch((s) => s.customStyle);
  const showEvidence = useResearch((s) => s.showEvidence);
  const writing = useResearch((s) => s.writing);
  const finding = useResearch((s) => s.finding);
  const newSourceIds = useResearch((s) => s.newSourceIds);
  const focusSource = useResearch((s) => s.focusSource);
  const {
    setStyle, setCustomStyle, toggleEvidencePanel, exportAs, findMore,
    editParagraph, editHeading, editTitle, editAbstract, deleteParagraph,
    setHoverCite, toggleEv, clearFocusSource, writeReportNow, copyPaper,
  } = useResearch();

  const [editing, setEditing] = useState(null);   // "title" | "abstract" | paraId | headingId
  const [explain, setExplain] = useState(null);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  // A two-second tick on the button itself. The toast already confirms the
  // copy, but the confirmation people actually look for is on the control they
  // just pressed -- without it the click feels like it did nothing.
  const [copied, setCopied] = useState(false);
  const docRef = useRef(null);
  const paraRefs = useRef({});

  const byN = {};
  evidence.forEach((e) => { byN[e.n] = e; });

  // Only sources the paper ACTUALLY cites belong in the reference list. The
  // evidence panel holds everything found; a bibliography holds what was used.
  const citedNumbers = new Set();
  sections.forEach((sec) =>
    (sec.paragraphs || []).forEach((p) => (p.citations || []).forEach((c) => citedNumbers.add(c))));
  const cited = evidence.filter((e) => citedNumbers.has(e.n));
  const references = orderReferences(cited, style);

  // Which source number is currently lit, so a paragraph can tell in O(1)
  // whether it cites it.
  const litSource = hoverCite ? evidence.find((e) => e.id === hoverCite) : null;
  const litN = litSource ? litSource.n : null;

  // Sidebar -> paper. Scroll to the first paragraph citing the clicked source.
  useEffect(() => {
    if (!focusSource) return;
    const src = evidence.find((e) => e.id === focusSource);
    if (!src) { clearFocusSource(); return; }
    for (const sec of sections) {
      const hit = (sec.paragraphs || []).find((p) => (p.citations || []).includes(src.n));
      if (hit) {
        const el = paraRefs.current[`${sec.id}:${hit.id}`];
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
        break;
      }
    }
    clearFocusSource();
  }, [focusSource, sections, evidence, clearFocusSource]);

  if (!paper && !sections.length) {
    return (
      <div className="research-report">
        <div className="paper-pending">
          <Loader2 size={20} className="spin" />
          <span>Writing the paper…</span>
        </div>
      </div>
    );
  }

  const styleLabel = (STYLES.find((s) => s.id === style) || STYLES[0]).label;

  return (
    <div className="research-report">
      <div className="research-doc-pane">
        <div className="research-doc-bar">
          <select
            className="paper-style-picker"
            value={style}
            onChange={(e) => setStyle(e.target.value)}
            title="Citation style — switching is instant, nothing regenerates"
          >
            {STYLES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>

          {style === "custom" && (
            <input
              className="paper-custom-style"
              value={customStyle}
              onChange={(e) => setCustomStyle(e.target.value)}
              placeholder="Describe the format, e.g. like APA but with the URL in brackets last"
            />
          )}

          {/* While writing, the source count is not just uninteresting, it is
              MISLEADING: it counts citations in the sections written so far and
              reads as a finished tally. "1 of 42 sources cited" on a paper
              still being written is why a working run looked like a failed one. */}
          <span className="research-doc-status">
            {writing
              ? <><Loader2 size={12} className="spin" /> Arthur is writing — {sections.length} section{sections.length === 1 ? "" : "s"} so far</>
              : `${cited.length} of ${evidence.length} sources cited`}
          </span>

          <button className="btn tiny" disabled={finding} onClick={() => setFindOpen(true)}>
            {finding ? <><Loader2 size={13} className="spin" /> Searching</> : <><Plus size={13} strokeWidth={1.9} /> Find more sources</>}
          </button>

          <button
            className="btn tiny"
            title="Copy the whole paper, with citations rendered in the chosen style"
            onClick={async () => {
              await copyPaper();
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
          >
            {copied
              ? <><ClipboardCheck size={13} strokeWidth={1.8} /> Copied</>
              : <><Copy size={13} strokeWidth={1.8} /> Copy</>}
          </button>

          <div className="paper-export">
            <button className="btn tiny" onClick={() => setExportOpen((o) => !o)}>
              <Download size={13} strokeWidth={1.8} /> Export
            </button>
            {exportOpen && (
              <div className="paper-export-menu" onMouseLeave={() => setExportOpen(false)}>
                <button onClick={() => { exportAs("docx"); setExportOpen(false); }}>
                  <FileText size={13} strokeWidth={1.8} /> Word (.docx)
                </button>
                <button onClick={() => { exportAs("pdf"); setExportOpen(false); }}>
                  <FileText size={13} strokeWidth={1.8} /> PDF
                </button>
              </div>
            )}
          </div>

          <button className={`btn tiny${showEvidence ? " active" : ""}`} onClick={toggleEvidencePanel}>
            <PanelRight size={13} strokeWidth={1.8} /> Sources
          </button>
        </div>

        {newSourceIds.length > 0 && (
          <div className="paper-new-sources">
            <span>
              {newSourceIds.length} new source{newSourceIds.length === 1 ? "" : "s"} added. The paper
              does not cite {newSourceIds.length === 1 ? "it" : "them"} yet.
            </span>
            <button className="btn tiny primary" disabled={writing} onClick={writeReportNow}>
              Rewrite to include {newSourceIds.length === 1 ? "it" : "them"}
            </button>
          </div>
        )}

        <div className="paper-scroll" ref={docRef}>
          <article className="paper">
            <h1
              className="paper-title"
              onDoubleClick={() => setEditing("title")}
              title="Double-click to edit"
            >
              {editing === "title" ? (
                <input
                  autoFocus
                  defaultValue={paper?.title || ""}
                  onBlur={(e) => { editTitle(e.target.value); setEditing(null); }}
                />
              ) : (paper?.title || "Untitled paper")}
            </h1>

            {paper?.abstract && (
              <section className="paper-abstract">
                <h2>Abstract</h2>
                {editing === "abstract" ? (
                  <textarea
                    autoFocus
                    defaultValue={paper.abstract}
                    onBlur={(e) => { editAbstract(e.target.value); setEditing(null); }}
                  />
                ) : (
                  <p onDoubleClick={() => setEditing("abstract")} title="Double-click to edit">
                    {paper.abstract}
                  </p>
                )}
              </section>
            )}

            {sections.map((sec) => (
              <section key={sec.id} className="paper-section">
                {editing === `h:${sec.id}` ? (
                  <input
                    className="paper-heading-edit"
                    autoFocus
                    defaultValue={sec.heading}
                    onBlur={(e) => { editHeading(sec.id, e.target.value); setEditing(null); }}
                  />
                ) : (
                  <h2 onDoubleClick={() => setEditing(`h:${sec.id}`)} title="Double-click to edit">
                    {sec.heading}
                  </h2>
                )}

                {(sec.paragraphs || []).map((p) => {
                  const key = `${sec.id}:${p.id}`;
                  const conf = CONF[p.conf];

                  // A table is a paragraph-shaped block with structured data
                  // instead of prose. It still lights up from the sidebar and
                  // still cites: every row carries the source behind it.
                  // A notice is Arthur telling you it could not write this
                  // section. It is NOT prose and must never look like prose:
                  // no confidence rail, no citation pills, no double-click to
                  // edit. Styling it like a paragraph is how the old
                  // verbatim-abstract fallback fooled people into reading a
                  // source's own words as the paper's argument.
                  if (p.kind === "notice") {
                    return (
                      <div key={p.id} className="paper-notice">
                        <AlertCircle size={14} strokeWidth={1.9} />
                        <span>{p.text}</span>
                      </div>
                    );
                  }

                  if (p.kind === "table") {
                    return (
                      <div
                        key={p.id}
                        ref={(el) => { if (el) paraRefs.current[key] = el; }}
                        className={`paper-para${litN && (p.citations || []).includes(litN) ? " lit" : ""}`}
                      >
                        <span className="paper-conf-rail ok" />
                        <div className="paper-para-body">
                          <div className="paper-table-wrap">
                            <table className="paper-table">
                              <thead>
                                <tr>
                                  {(p.columns || []).map((c, i) => <th key={i}>{c}</th>)}
                                  <th className="src-col">Src</th>
                                </tr>
                              </thead>
                              <tbody>
                                {(p.rows || []).map((row, ri) => {
                                  const n = (p.row_sources || [])[ri];
                                  const src = byN[n];
                                  return (
                                    <tr key={ri}>
                                      {row.map((cell, ci) => <td key={ci}>{cell}</td>)}
                                      <td className="src-col">
                                        {src && (
                                          <span
                                            className={`cite-pill numeric${hoverCite === src.id ? " hot" : ""}`}
                                            onMouseEnter={() => setHoverCite(src.id)}
                                            onMouseLeave={() => setHoverCite(null)}
                                            onClick={() => { setHoverCite(src.id); toggleEv(src.id); }}
                                            title={src.title}
                                          >
                                            {n}
                                          </span>
                                        )}
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                          {p.caption && <div className="paper-table-caption">{p.caption}</div>}
                          <div className="paper-para-foot">
                            <button
                              className="paper-para-del"
                              title="Remove this table"
                              onClick={() => deleteParagraph(sec.id, p.id)}
                            >
                              <Trash2 size={11} strokeWidth={2} />
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  }

                  return (
                    <div
                      key={p.id}
                      ref={(el) => { if (el) paraRefs.current[key] = el; }}
                      className={`paper-para${litN && (p.citations || []).includes(litN) ? " lit" : ""}`}
                    >
                      <span className={`paper-conf-rail ${p.conf}`} />
                      <div className="paper-para-body">
                        {editing === key ? (
                          <textarea
                            autoFocus
                            defaultValue={p.text}
                            onBlur={(e) => { editParagraph(sec.id, p.id, e.target.value); setEditing(null); }}
                          />
                        ) : (
                          <p onDoubleClick={() => setEditing(key)} title="Double-click to edit">
                            <Cited
                              text={p.text}
                              byN={byN}
                              style={style}
                              hoverCite={hoverCite}
                              onHover={setHoverCite}
                              onOpen={toggleEv}
                            />
                          </p>
                        )}
                        {/* No "Arthur wrote this" attribution and no
                            accept/revert. Arthur wrote the ENTIRE paper, so
                            marking each paragraph as machine-written says
                            nothing, and "accepting" prose that is already
                            yours to edit is a step that does no work. The
                            only real actions are edit (double-click) and
                            remove. */}
                        <div className="paper-para-foot">
                          {conf && (
                            <button className="research-conf-btn" onClick={() => setExplain(p.conf)}>
                              {conf.label}
                            </button>
                          )}
                          {/* An unverified claim is the one place the app
                              should offer to DO something rather than just
                              label the problem: send this exact sentence back
                              out to search and see if anything backs it. */}
                          {p.conf === "unverified" && (
                            <button
                              className="paper-verify"
                              disabled={finding}
                              onClick={() => findMore(p.text.replace(/\[\d+\]/g, "").slice(0, 300))}
                            >
                              Verify this
                            </button>
                          )}
                          <button
                            className="paper-para-del"
                            title="Remove this paragraph"
                            onClick={() => deleteParagraph(sec.id, p.id)}
                          >
                            <Trash2 size={11} strokeWidth={2} />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </section>
            ))}

            {writing && (
              <div className="paper-writing">
                <Loader2 size={14} className="spin" /> writing the next section…
              </div>
            )}

            {references.length > 0 && (
              <section className="paper-references">
                <h2>{HEADINGS[style] || "References"}</h2>
                {references.map((src) => (
                  <p
                    key={src.id}
                    className="paper-ref"
                    onMouseEnter={() => setHoverCite(src.id)}
                    onMouseLeave={() => setHoverCite(null)}
                  >
                    {referenceLine(src, style)}
                  </p>
                ))}
              </section>
            )}
          </article>
        </div>
      </div>

      {showEvidence && <EvidencePanel variant="report" />}

      {findOpen && (
        <div className="modal-backdrop" onClick={() => setFindOpen(false)}>
          <div className="modal narrow" onClick={(e) => e.stopPropagation()}>
            <h3><Search size={17} color="var(--accent)" /> Find more sources</h3>
            <p className="modal-sub">
              Describe what you want more evidence on. Arthur searches the web and the academic
              databases, keeps only what it does not already have, and adds it to your sources.
              Your paper is not rewritten unless you ask.
            </p>
            <input
              className="paper-find-input"
              autoFocus
              value={findQuery}
              onChange={(e) => setFindQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && findQuery.trim()) {
                  findMore(findQuery); setFindOpen(false); setFindQuery("");
                }
              }}
              placeholder="e.g. longitudinal studies after 2020"
            />
            <div className="research-actions end">
              <button className="btn" onClick={() => setFindOpen(false)}>Cancel</button>
              <button
                className="btn primary"
                disabled={!findQuery.trim()}
                onClick={() => { findMore(findQuery); setFindOpen(false); setFindQuery(""); }}
              >
                Search
              </button>
            </div>
          </div>
        </div>
      )}

      {explain && (
        <div className="modal-backdrop" onClick={() => setExplain(null)}>
          <div className="modal narrow" onClick={(e) => e.stopPropagation()}>
            <h3>{CONF[explain].label}</h3>
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

// Splits prose on [n] markers and renders each as a live citation pill in the
// current style. Markers with no matching source are left as plain text --
// visibly unresolved, which is the honest outcome rather than hiding them.
//
// `hoverCite` is passed in rather than selected here: this renders once per
// citation, and subscribing at that granularity would re-render every pill in
// the paper on every mouse move.
function Cited({ text, byN, style, hoverCite, onHover, onOpen }) {
  const parts = String(text || "").split(/(\[\d+\])/g);

  return parts.map((part, i) => {
    const m = /^\[(\d+)\]$/.exec(part);
    if (!m) return <React.Fragment key={i}>{part}</React.Fragment>;
    const src = byN[Number(m[1])];
    if (!src) return <React.Fragment key={i}>{part}</React.Fragment>;
    const numeric = isNumericStyle(style);
    return (
      <span
        key={i}
        className={`cite-pill${numeric ? " numeric" : " authordate"}${hoverCite === src.id ? " hot" : ""}`}
        onMouseEnter={() => onHover(src.id)}
        onMouseLeave={() => onHover(null)}
        onClick={(e) => { e.stopPropagation(); onHover(src.id); onOpen(src.id); }}
        title={src.title}
      >
        {numeric ? inTextLabel(src, style) : `(${inTextLabel(src, style)})`}
      </span>
    );
  });
}
