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

export const DEPTHS = {
  quick: { label: "Quick", budget: "~4 sources · about 40 sec" },
  standard: { label: "Standard", budget: "~8 sources · about 2 min" },
  exhaustive: { label: "Exhaustive", budget: "~20 sources · about 6 min" },
};

export const SOURCE_KINDS = [
  { id: "web", label: "Web" },
  { id: "academic", label: "Academic papers" },
  { id: "news", label: "News" },
  { id: "docs", label: "Docs" },
];

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

  // ---------- plan ----------
  async toPlan() {
    const { question, depth } = get();
    if (!question.trim()) return;
    set({ planning: true, fault: null });
    try {
      const res = await planInvestigation({ question: question.trim(), depth });
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

  // ---------- run ----------
  async run() {
    const s = get();
    const subs = s.subs.map((q) => q.text.trim()).filter(Boolean);
    if (!subs.length) return;

    stopTimer();
    const controller = new AbortController();
    abort = controller;

    set({
      stage: "run",
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
    timer = setInterval(() => set((st) => ({ elapsed: st.elapsed + 1 })), 1000);

    const splitDomains = (v) =>
      v.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean).slice(0, 20);

    try {
      const stream = runInvestigation(
        {
          question: s.question.trim(),
          sub_questions: subs,
          depth: s.depth,
          sources: s.sources,
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
      stopTimer();
    }
  },

  applyEvent(event, data) {
    switch (event) {
      case "research_lane":
        set((s) => ({
          lanes: s.lanes.map((l) => (l.id === data.id ? { ...l, ...data } : l)),
        }));
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
        set({
          paper: { title: data.title, abstract: data.abstract, question: data.question },
          sections: (data.sections || []).slice().sort((a, b) => a.order - b.order),
          stage: "report",
        });
        break;

      case "error":
        if (data.code === "tavily_missing") set({ fault: "tavily" });
        else if (data.code === "zero_results") set({ fault: "zero" });
        else set({ fault: "failed", faultDetail: data.message || "" });
        break;

      case "done":
        set((s) => {
          // A find-more-sources stream also ends in `done`, but it wrote no
          // paper and must not create a recents entry.
          if (!s.sections.length) return { stage: s.stage };
          const entry = {
            id: `r${Date.now().toString(36)}`,
            title: (s.paper && s.paper.title) || s.question.slice(0, 90),
            at: new Date().toISOString(),
            sources: data.sources || s.evidence.length,
            independent: data.independent || 0,
            status: s.lanes.some((l) => l.state === "blocked") ? "partial" : "done",
            snapshot: {
              question: s.question, paper: s.paper, sections: s.sections,
              evidence: s.evidence, subs: s.subs.map((q) => q.text),
            },
          };
          const recents = [entry, ...s.recents.filter((r) => r.title !== entry.title)];
          saveRecents(recents);
          return { stage: "report", recents };
        });
        break;

      case "status":
        // Surfaced verbatim on the run screen (see ResearchRun.jsx) so "all
        // lanes done, still no report" reads as "comparing sources / writing"
        // instead of looking stuck.
        set({ statusText: data.text || "" });
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
        return { stage: "report" };
      }
      // Search finished (or was cut short) but nothing was written yet. Stay
      // on the run screen -- the lanes and evidence are still useful -- and
      // offer to write the report from what's already there instead of
      // leaving the screen looking frozen with a dead Stop button.
      useToasts.getState().push("Stopped before the paper was written. Nothing gathered was lost.", "info");
      return { stopped: true, statusText: "" };
    });
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

  // ---------- export ----------
  async exportAs(fmt) {
    const s = get();
    if (!s.paper || !s.sections.length) return;
    try {
      const { blob, filename } = await exportPaper({
        paper: { ...s.paper, sections: s.sections },
        sources: s.evidence,
        fmt,
        style: s.style,
        custom_style: s.customStyle,
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
      useToasts.getState().push(`Exported as ${fmt.toUpperCase()}.`, "success");
    } catch (e) {
      useToasts.getState().push(e.message || "Export failed.", "error");
    }
  },

  newInvestigation: () => {
    if (abort) abort.abort();
    abort = null;
    stopTimer();
    set({ ...BLANK, recents: get().recents, degraded: get().degraded });
  },

  openRecent: (id) => {
    if (abort) abort.abort();
    abort = null;
    stopTimer();
    const r = get().recents.find((x) => x.id === id);
    if (!r || !r.snapshot) return;
    set({
      stage: "report",
      question: r.snapshot.question,
      paper: r.snapshot.paper || null,
      sections: r.snapshot.sections || [],
      evidence: r.snapshot.evidence || [],
      subs: (r.snapshot.subs || []).map((text, i) => ({ id: `sq${i}`, text })),
      lanes: [],
      fault: null,
      cursorBlock: null,
      stopped: false,
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
