// The brief composer. Not a chat box, on purpose.
//
// A chat box says "say something and I will reply". This says "commission a
// piece of work": there is a question, a set of choices about how the work is
// done, and a list of what you have already commissioned.
//
// THE CARD IS SEGMENTED, NOT PADDED. Each decision -- what to ask, how
// thorough, where to look, how it comes out -- gets its own band separated by
// a rule, and each band is headed by the question it is actually asking
// ("How thorough should it be?") with the trade-off spelled out underneath.
// The previous version used tiny uppercase micro-labels ("DEPTH", "SOURCES"),
// which name a field without saying what choosing it does. A person
// commissioning four minutes of work should be able to read this screen
// top to bottom and understand every choice without already knowing the app.
//
// The cost of all those choices is stated ONCE, in a sentence, in the footer
// bar -- see the store's summary(). Repeating a budget on every control was
// noise; stating the consequence once, next to the button that spends it, is
// the thing that actually informs the decision.
import React, { useMemo, useState } from "react";
import { ArrowRight, Check, ChevronRight, CornerUpLeft, Loader2, Trash2 } from "lucide-react";
import {
  useResearch, recentRows, DEPTHS, SOURCE_KINDS, LENGTHS, MAX_WORDS,
} from "../../stores/research";
import ModelMenu from "../chat/ModelMenu";

