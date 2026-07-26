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
import { planInvestigation, runInvestigation } from "../api/research";
import { useToasts } from "./toasts";

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
  blocks: [],
  elapsed: 0,
  planning: false,
  fault: null, // tavily | offline | zero | failed
  faultDetail: "",
};

export const useResearch = create((set, get) => ({
  ...BLANK,
  recents: loadRecents(),
  degraded: false, // Docker off: snippets only, not a failure
  // view-only bits
  evFilter: "all",
  usedOnly: false,
  expandedEv: null,
  hoverCite: null,
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
      blocks: [],
      gapNote: "",
      elapsed: 0,
      fault: null,
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
          if (i === -1) return { evidence: [...s.evidence, data] };
          const next = s.evidence.slice();
          next[i] = { ...next[i], ...data };
          return { evidence: next };
        });
        break;

      case "research_gap":
        set({ gapNote: data.note || "" });
        break;

      case "research_block":
        set((s) => ({ blocks: [...s.blocks, data], stage: "report" }));
        break;

      case "error":
        if (data.code === "tavily_missing") set({ fault: "tavily" });
        else if (data.code === "zero_results") set({ fault: "zero" });
        else set({ fault: "failed", faultDetail: data.message || "" });
        break;

      case "done":
        set((s) => {
          // A run with no blocks produced nothing worth reopening.
          if (!s.blocks.length) return { stage: s.stage };
          const entry = {
            id: `r${Date.now().toString(36)}`,
            title: s.question.slice(0, 90),
            at: new Date().toISOString(),
            sources: data.sources || s.evidence.length,
            independent: data.independent || 0,
            status: s.lanes.some((l) => l.state === "blocked") ? "partial" : "done",
            snapshot: { question: s.question, blocks: s.blocks, evidence: s.evidence },
          };
          const recents = [entry, ...s.recents.filter((r) => r.title !== entry.title)];
          saveRecents(recents);
          return { stage: "report", recents };
        });
        break;

      default:
        break; // status / unknown events are informational only
    }
  },

  stop() {
    if (abort) abort.abort();
    abort = null;
    stopTimer();
    useToasts.getState().push("Stopped. Everything gathered so far has been kept.", "info");
    // Partial results are still a report if anything was written; otherwise the
    // lanes stay on screen so the user can see how far it got.
    set((s) => (s.blocks.length ? { stage: "report" } : {}));
  },

  newInvestigation: () => {
    if (abort) abort.abort();
    abort = null;
    stopTimer();
    set({ ...BLANK, recents: get().recents, degraded: get().degraded });
  },

  openRecent: (id) => {
    const r = get().recents.find((x) => x.id === id);
    if (!r || !r.snapshot) return;
    set({
      stage: "report",
      question: r.snapshot.question,
      blocks: r.snapshot.blocks,
      evidence: r.snapshot.evidence,
      lanes: [],
      fault: null,
      cursorBlock: null,
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

  // Accept drops the "Arthur wrote this" attribution; revert removes the block.
  // Both are local edits to a local document -- nothing is sent anywhere.
  acceptBlock: (id) =>
    set((s) => ({ blocks: s.blocks.map((b) => (b.id === id ? { ...b, ai: false, fresh: false } : b)) })),
  revertBlock: (id) => set((s) => ({ blocks: s.blocks.filter((b) => b.id !== id) })),
  editBlock: (id, text) =>
    set((s) => ({ blocks: s.blocks.map((b) => (b.id === id ? { ...b, text, ai: false } : b)) })),

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
let timer = null;

function stopTimer() {
  if (timer) clearInterval(timer);
  timer = null;
}
