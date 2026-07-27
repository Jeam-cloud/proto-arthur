// The brief composer. Not a chat box, on purpose.
//
// A chat box says "say something and I will reply". This says "commission a
// piece of work": there is a question field, a budget you pick before starting,
// and a list of what you have already commissioned. Every control here changes
// what the run will actually cost, which is why each one shows its own budget
// rather than hiding it behind a word like "Exhaustive".
import React, { useMemo, useState } from "react";
import { ChevronRight, Loader2, Trash2 } from "lucide-react";
import {
  useResearch, recentRows, DEPTHS, SOURCE_KINDS, LENGTHS, MAX_WORDS, MAX_PAGES,
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
  // disagree about the number -- see targetWords() for the precedence rule.
  const targetWords = useResearch((s) => s.targetWords());
  // Which recent investigation (if any) is pending a delete confirmation.
  // Deleting can't happen on a single click -- a recents entry may be the
  // only copy of a finished paper (recents live in localStorage, not the
  // backend DB, per the WHY comment on loadRecents() below), so an accidental
  // click must not be able to destroy it.
  const [confirmDelete, setConfirmDelete] = useState(null); // {id, title} | null
  // Select the raw array (stable reference), derive the display rows in render.
  const rawRecents = useResearch((s) => s.recents);
  const recents = useMemo(() => recentRows(rawRecents), [rawRecents]);

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
          {/* The box appears only once "Other…" is on, so the default composer
              is not carrying an empty field nobody asked for. */}
          {sources.includes("other") && (
            <input
              className="research-other-input"
              autoFocus
              value={otherSource}
              onChange={(e) => setOtherSource(e.target.value)}
              placeholder="Narrow every search, e.g. randomised controlled trials only, or UK data"
            />
          )}

          <div className="research-grid spaced">
            <div>
              <label className="micro-label">Model</label>
              {/* Same picker as the chat composer, in controlled mode -- an
                  investigation's model is not a conversation's. */}
              <ModelMenu mode="research" value={model} onChange={setModel} placement="down" />
            </div>

            <div>
              <label className="micro-label">Length</label>
              <select
                className="research-select"
                value={length}
                onChange={(e) => setLength(e.target.value)}
              >
                {LENGTHS.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.words > 0 ? `${l.label} · ~${l.words} words` : l.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="micro-label">Max pages</label>
              <input
                className="research-select"
                inputMode="numeric"
                value={maxPages}
                onChange={(e) => setMaxPages(e.target.value)}
                placeholder={`No cap · max ${MAX_PAGES}`}
              />
            </div>
          </div>

          {length === "custom" && (
            <input
              className="research-other-input"
              autoFocus
              inputMode="numeric"
              value={customWords}
              onChange={(e) => setCustomWords(e.target.value)}
              placeholder={`Target words (up to ${MAX_WORDS})`}
            />
          )}

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
            {/* Every control's real cost in one line, including the length
                target -- the composer's whole premise is that you see the
                budget before you spend it, and length is part of the budget. */}
            <span className="research-budget">
              {DEPTHS[depth].budget}
              {targetWords > 0 && ` · ~${targetWords} words (~${Math.max(1, Math.round(targetWords / 275))} pages)`}
            </span>
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
