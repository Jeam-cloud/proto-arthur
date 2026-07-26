// The evidence panel. Sources accumulate here for the whole run and never reset
// between steps, because "what have we actually got" is the question a person
// asks continuously while research is running.
//
// Three things here exist to stop the panel from flattering itself:
//
// 1. Papers get a different card from web pages. A peer-reviewed paper and a
//    blog post are not the same kind of claim and should not look the same.
// 2. Reprints are collapsed. Five outlets running the same wire story is one
//    source, and showing it as five would manufacture false corroboration.
// 3. Contradictions are shown on BOTH cards and point at each other. The
//    alternative -- quietly preferring one -- hides the most useful fact
//    available, which is that the record is not settled.
import React, { useRef, useEffect } from "react";
import { TriangleAlert, FileText, Globe } from "lucide-react";
import { useResearch } from "../../stores/research";

const PROVIDER_LABEL = {
  tavily: "Web",
  openalex: "OpenAlex",
  arxiv: "arXiv",
  crossref: "Crossref",
};

export default function EvidencePanel({ variant = "report" }) {
  const evidence = useResearch((s) => s.evidence);
  const evFilter = useResearch((s) => s.evFilter);
  const usedOnly = useResearch((s) => s.usedOnly);
  const expandedEv = useResearch((s) => s.expandedEv);
  const hoverCite = useResearch((s) => s.hoverCite);
  const { setEvFilter, toggleUsedOnly, toggleEv, setHoverCite } = useResearch();
  const scrollRef = useRef(null);
  const cardRefs = useRef({});

  // Reprint counts are computed from the FULL list, not the filtered one:
  // "3 sources, 1 independent" must stay true regardless of what you filter to.
  const reprintCount = {};
  evidence.forEach((e) => {
    if (e.dup_of) reprintCount[e.dup_of] = (reprintCount[e.dup_of] || 0) + 1;
  });

  const shown = evidence
    .filter((e) => !e.dup_of)
    .filter((e) => evFilter === "all" || (evFilter === "paper" ? e.kind === "paper" : e.type === evFilter))
    .filter((e) => !usedOnly || e.used);

  // Clicking a citation pill in the document scrolls its card into view.
  useEffect(() => {
    if (!hoverCite || variant !== "report") return;
    const el = cardRefs.current[hoverCite];
    const box = scrollRef.current;
    if (el && box) box.scrollTop = Math.max(0, el.offsetTop - 12);
  }, [hoverCite, variant]);

  return (
    <div className={`evidence-panel ${variant}`}>
      <div className="evidence-head">
        <div className="evidence-head-row">
          <div className="evidence-title">Evidence</div>
          <span className="evidence-count">
            {variant === "run" ? `${evidence.length} sources · accumulating` : shown.length}
          </span>
        </div>
        {variant === "report" && (
          <div className="evidence-filters">
            <select value={evFilter} onChange={(e) => setEvFilter(e.target.value)}>
              <option value="all">All types</option>
              <option value="paper">Papers</option>
              <option value="news">News</option>
              <option value="docs">Docs</option>
              <option value="blog">Blogs</option>
            </select>
            <span className="evidence-filter-label">Used only</span>
            <label className="switch" title="Hide sources the report did not cite">
              <input type="checkbox" checked={usedOnly} onChange={toggleUsedOnly} />
              <span className="track" />
              <span className="thumb" />
            </label>
          </div>
        )}
      </div>

      <div className="evidence-list" ref={scrollRef}>
        {shown.length === 0 && (
          <div className="evidence-empty">Sources appear here as they are found.</div>
        )}

        {shown.map((e) => {
          const reprints = reprintCount[e.id] || 0;
          const hot = hoverCite === e.id;
          const contra = e.contradicts ? evidence.find((x) => x.id === e.contradicts) : null;
          return (
            <div
              key={e.id}
              ref={(el) => { if (el) cardRefs.current[e.id] = el; }}
              className={`evidence-card${hot ? " hot" : ""}`}
              onMouseEnter={() => setHoverCite(e.id)}
              onMouseLeave={() => setHoverCite(null)}
              onClick={() => toggleEv(e.id)}
            >
              <span className="evidence-num">{e.n}</span>
              <div className="evidence-body">
                <div className="evidence-card-title">{e.title}</div>

                {e.kind === "paper" ? (
                  <div className="evidence-paper">
                    <div className="evidence-paper-authors">
                      {e.authors || "Unattributed"} · {e.year || "n.d."}
                    </div>
                    <div className="evidence-paper-row">
                      <span className="evidence-venue">{e.venue || "Preprint"}</span>
                      {e.cites > 0 && <span className="mono evidence-cites">{e.cites} citations</span>}
                      <span className="evidence-provider">{PROVIDER_LABEL[e.provider] || e.provider}</span>
                    </div>
                    {e.doi && <div className="evidence-doi mono">doi:{e.doi}</div>}
                    {e.pdf_url && (
                      <a
                        className="evidence-pdf"
                        href={e.pdf_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(ev) => ev.stopPropagation()}
                      >
                        <FileText size={11} strokeWidth={1.9} /> PDF
                      </a>
                    )}
                  </div>
                ) : (
                  <div className="evidence-web">
                    <span className="mono evidence-domain">{e.domain}</span>
                    {e.date && <span className="evidence-date">{e.date}</span>}
                    <span className="evidence-type">{e.type}</span>
                  </div>
                )}

                <div className="evidence-tags">
                  {e.used
                    ? <span className="tag good">in report</span>
                    : <span className="tag">not used</span>}
                  {reprints > 0 && (
                    <span className="evidence-reprints" title="Same publisher, same story">
                      {reprints + 1} sources · 1 independent
                    </span>
                  )}
                  {e.kind !== "paper" && (
                    <span className="evidence-provider">
                      <Globe size={10} strokeWidth={2} /> {PROVIDER_LABEL[e.provider] || e.provider}
                    </span>
                  )}
                </div>

                {contra && (
                  <div className="evidence-contra">
                    <TriangleAlert size={13} strokeWidth={1.9} />
                    <span>
                      Contradicts <em>{contra.title}</em>
                      {e.contra_note ? <span className="evidence-contra-note">{e.contra_note}</span> : null}
                    </span>
                  </div>
                )}

                {expandedEv === e.id && <div className="evidence-passage">{e.passage}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
