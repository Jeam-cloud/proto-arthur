// The brief composer. Not a chat box, on purpose.
//
// A chat box says "say something and I will reply". This says "commission a
// piece of work": there is a question field, a budget you pick before starting,
// and a list of what you have already commissioned. Every control here changes
// what the run will actually cost, which is why each one shows its own budget
// rather than hiding it behind a word like "Exhaustive".
import React from "react";
import { ChevronRight, Loader2 } from "lucide-react";
import { useResearch, DEPTHS, SOURCE_KINDS } from "../../stores/research";

export default function ResearchHome() {
  const {
    question, setQuestion, depth, setDepth, sources, toggleSource,
    advanced, toggleAdvanced, includeDomains, setIncludeDomains,
    excludeDomains, setExcludeDomains, toPlan, planning, openRecent,
  } = useResearch();
  const recents = useResearch((s) => s.recentRows());

  return (
    <div className="research-scroll">
      <div className="research-col wide">
        <h1 className="research-title">Commission an investigation</h1>
        <p className="research-lede">
          Arthur decomposes the question, searches and reads sources on your machine, then writes a
          report you can trace back to every source.
        </p>

        <div className="research-card">
          <label className="micro-label">Question</label>
          <textarea
            className="research-question"
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="How do the licenses of the major open-weight model releases differ for commercial use, and which restrict redistribution"
          />

          <label className="micro-label spaced">Depth</label>
          <div className="research-depth">
            {Object.entries(DEPTHS).map(([id, d]) => (
              <button
                key={id}
                className={`research-depth-opt${depth === id ? " active" : ""}`}
                onClick={() => setDepth(id)}
              >
                <span className="research-depth-label">{d.label}</span>
                <span className="research-depth-budget">{d.budget}</span>
              </button>
            ))}
          </div>

          <label className="micro-label spaced">Sources</label>
          <div className="research-chips">
            {SOURCE_KINDS.map((k) => (
              <button
                key={k.id}
                className={`research-chip${sources.includes(k.id) ? " active" : ""}`}
                onClick={() => toggleSource(k.id)}
              >
                {k.label}
              </button>
            ))}
          </div>

          <button className="research-advanced-toggle" onClick={toggleAdvanced}>
            <ChevronRight size={13} strokeWidth={2} className={advanced ? "rot90" : ""} />
            Advanced
          </button>
          {advanced && (
            <div className="research-advanced">
              <div>
                <label>Include domains</label>
                <input
                  type="text"
                  className="mono"
                  value={includeDomains}
                  onChange={(e) => setIncludeDomains(e.target.value)}
                  placeholder="arxiv.org, *.gov"
                />
              </div>
              <div>
                <label>Exclude domains</label>
                <input
                  type="text"
                  className="mono"
                  value={excludeDomains}
                  onChange={(e) => setExcludeDomains(e.target.value)}
                  placeholder="pinterest.com"
                />
              </div>
            </div>
          )}

          <div className="research-card-footer">
            <span className="research-budget">{DEPTHS[depth].budget}</span>
            <button className="btn primary" disabled={!question.trim() || planning} onClick={toPlan}>
              {planning ? <><Loader2 size={14} className="spin" /> Planning</> : "Start investigation"}
            </button>
          </div>
        </div>

        {recents.length > 0 && (
          <>
            <div className="micro-label spaced">Recent investigations</div>
            {recents.map((r) => (
              <div key={r.id} className="research-recent" onClick={() => openRecent(r.id)}>
                <span className={`research-dot ${r.status}`} />
                <div className="research-recent-body">
                  <div className="research-recent-title">{r.title}</div>
                  <div className="research-recent-meta">{r.meta}</div>
                </div>
                <ChevronRight size={15} strokeWidth={1.8} />
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
