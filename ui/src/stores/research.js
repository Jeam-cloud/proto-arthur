// Research mode state: one investigation at a time, four stages.
//
//   home  -> the brief composer (question, depth, sources)
//   plan  -> the sub-questions, editable, before anything runs
//   run   -> lanes + the evidence panel filling up live
//   report-> the document, still attached to the same evidence
//
// WHY a store and not component state: the run outlives the screen. A user can
// switch to another mode mid-run and come back; lanes and evidence have to
// still be there. Component state would be thrown away on unmount.
//
// WHY events are applied as whole objects: the backend emits complete lane and
// source records rather than deltas (see python/core/events.py), so every
// handler here is an idempotent upsert. Applying the same event twice is
// harmless, which is what makes reconnecting mid-run safe.
import { create } from "zustand";
import {
  planInvestigation, runInvestigation, synthesizeInvestigation,
  findMoreSources, exportPaper,
} from "../api/research";
import { useToasts } from "./toasts";

const STYLE_KEY = "arthur.research.style";

// Each depth now carries a plain-English `desc` alongside the budget, and the
// source COUNT has moved out of the budget line into the composer's summary
// footer. The old label read "~8 sources · about 2 min", which asked the
// reader to decode two units and a tilde before learning anything; the choice
// people are actually making is "how thorough", so the card says that and the
// footer states the consequence in a sentence.
export const DEPTHS = {
  quick: {
    label: "Quick",
    desc: "A short answer with a few sources",
    budget: "About 40 seconds",
    srcs: 4,
  },
  standard: {
    label: "Standard",
    desc: "A full paper, balanced coverage",
    budget: "About 2 minutes",
    srcs: 8,
  },
  exhaustive: {
    label: "Exhaustive",
    desc: "Wide reading, every claim checked twice",
    budget: "About 6 minutes",
    srcs: 20,
  },
};

export const SOURCE_KINDS = [
  { id: "web", label: "Web" },
  { id: "academic", label: "Academic papers" },
  { id: "news", label: "News" },
  { id: "docs", label: "Docs" },
  // "Other" is not a fifth search index -- there is no provider behind it.
  // Selecting it opens a free-text box whose contents are appended to every
  // sub-question as a qualifier ("...clinical trials only", "...UK law").
  // WHY steer the QUERIES rather than invent a source type: the providers in
  // research/providers.py are a fixed set, so a made-up kind would be silently
  // dropped server-side and the user would never learn their input did
  // nothing. Folding it into the query is a thing that visibly works.
  { id: "other", label: "Other…", freeform: true },
];

// Length presets. Words, not pages, because words are what the writer can
// actually aim at -- pages are converted from words downstream at a fixed
// 275 words/page (see engine.WORDS_PER_PAGE).
//
// The label carries the number rather than hiding it behind "Brief": someone
// choosing a length is choosing a size, and "Short" alone does not say what
// size. There is no "let the model decide" option any more -- an unbounded
// local model writes whatever it writes, which was the least predictable
// outcome sitting in the default slot.
export const LENGTHS = [
  { id: "brief", label: "Short — about 900 words", words: 900 },
  { id: "standard", label: "Medium — about 1,800 words", words: 1800 },
  { id: "extended", label: "Long — about 3,400 words", words: 3400 },
  { id: "custom", label: "Custom…", words: -1 },
];

// Hard ceiling on the custom word box. Past this a local model pads rather
// than writes, and the run time stops buying anything.
export const MAX_WORDS = 6000;
export const MAX_PAGES = 40;

const RECENTS_KEY = "arthur.research.recents";

// Recents live in localStorage rather than the backend DB on purpose: a report
// is a document, and the next step for it is the workspace folder (Export), not
// a second database. This keeps the list useful without inventing a schema we
// would have to migrate later.
function loadRecents() {
  try {
    return JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveRecents(list) {
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(list.slice(0, 12)));
  } catch {
    /* quota or private mode — recents are a convenience, never a blocker */
  }
}