export default function ResearchHome() {
  const {
    question, setQuestion, depth, setDepth, sources, toggleSource,
    advanced, toggleAdvanced, includeDomains, setIncludeDomains,
    excludeDomains, setExcludeDomains, toPlan, planning, openRecent,
    deleteRecent, otherSource, setOtherSource, model, setModel,
    length, setLength, customWords, setCustomWords, maxPages, setMaxPages,
  } = useResearch();
  // Read through the store so the footer and the request body can never
  // disagree about what the run will do -- see targetWords()/summary().
  const summary = useResearch((s) => s.summary());
  // Is there an investigation loaded behind this screen to go back to?
  // Selected as primitives, never as a derived object: a selector that builds
  // a fresh object every call breaks useSyncExternalStore's stability
  // requirement and loops until React throws.
  const resume = useResearch((s) => s.resume);
  const hasOpen = useResearch((s) => s.sections.length > 0 || s.lanes.length > 0);
  const openWriting = useResearch((s) => s.writing);
  const openTitle = useResearch(
    (s) => (s.paper && s.paper.title) || s.question.slice(0, 60) || "your investigation",
  );
  // Which recent investigation (if any) is pending a delete confirmation.
  // Deleting can't happen on a single click -- a recents entry may be the
  // only copy of a finished paper (recents live in localStorage, not the
  // backend DB, per the WHY comment on loadRecents()), so an accidental
  // click must not be able to destroy it.
  const [confirmDelete, setConfirmDelete] = useState(null); // {id, title} | null
  // Select the raw array (stable reference), derive the display rows in render.
  const rawRecents = useResearch((s) => s.recents);
  const recents = useMemo(() => recentRows(rawRecents), [rawRecents]);

  return (
    <div className="research-scroll">
      <div className="research-col wide">
        {/* The other half of the back button. Stepping out of a run has to be
            reversible or it is just a different way of losing your work, and a
            run that is still streaming in the background needs somewhere
            visible to click back into. */}
        {hasOpen && (
          <button className="research-resume" onClick={resume}>
            <CornerUpLeft size={14} strokeWidth={1.9} />
            <span>
              Back to <strong>{openTitle}</strong>
              {openWriting ? " — still writing" : ""}
            </span>
            <ChevronRight size={15} strokeWidth={1.8} />
          </button>
        )}

        <h1 className="research-title">New investigation</h1>
        <p className="research-lede">
          Ask a question. Arthur breaks it into parts, reads sources on your machine, and writes
          a paper with a citation on every claim.
        </p>

        <div className="research-card">
          <div className="research-band first">
            <label className="research-band-q">What do you want to know?</label>
            <textarea
              className="research-question"
              rows={3}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="How do the licenses of the major open-weight model releases differ for commercial use?"
            />
          </div>

          <div className="research-band">
            <div className="research-band-q">How thorough should it be?</div>
            <div className="research-band-help">More depth means more sources and more time.</div>
            <div className="research-depth">
              {Object.entries(DEPTHS).map(([id, d]) => (
                <button
                  key={id}
                  className={`research-depth-opt${depth === id ? " active" : ""}`}
                  onClick={() => setDepth(id)}
                >
                  <span className="research-depth-top">
                    <span className="research-depth-label">{d.label}</span>
                    {/* The tick, not just a fill, marks the choice. On a dark
                        theme a subtle background change alone is easy to miss
                        at a glance, and this control is a commitment. */}
                    {depth === id && <Check size={15} strokeWidth={2.2} />}
                  </span>
                  <span className="research-depth-desc">{d.desc}</span>
                  <span className="research-depth-budget">{d.budget}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="research-band">
            <div className="research-band-q">Where should it look?</div>
            <div className="research-band-help">
              Pick at least one. Academic papers are read in full, not skimmed.
            </div>
            <div className="research-chips">
              {SOURCE_KINDS.map((k) => (
                <button
                  key={k.id}
                  className={`research-chip${sources.includes(k.id) ? " active" : ""}`}
                  onClick={() => toggleSource(k.id)}
                >
                  {sources.includes(k.id) && <Check size={13} strokeWidth={2.4} />}
                  {k.label}
                </button>
              ))}
            </div>
            {/* Shown only once "Other…" is on, so the default composer is not
                carrying an empty field nobody asked for. */}
            {sources.includes("other") && (
              <input
                className="research-inline-input"
                autoFocus
                value={otherSource}
                onChange={(e) => setOtherSource(e.target.value)}
                placeholder="Narrow every search, e.g. randomised controlled trials only, or UK data"
              />
            )}

            <div className="research-grid">
              <div>
                <label className="research-field-label">Model</label>
                {/* Same picker as the chat composer, in controlled mode -- an
                    investigation's model is not a conversation's. */}
                <ModelMenu mode="research" value={model} onChange={setModel} placement="down" />
              </div>

              <div>
                <label className="research-field-label">Paper length</label>
                <select
                  className="research-select"
                  value={length}
                  onChange={(e) => setLength(e.target.value)}
                >
                  {LENGTHS.map((l) => (
                    <option key={l.id} value={l.id}>{l.label}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="research-field-label">Max pages</label>
                <input
                  className="research-select"
                  inputMode="numeric"
                  value={maxPages}
                  onChange={(e) => setMaxPages(e.target.value)}
                  placeholder="No limit"
                />
              </div>
            </div>

            {length === "custom" && (
              <input
                className="research-inline-input"
                autoFocus
                inputMode="numeric"
                value={customWords}
                onChange={(e) => setCustomWords(e.target.value)}
                placeholder={`Target words (up to ${MAX_WORDS.toLocaleString()})`}
              />
            )}

            <button className="research-advanced-toggle" onClick={toggleAdvanced}>
              <ChevronRight size={13} strokeWidth={2} className={advanced ? "rot90" : ""} />
              Advanced
            </button>
            {advanced && (
              <div className="research-advanced">
                <div>
                  <label>Only these domains</label>
                  <input
                    type="text"
                    className="mono"
                    value={includeDomains}
                    onChange={(e) => setIncludeDomains(e.target.value)}
                    placeholder="arxiv.org, *.gov"
                  />
                </div>
                <div>
                  <label>Never these domains</label>
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
          </div>

          <div className="research-card-footer">
            <span className="research-budget">{summary}</span>
            <button className="btn primary" disabled={!question.trim() || planning} onClick={toPlan}>
              {planning
                ? <><Loader2 size={14} className="spin" /> Planning</>
                : <><ArrowRight size={15} strokeWidth={2} /> Start investigation</>}
            </button>
          </div>
        </div>

        {recents.length > 0 && (
          <>
            <div className="research-section-head">Earlier investigations</div>
            {recents.map((r) => (
              <div key={r.id} className="research-recent" onClick={() => openRecent(r.id)}>
                <span className={`research-dot ${r.status}`} />
                <div className="research-recent-body">
                  <div className="research-recent-title">{r.title}</div>
                  <div className="research-recent-meta">{r.meta}</div>
                </div>
                <button
                  className="research-recent-del"
                  title="Delete this investigation"
                  onClick={(e) => { e.stopPropagation(); setConfirmDelete({ id: r.id, title: r.title }); }}
                >
                  <Trash2 size={14} strokeWidth={1.8} />
                </button>
                <ChevronRight size={15} strokeWidth={1.8} />
              </div>
            ))}
          </>
        )}
      </div>

      {confirmDelete && (
        <div className="modal-backdrop" onClick={() => setConfirmDelete(null)}>
          <div className="modal narrow" onClick={(e) => e.stopPropagation()}>
            <h3>Delete this investigation?</h3>
            <p className="modal-sub">
              "{confirmDelete.title}" and everything gathered for it will be removed. This cannot
              be undone.
            </p>
            <div className="research-actions end">
              <button className="btn" onClick={() => setConfirmDelete(null)}>Cancel</button>
              <button
                className="btn danger"
                onClick={() => { deleteRecent(confirmDelete.id); setConfirmDelete(null); }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
