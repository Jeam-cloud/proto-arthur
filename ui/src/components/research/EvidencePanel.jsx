// The evidence panel. Sources accumulate here for the whole run and never reset
// between steps, because "what have we actually got" is the question a person
// asks continuously while research is running.
//
// Four things here exist to stop the panel from flattering itself, or from
// being a dead end:
//
// 1. Papers get a different card from web pages. A peer-reviewed paper and a
//    blog post are not the same kind of claim and should not look the same.
// 2. Reprints are collapsed. Five outlets running the same wire story is one
//    source, and showing it as five would manufacture false corroboration.
// 3. Contradictions get their OWN card between the two sources involved,
//    instead of a note buried inside each one -- "the record disagrees" is
//    important enough to say once, clearly, rather than twice, quietly.
// 4. Every card links out. A source you cannot open yourself is a source you
//    have to take Arthur's word for, which defeats the point of citing it.
//    Papers link straight to the PDF Arthur itself read (with a REAL page
//    count -- see research/engine.py -- never an estimate); everything else
//    links to the page it came from.
import React, { useRef, useEffect } from "react";
import { ArrowLeftRight, ExternalLink, FileText } from "lucide-react";
import { useResearch } from "../../stores/research";

const PROVIDER_LABEL = {
  tavily: "Web",
  openalex: "OpenAlex",
  arxiv: "arXiv",
  crossref: "Crossref",
};

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function avatarLetter(e) {
  return (e.domain || e.venue || "?").charAt(0).toUpperCase();
}

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

  // A contradiction is a fact about a PAIR, not about either card alone, so it
  // gets rendered exactly once -- right after the second of the two sources
  // has appeared, so both are already visible above it. `seen` tracks what's
  // been rendered so far; `paired` stops the block from also firing off the
  // OTHER side of the same pair.
  const seen = new Set();
  const paired = new Set();

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
          seen.add(e.id);
          const reprints = reprintCount[e.id] || 0;
          const hot = hoverCite === e.id;
          const pairKey = e.contradicts ? [e.id, e.contradicts].sort().join("|") : "";
          const showContra = e.contradicts && seen.has(e.contradicts) && !paired.has(pairKey);
          if (showContra) paired.add(pairKey);
          const other = showContra ? evidence.find((x) => x.id === e.contradicts) : null;

          // A card links to the PDF it actually read when it read one (real
          // page count attached), otherwise to the page/landing URL. Never a
          // dead end: something is always clickable.
          const linkHref = e.pdf_url || e.url;
          const readPdf = (e.is_pdf || e.pdf_url) && e.pages > 0;

          return (
            <React.Fragment key={e.id}>
              <div
                ref={(el) => { if (el) cardRefs.current[e.id] = el; }}
                className={`evidence-card${hot ? " hot" : ""}`}
                onMouseEnter={() => setHoverCite(e.id)}
                onMouseLeave={() => setHoverCite(null)}
                onClick={() => toggleEv(e.id)}
              >
                <span className="evidence-num">{e.n}</span>
                <div className="evidence-body">
                  {e.kind === "paper" ? (
                    <div className="evidence-badges">
                      <span className="badge-provider">{PROVIDER_LABEL[e.provider] || e.provider}</span>
                      {readPdf ? (
                        <a
                          className="badge-pdf"
                          href={linkHref}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(ev) => ev.stopPropagation()}
                          title="Open the PDF Arthur read"
                        >
                          <FileText size={11} strokeWidth={1.9} /> PDF · {e.pages}p read
                        </a>
                      ) : e.url && (
                        <a
                          className="badge-pdf"
                          href={e.url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(ev) => ev.stopPropagation()}
                        >
                          <ExternalLink size={11} strokeWidth={1.9} /> View source
                        </a>
                      )}
                    </div>
                  ) : (
                    <div className="evidence-avatar-row">
                      <span className="evidence-avatar">{avatarLetter(e)}</span>
                      <a
                        className="mono evidence-domain"
                        href={e.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(ev) => ev.stopPropagation()}
                        title="Open this source"
                      >
                        {e.domain}
                      </a>
                      <span className="evidence-type">{e.type}</span>
                    </div>
                  )}

                  <div className="evidence-card-title">{e.title}</div>

                  {e.kind === "paper" ? (
                    <div className="evidence-paper">
                      <div className="evidence-paper-authors">{e.authors || "Unattributed"}</div>
                      <div className="evidence-paper-row">
                        <span className="evidence-venue">{e.venue || "Preprint"}</span>
                        <span className="mono evidence-year">{e.year || "n.d."}</span>
                        {e.cites > 0 && <span className="mono evidence-cites">{e.cites} citations</span>}
                      </div>
                      {e.doi && <div className="evidence-doi mono">doi:{e.doi}</div>}
                    </div>
                  ) : (
                    e.date && <div className="evidence-date">{formatDate(e.date)}</div>
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
                  </div>

                  {expandedEv === e.id && (
                    <div className="evidence-passage">
                      {e.passage}
                      {readPdf && (
                        <div className="evidence-passage-foot">Read in full: {e.pages} page{e.pages === 1 ? "" : "s"}.</div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {showContra && other && (
                <div className="contra-card">
                  <div className="contra-head">
                    <ArrowLeftRight size={14} strokeWidth={2} />
                    <span>Sources disagree</span>
                  </div>
                  {e.contra_note && <div className="contra-question">{e.contra_note}</div>}
                  <div className="contra-note">Both are shown. Arthur will not pick one for you.</div>
                  <div className="contra-refs">
                    {[other, e].map((r) => (
                      <button
                        key={r.id}
                        className="contra-ref"
                        onClick={(ev) => { ev.stopPropagation(); setHoverCite(r.id); toggleEv(r.id); }}
                      >
                        <span className="evidence-num small">{r.n}</span>
                        <span className="mono">{r.domain || r.venue}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