// Keeps a numeric text input numeric and inside the range the backend will
// accept. Empty string is preserved rather than coerced to 0 so the field can
// genuinely be blank ("no cap") instead of showing a 0 the user did not type.
function clampNum(v, max) {
  const digits = String(v).replace(/\D/g, "");
  if (!digits) return "";
  return String(Math.min(Number(digits), max));
}

function relativeDate(iso) {
  const then = new Date(iso);
  const days = Math.floor((Date.now() - then.getTime()) / 86_400_000);
  if (days <= 0) return `Today, ${then.toTimeString().slice(0, 5)}`;
  if (days === 1) return "Yesterday";
  return `${days} days ago`;
}

const BLANK = {
  stage: "home",
  question: "",
  depth: "standard",
  sources: ["web", "academic"],
  otherSource: "",     // free text behind the "Other…" chip
  model: "",           // "" = follow Settings -> Models for research mode
  length: "standard",  // one of LENGTHS[].id
  customWords: "",     // only meaningful when length === "custom"
  maxPages: "",        // "" = no page cap
  advanced: false,
  includeDomains: "",
  excludeDomains: "",
  subs: [],
  lanes: [],
  gapNote: "",
  evidence: [],
  // The paper. `sections` fills in progressively as each one is written;
  // title/abstract arrive last because the abstract is written from the
  // finished body (see research/engine.py._write_paper).
  paper: null,        // {title, abstract, question}
  sections: [],
  elapsed: 0,
  planning: false,
  fault: null, // tavily | offline | zero | failed
  faultDetail: "",
  // What the backend is doing between "search finished" and "report appears"
  // (contradiction check, then writing) -- see events.STATUS in
  // research/engine.py. Without this the run screen sat at 100% with no
  // feedback for however long synthesis took, which read as a hang even when
  // it wasn't one.
  statusText: "",
  // True when Stop was pressed (or the stream died) AFTER search finished but
  // BEFORE a report was written: the lanes are all done, sources are sitting
  // in the evidence panel, but there's no report yet and re-running the whole
  // search would throw that work away. See writeReportNow().
  stopped: false,
  writing: false,
  finding: false,      // a "find more sources" search is running
  newSourceIds: [],    // arrived since the paper was written -> offer a rewrite
  modelWarning: "",    // set by /research/plan when the model looks too small
  // Identifies the recents entry for the CURRENT investigation, set the
  // moment a run starts (see run()/persistRun()) so progress can be saved
  // incrementally instead of only at the very end. null before anything has
  // been commissioned and after newInvestigation() resets the screen.
  runId: null,
};

export const useResearch = create((set, get) => ({
  ...BLANK,
  recents: loadRecents(),
  degraded: false, // Docker off: snippets only, not a failure
  // Citation style persists across investigations: someone writing in APA is
  // writing in APA next week too, and re-picking it every time is friction
  // for no reason.
  style: localStorage.getItem(STYLE_KEY) || "apa",
  customStyle: "",
  // view-only bits
  evFilter: "all",
  usedOnly: false,
  expandedEv: null,
  hoverCite: null,     // source id highlighted from EITHER direction
  focusSource: null,   // source id the paper should scroll to (sidebar -> paper)
  cursorBlock: null,
  showEvidence: true,
  explain: null,

  // ---------- composer ----------
  setQuestion: (question) => set({ question }),
  setDepth: (depth) => set({ depth }),
  toggleSource: (id) =>
    set((s) => ({
      sources: s.sources.includes(id) ? s.sources.filter((x) => x !== id) : [...s.sources, id],
    })),
  toggleAdvanced: () => set((s) => ({ advanced: !s.advanced })),
  setIncludeDomains: (v) => set({ includeDomains: v }),
  setExcludeDomains: (v) => set({ excludeDomains: v }),
  setOtherSource: (v) => set({ otherSource: v.slice(0, 200) }),
  setModel: (model) => set({ model }),
  setLength: (length) => set({ length }),
  // Digits only, clamped on the way IN rather than on submit. Letting someone
  // type 99999 and rejecting it at the end wastes their time; the box simply
  // will not hold a number the backend would refuse (see ResearchRunRequest).
  setCustomWords: (v) => set({ customWords: clampNum(v, MAX_WORDS) }),
  setMaxPages: (v) => set({ maxPages: clampNum(v, MAX_PAGES) }),

  // The single word target the run will use, derived from whichever controls
  // are set. Kept here, not in the component, so the composer footer and the
  // request body can never show different numbers.
  targetWords() {
    const s = get();
    const preset = LENGTHS.find((l) => l.id === s.length);
    const words = s.length === "custom" ? Number(s.customWords || 0) : (preset ? preset.words : 0);
    const pages = Number(s.maxPages || 0);
    const fromPages = pages ? Math.max(275, (pages - 1) * 275) : 0;
    if (words > 0 && fromPages) return Math.min(words, fromPages);
    return words > 0 ? words : fromPages;
  },

  // The composer footer, as one sentence in plain English.
  //
  // WHY a sentence and not a row of numbers: the old footer said
  // "~8 sources · about 2 min", which is three units and a tilde to decode.
  // This states what will happen and, more importantly, ends on the fact that
  // matters most in a local-first app and that no other screen ever says out
  // loud -- none of this leaves the machine.
  summary() {
    const s = get();
    const srcs = (DEPTHS[s.depth] || DEPTHS.standard).srcs;
    const words = get().targetWords();
    const length = words
      ? `write about ${words.toLocaleString()} words`
      : "write a paper";
    const pages = Number(s.maxPages || 0);
    const cap = pages ? `, capped at ${pages} page${pages === 1 ? "" : "s"}` : "";
    return `Arthur will read about ${srcs} sources and ${length}${cap}. Nothing leaves this computer.`;
  },

  // ---------- plan ----------
  async toPlan() {
    const { question, depth } = get();
    if (!question.trim()) return;
    set({ planning: true, fault: null });
    try {
      const res = await planInvestigation({ question: question.trim(), depth, model: get().model });
      set({
        stage: "plan",
        planning: false,
        subs: (res.sub_questions || []).map((text, i) => ({ id: `sq${i}`, text })),
        // Shown on the plan screen, which is the last moment the model can be
        // changed for free -- after this a run costs minutes.
        modelWarning: res.warning || "",
      });
    } catch (e) {
      set({ planning: false });
      // A missing key or a dead backend is a screen, not a toast: the user
      // cannot proceed and needs the fix in front of them.
      if (e.code === "backend_unreachable") set({ fault: "offline" });
      else useToasts.getState().push(e.message, "error");
    }
  },
  editSub: (id, text) =>
    set((s) => ({ subs: s.subs.map((q) => (q.id === id ? { ...q, text } : q)) })),
  delSub: (id) => set((s) => ({ subs: s.subs.filter((q) => q.id !== id) })),
  addSub: () =>
    set((s) => ({
      subs: [...s.subs, { id: `sq${Math.random().toString(36).slice(2, 6)}`, text: "" }],
    })),
  backHome: () => set({ stage: "home" }),

  // Leave the current investigation on screen and go back to the composer,
  // WITHOUT throwing it away.
  //
  // WHY this is separate from newInvestigation(): the only way out of a run
  // used to be "New investigation", which aborts the stream and clears
  // everything. That makes going back to look at the composer a destructive
  // act, so people avoid pressing it and end up stuck on a screen with no
  // exit. Here the run keeps streaming in the background and the finished
  // paper stays in recents, so returning is free and reversible -- which is
  // the whole reason a back button can be pressed without thinking.
  toHome: () => {
    const s = get();
    if (s.runId && (s.evidence.length || s.sections.length)) s.persistRun(
      s.sections.length ? "done" : "partial",
    );
    set({ stage: "home" });
  },

  // Reopen whatever is currently loaded. Paired with toHome() so the composer
  // can offer a way BACK into the investigation you just stepped out of.
  resume: () => set((s) => ({
    stage: s.sections.length || s.paper ? "report" : (s.lanes.length ? "run" : "home"),
  })),

  // ---------- run ----------
  async run() {
    const s = get();
    const subs = s.subs.map((q) => q.text.trim()).filter(Boolean);
    if (!subs.length) return;

    stopTimer();
    const controller = new AbortController();
    abort = controller;

    // Assigned here, not on completion: this is what lets the investigation
    // be saved to Recent investigations the moment it starts (see
    // persistRun() below) rather than only once a report is finished. Before
    // this, a crash, a force-quit, or navigating away mid-run lost the whole
    // thing -- there was nothing in recents to come back to.
    const runId = `r${Date.now().toString(36)}`;

    set({
      stage: "run",
      runId,
      lanes: subs.map((text, i) => ({
        id: `sq${i}`, text, state: "queued", read: 0, of: 0, srcs: 0, pass: 1,
      })),
      evidence: [],
      paper: null,
      sections: [],
      newSourceIds: [],
      gapNote: "",
      elapsed: 0,
      fault: null,
      statusText: "",
      stopped: false,
    });
    get().persistRun("running");
    timer = setInterval(() => set((st) => ({ elapsed: st.elapsed + 1 })), 1000);

    const splitDomains = (v) =>
      v.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean).slice(0, 20);

    try {
      // "other" is a UI-only chip: it carries no provider, so it is stripped
      // from the kinds list and its text is folded into each sub-question
      // instead. Sending it as a kind would have the server silently ignore
      // it -- see SOURCE_KINDS.
      const kinds = s.sources.filter((k) => k !== "other");
      const qualifier = s.sources.includes("other") ? s.otherSource.trim() : "";
      const queries = qualifier ? subs.map((q) => `${q} (${qualifier})`) : subs;

      const stream = runInvestigation(
        {
          question: s.question.trim(),
          sub_questions: queries,
          depth: s.depth,
          sources: kinds.length ? kinds : ["web"],
          model: s.model,
          target_words: get().targetWords(),
          max_pages: Number(s.maxPages || 0),
          include_domains: splitDomains(s.includeDomains),
          exclude_domains: splitDomains(s.excludeDomains),
        },
        { signal: controller.signal },
      );
      for await (const { event, data } of stream) {
        get().applyEvent(event, data);
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        set({ fault: "failed", faultDetail: e.message || "The search provider stopped responding." });
      }
    } finally {
      // The stream is over however it ended -- normally, aborted, or thrown.
      // Clearing `writing` here rather than only on the `done` event means a
      // dropped connection cannot leave the toolbar saying "Arthur is
      // writing…" forever over a paper nothing is coming for.
      set({ writing: false });
      stopTimer();
    }
  },

  applyEvent(event, data) {
    switch (event) {
      case "research_lane":
        set((s) => ({
          lanes: s.lanes.map((l) => (l.id === data.id ? { ...l, ...data } : l)),
        }));
        // Checkpoint recents when a lane SETTLES (done/thin/blocked), not on
        // every state change -- frequent enough that an interrupted run is
        // never far from its last save, rare enough not to hammer
        // localStorage on every "searching" -> "reading" tick.
        if (["done", "thin", "blocked"].includes(data.state)) get().persistRun("running");
        break;

      case "research_source":
        set((s) => {
          const i = s.evidence.findIndex((e) => e.id === data.id);
          if (i === -1) {
            // A source arriving after the paper exists came from "find more
            // sources" -- flag it so the UI can offer a rewrite rather than
            // silently leaving it uncited.
            const isNew = !!s.paper && data.added_manually;
            return {
              evidence: [...s.evidence, data],
              newSourceIds: isNew ? [...s.newSourceIds, data.id] : s.newSourceIds,
            };
          }
          const next = s.evidence.slice();
          next[i] = { ...next[i], ...data };
          return { evidence: next };
        });
        break;

      case "research_gap":
        set({ gapNote: data.note || "" });
        break;

      case "research_section":
        // Sections stream in as each is written. Upsert by id and keep them
        // ordered by `order`, never by arrival -- the paper must read
        // correctly even if a later section finishes first.
        set((s) => {
          const rest = s.sections.filter((x) => x.id !== data.id);
          return {
            sections: [...rest, data].sort((a, b) => a.order - b.order),
            stage: "report",
          };
        });
        break;

      case "research_paper":
        // Two different emits arrive on this event and they must be handled
        // differently. The engine now sends a PROVISIONAL paper (title only,
        // `sections: []`) before writing starts, so a run that gets cut short
        // still has a titled document instead of "Untitled paper"; the real
        // one arrives at the end carrying every section. Blindly assigning
        // `data.sections` would let the provisional emit wipe the sections
        // that streamed in ahead of it, so an empty list means "keep what we
        // have" rather than "the paper has no sections".
        set((s) => ({
          paper: {
            title: data.title,
            // Same rule for the abstract: the provisional emit has none, and
            // clearing a real one would be a visible regression on screen.
            abstract: data.abstract || (s.paper && s.paper.abstract) || "",
            question: data.question,
          },
          sections: (data.sections || []).length
            ? data.sections.slice().sort((a, b) => a.order - b.order)
            : s.sections,
          stage: "report",
        }));
        break;

      case "error":
        set({ writing: false });
        if (data.code === "tavily_missing") set({ fault: "tavily" });
        else if (data.code === "zero_results") set({ fault: "zero" });
        else set({ fault: "failed", faultDetail: data.message || "" });
        break;

      case "done": {
        // A find-more-sources stream also ends in `done`, but it wrote no
        // paper and must not create/update a recents entry.
        const s = get();
        if (!s.sections.length) break;
        set({ stage: "report", writing: false, statusText: "" });
        get().persistRun(get().lanes.some((l) => l.state === "blocked") ? "partial" : "done");
        break;
      }

      case "status":
        // Surfaced verbatim on the run screen (see ResearchRun.jsx) so "all
        // lanes done, still no report" reads as "comparing sources / writing"
        // instead of looking stuck.
        //
        // `phase` does the same job for the REPORT screen, which had no such
        // signal at all: the moment the first section streamed in, the stage
        // flipped to "report" and the toolbar showed a source count, which
        // reads as a finished paper. A run that was still writing for another
        // two minutes therefore looked like one that had produced a single
        // paragraph and stopped -- the "so many sources, nothing generated"
        // symptom. `writing` keeps the toolbar honest until the paper lands.
        set({
          statusText: data.text || "",
          ...(data.phase === "writing" ? { writing: true } : {}),
          ...(data.phase === "done" ? { writing: false } : {}),
        });
        break;

      default:
        break; // unknown events are informational only
    }
  },

  stop() {
    if (abort) abort.abort();
    abort = null;
    stopTimer();
    set((s) => {
      if (s.sections.length) {
        // Some of the paper already exists -- a partial paper is still a real
        // one, and the sections that finished are complete in themselves.
        useToasts.getState().push("Stopped. Everything gathered so far has been kept.", "info");
        // `writing` is cleared here as well as in the stream's finally block.
        // Stop is now reachable from the paper toolbar mid-write, and the
        // button that stops the write must not leave the toolbar still saying
        // "Arthur is writing" for however long the abort takes to unwind.
        return { stage: "report", writing: false, statusText: "" };
      }
      // Search finished (or was cut short) but nothing was written yet. Stay
      // on the run screen -- the lanes and evidence are still useful -- and
      // offer to write the report from what's already there instead of
      // leaving the screen looking frozen with a dead Stop button.
      useToasts.getState().push("Stopped before the paper was written. Nothing gathered was lost.", "info");
      return { stopped: true, statusText: "" };
    });
    // Save whatever exists NOW. Without this, stopping mid-run and then
    // navigating home (or the app closing) lost every source that had been
    // gathered, because only the final `done` event used to write to
    // recents -- exactly the "I have to start a completely new one" bug.
    if (get().evidence.length) get().persistRun("partial");
  },

  // Writes (or rewrites) the paper from the sources currently held. Used both
  // by the post-stop "Write the paper" button and by the "rewrite to include
  // new sources" action after a find-more search.
  async writeReportNow() {
    const s = get();
    if (s.writing || !s.evidence.length) return;
    stopTimer();
    const controller = new AbortController();
    abort = controller;
    set({
      writing: true, stopped: false, statusText: "Writing the paper", elapsed: 0,
      sections: [], paper: null, newSourceIds: [],
    });
    timer = setInterval(() => set((st) => ({ elapsed: st.elapsed + 1 })), 1000);

    try {
      const stream = synthesizeInvestigation(
        {
          question: s.question.trim(),
          sources: s.evidence,
          sub_questions: s.subs.map((q) => q.text).filter(Boolean),
          model: s.model,
          target_words: get().targetWords(),
          max_pages: Number(s.maxPages || 0),
        },
        { signal: controller.signal },
      );
      for await (const { event, data } of stream) {
        get().applyEvent(event, data);
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        useToasts.getState().push(e.message || "Writing the paper failed.", "error");
        set({ stopped: true });
      }
    } finally {
      set({ writing: false });
      stopTimer();
    }
  },

  // ---------- find more sources ----------
  async findMore(query) {
    const s = get();
    if (s.finding || !query.trim()) return;
    const controller = new AbortController();
    findAbort = controller;
    set({ finding: true, statusText: "" });
    const before = s.evidence.length;
    try {
      const stream = findMoreSources(
        { query: query.trim(), sources: s.evidence, kinds: s.sources },
        { signal: controller.signal },
      );
      for await (const { event, data } of stream) {
        get().applyEvent(event, data);
      }
      const added = get().evidence.length - before;
      useToasts.getState().push(
        added ? `${added} new source${added === 1 ? "" : "s"} added.` : "No new sources found.",
        added ? "success" : "info",
      );
    } catch (e) {
      if (e.name !== "AbortError") useToasts.getState().push(e.message || "Search failed.", "error");
    } finally {
      set({ finding: false, statusText: "" });
    }
  },

  // ---------- citation style ----------
  setStyle(style) {
    // Switching style re-renders citations instantly and offline -- nothing
    // regenerates, because the model never wrote the citations in the first
    // place (see lib/citeFormat.js).
    localStorage.setItem(STYLE_KEY, style);
    set({ style });
  },
  setCustomStyle: (customStyle) => set({ customStyle }),

  // ---------- copy ----------
  // Puts the whole paper on the clipboard in BOTH rich and plain form, so it
  // pastes as a formatted document into Word/Docs and as clean text into a
  // plain editor. Citations are rendered into the prose exactly as they are on
  // export -- pasting a paper full of raw [3] markers would defeat the point.
  async copyPaper() {
    const s = get();
    if (!s.sections.length) {
      useToasts.getState().push("Nothing to copy yet.", "info");
      return;
    }
    const state = { paper: s.paper, sections: s.sections, evidence: s.evidence, style: s.style };
    const { paperToHtml, paperToText } = await import("../lib/paperDoc");
    const { copyToClipboard } = await import("../lib/clipboard");
    try {
      // Which fidelity landed is reported honestly: losing the formatting is a
      // far smaller failure than losing the copy, but the user should know
      // which one they got before they paste.
      const how = await copyToClipboard({ text: paperToText(state), html: paperToHtml(state) });
      useToasts.getState().push(
        how === "rich" ? "Paper copied, formatting included." : "Paper copied as plain text.",
        "success",
      );
    } catch (e) {
      useToasts.getState().push(e.message || "Copy failed.", "error");
    }
  },

  // ---------- export ----------
  async exportAs(fmt) {
    const s = get();
    // `s.paper` can be null even though sections exist -- e.g. writing was
    // interrupted before the title/abstract step ran. The doc still shows a
    // "Untitled paper" fallback on screen (see ResearchPaper.jsx), so Export
    // must use that SAME fallback rather than silently doing nothing, which
    // is what made Export look broken: the button just quietly returned here
    // with no toast and no network call.
    if (!s.sections.length) {
      useToasts.getState().push("Nothing to export yet.", "info");
      return;
    }
    const paper = s.paper || { title: s.question.slice(0, 90) || "Untitled paper", abstract: "", question: s.question };
    try {
      const { blob, filename } = await exportPaper({
        paper: { ...paper, sections: s.sections },
        sources: s.evidence,
        fmt,
        style: s.style,
        custom_style: s.customStyle,
        // Only read server-side when the style is "custom" -- that is the one
        // citation path that asks a model to format references. Sending the
        // investigation's chosen model keeps that consistent with the rest of
        // the run instead of silently falling back to the global default.
        model: s.model,
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
      useToasts.getState().push(`Exported as ${fmt.toUpperCase()}.`, "success");
    } catch (e) {
      // The backend renderer is the good one (real .docx tables, tested
      // bibliography), so it is always tried first. But a person looking at a
      // finished paper they cannot get out of the app is stuck, and "Export
      // failed" is not an answer. When the failure was the CONNECTION rather
      // than the document, render it here in the browser instead.
      if (e.code === "export_unreachable") {
        await get().exportLocally(fmt, e.message);
        return;
      }
      useToasts.getState().push(e.message || "Export failed.", "error");
    }
  },

  // Browser-only renderer. Needs no backend, no Python, no libraries -- see
  // lib/paperDoc.js for why each format is produced the way it is.
  async exportLocally(fmt, reason) {
    const s = get();
    const paper = s.paper || { title: s.question.slice(0, 90) || "Untitled paper", abstract: "", question: s.question };
    const state = { paper, sections: s.sections, evidence: s.evidence, style: s.style };
    const { paperToDocBlob, printPaper } = await import("../lib/paperDoc");
    try {
      if (fmt === "pdf") {
        printPaper(state);
        useToasts.getState().push(
          `${reason} Opened the print dialog instead — choose "Save as PDF".`, "info", 9000,
        );
        return;
      }
      const blob = paperToDocBlob(state);
      const safe = (paper.title || "paper").replace(/[^\w \-]/g, "").trim().replace(/\s+/g, "-") || "paper";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${safe}.doc`;
      a.click();
      URL.revokeObjectURL(a.href);
      useToasts.getState().push(
        `${reason} Saved a Word-readable .doc from the browser instead.`, "info", 9000,
      );
    } catch (err) {
      useToasts.getState().push(err.message || "Export failed both ways.", "error");
    }
  },

  newInvestigation: () => {
    if (abort) abort.abort();
    abort = null;
    stopTimer();
    const s = get();
    // Model and length carry over. They are preferences about how this person
    // works, not properties of one investigation -- someone who picked a
    // bigger model and a 2000-word target wants that next time too, the same
    // reason citation style persists. Everything else resets.
    set({
      ...BLANK,
      recents: s.recents,
      degraded: s.degraded,
      model: s.model,
      length: s.length,
      customWords: s.customWords,
      maxPages: s.maxPages,
    });
  },

  // Upserts the CURRENT investigation into recents by `runId`, called at
  // every meaningful checkpoint (run start, a lane settling, stop, done) --
  // see run()/stop()/applyEvent() above. Keyed by id rather than title (the
  // old approach) so the SAME investigation is updated in place instead of
  // spawning a fresh entry every time it progresses.
  persistRun(status) {
    const s = get();
    if (!s.runId) return;
    const prior = s.recents.find((r) => r.id === s.runId);
    const entry = {
      id: s.runId,
      title: (s.paper && s.paper.title) || s.question.slice(0, 90) || "Untitled investigation",
      at: (prior && prior.at) || new Date().toISOString(),
      sources: s.evidence.length,
      independent: new Set(s.evidence.map((e) => e.publisher || e.domain).filter(Boolean)).size,
      status,
      snapshot: {
        question: s.question, paper: s.paper, sections: s.sections,
        evidence: s.evidence, subs: s.subs.map((q) => q.text),
      },
    };
    const recents = [entry, ...s.recents.filter((r) => r.id !== entry.id)];
    saveRecents(recents);
    set({ recents });
  },

  deleteRecent: (id) => {
    const recents = get().recents.filter((r) => r.id !== id);
    saveRecents(recents);
    set({ recents });
  },

  openRecent: (id) => {
    if (abort) abort.abort();
    abort = null;
    stopTimer();
    const r = get().recents.find((x) => x.id === id);
    if (!r || !r.snapshot) return;
    // An interrupted investigation (stopped or crashed before a paper was
    // written) has evidence but no paper/sections. Sending it to "report"
    // would show a permanent "Writing the paper…" spinner (see
    // ResearchPaper's `!paper && !sections.length` guard) since nothing is
    // ever going to arrive. Route it to "run" instead, where the gathered
    // evidence and the "Write the report" button are actually usable.
    const hasPaper = !!(r.snapshot.paper || (r.snapshot.sections || []).length);
    set({
      stage: hasPaper ? "report" : "run",
      runId: r.id,
      question: r.snapshot.question,
      paper: r.snapshot.paper || null,
      sections: r.snapshot.sections || [],
      evidence: r.snapshot.evidence || [],
      subs: (r.snapshot.subs || []).map((text, i) => ({ id: `sq${i}`, text })),
      lanes: [],
      fault: null,
      cursorBlock: null,
      stopped: !hasPaper,
      writing: false,
      statusText: "",
      newSourceIds: [],
    });
  },

  // ---------- report interactions ----------
  setEvFilter: (evFilter) => set({ evFilter }),
  toggleUsedOnly: () => set((s) => ({ usedOnly: !s.usedOnly })),
  toggleEv: (id) => set((s) => ({ expandedEv: s.expandedEv === id ? null : id })),
  toggleEvidencePanel: () => set((s) => ({ showEvidence: !s.showEvidence })),
  setHoverCite: (id) => set((s) => (s.hoverCite === id ? {} : { hoverCite: id })),
  setCursor: (cursorBlock) => set({ cursorBlock }),
  setExplain: (explain) => set({ explain }),

  // Sidebar -> paper. Sets both the highlight and a scroll target; the paper
  // clears focusSource once it has scrolled, so the same source can be
  // clicked again later and still scroll.
  focusOnSource: (id) => set({ hoverCite: id, focusSource: id }),
  clearFocusSource: () => set({ focusSource: null }),

  // Paragraph edits are local edits to a local document -- nothing is sent
  // anywhere. There is no authorship flag to clear: Arthur wrote the whole
  // paper, so tracking which paragraphs it wrote would mark all of them, and
  // an "accept" step over prose the user can already edit does no work.
  editParagraph: (sectionId, paraId, text) =>
    set((s) => ({
      sections: s.sections.map((sec) =>
        sec.id !== sectionId ? sec : {
          ...sec,
          paragraphs: sec.paragraphs.map((p) => (p.id === paraId ? { ...p, text } : p)),
        }),
    })),
  deleteParagraph: (sectionId, paraId) =>
    set((s) => ({
      sections: s.sections.map((sec) =>
        sec.id !== sectionId ? sec : {
          ...sec, paragraphs: sec.paragraphs.filter((p) => p.id !== paraId),
        }),
    })),
  editHeading: (sectionId, heading) =>
    set((s) => ({
      sections: s.sections.map((sec) => (sec.id === sectionId ? { ...sec, heading } : sec)),
    })),
  editTitle: (title) => set((s) => ({ paper: { ...s.paper, title } })),
  editAbstract: (abstract) => set((s) => ({ paper: { ...s.paper, abstract } })),

  setDegraded: (degraded) => set({ degraded }),
  clearFault: () => set({ fault: null, faultDetail: "" }),
  dismissDegraded: () => set({ degraded: false }),

}));

// Derived data lives OUTSIDE the store as a plain function, and callers wrap it
// in useMemo over `recents`.
//
// WHY this is not a store method: zustand selectors feed React's
// useSyncExternalStore, which requires the snapshot to be referentially stable
// between renders. A selector like `(s) => s.recentRows()` builds a fresh array
// every call, so React sees a "changed" snapshot on every render and loops
// until it throws "Maximum update depth exceeded". Selectors must return
// something already stored, never something freshly constructed.
export function recentRows(recents) {
  return recents.map((r) => ({
    ...r,
    meta: `${relativeDate(r.at)} · ${r.sources} sources${r.independent ? ` · ${r.independent} independent` : ""}`,
  }));
}

// Module-scope handles rather than store fields: an AbortController and an
// interval id are not state, and putting them in the store would make every
// subscriber re-render when a run starts.
let abort = null;
// Find-more runs on its own controller: it can be fired while a paper is
// already on screen, and cancelling it must not cancel anything else.
let findAbort = null;
let timer = null;

function stopTimer() {
  if (timer) clearInterval(timer);
  timer = null;
}

export function cancelFindMore() {
  if (findAbort) findAbort.abort();
  findAbort = null;
}
