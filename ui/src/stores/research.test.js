// Switching between saved investigations.
//
// This is the bug rian hit as "when I swap between these it breaks
// everything": clicking another entry in the runs list silently aborted a
// streaming investigation, and the run you landed on came back with its lanes
// thrown away, so it rendered "42 sources found · 0%" above an empty pane.
// Four separate faults, all in openRecent, all pinned here.
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/research", () => ({
  planInvestigation: vi.fn(),
  runInvestigation: vi.fn(),
  synthesizeInvestigation: vi.fn(),
  findMoreSources: vi.fn(),
  exportPaper: vi.fn(),
}));

function memoryStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
  };
}

// The store reads localStorage at MODULE LOAD (recents, citation style), so
// the stub has to exist before the import is evaluated.
vi.stubGlobal("localStorage", memoryStorage());

const { useResearch } = await import("./research");

const entry = (id, over = {}) => ({
  id,
  title: `Run ${id}`,
  at: new Date().toISOString(),
  sources: 2,
  independent: 2,
  status: "partial",
  snapshot: {
    question: `question ${id}`,
    paper: null,
    sections: [],
    evidence: [{ id: "e1", n: 1 }, { id: "e2", n: 2 }],
    subs: ["a", "b"],
    lanes: [
      { id: "sq0", text: "a", state: "done", read: 3, of: 3, srcs: 2, pass: 1 },
      { id: "sq1", text: "b", state: "done", read: 2, of: 2, srcs: 1, pass: 1 },
    ],
    ...over,
  },
});

beforeEach(() => {
  localStorage.clear();
  useResearch.setState({
    stage: "home", runId: null, lanes: [], evidence: [], sections: [], paper: null,
    streaming: false, writing: false, stopped: false, elapsed: 0, recents: [],
  });
});

describe("openRecent", () => {
  it("restores the lanes instead of blanking them", () => {
    // THE reported symptom: the run screen is built around lanes, so a
    // restored run without them showed 0% over an empty pane.
    useResearch.setState({ recents: [entry("r1")] });
    useResearch.getState().openRecent("r1");

    const s = useResearch.getState();
    expect(s.lanes).toHaveLength(2);
    expect(s.lanes.every((l) => l.state === "done")).toBe(true);
    expect(s.evidence).toHaveLength(2);
  });

  it("resets the clock rather than inheriting the last run's", () => {
    useResearch.setState({ recents: [entry("r1")], elapsed: 7 });
    useResearch.getState().openRecent("r1");
    expect(useResearch.getState().elapsed).toBe(0);
  });

  it("is a no-op when you click the run you are already viewing", () => {
    // It used to tear the run down and rebuild it from its last checkpoint,
    // so a stray click replaced live progress with a saved copy.
    useResearch.setState({
      recents: [entry("r1")], runId: "r1", stage: "run",
      evidence: [{ id: "live", n: 9 }],
    });
    useResearch.getState().openRecent("r1");
    expect(useResearch.getState().evidence).toEqual([{ id: "live", n: 9 }]);
  });

  it("checkpoints a streaming run before switching away from it", () => {
    // Switching has to stop the current run -- there is one stream and one
    // store. What it must not do is lose what that run had gathered.
    useResearch.setState({
      recents: [entry("r2")],
      runId: "r1",
      question: "live question",
      streaming: true,
      evidence: [{ id: "e9", n: 9 }],
    });
    useResearch.getState().openRecent("r2");

    const saved = useResearch.getState().recents.find((r) => r.id === "r1");
    expect(saved).toBeTruthy();
    expect(saved.status).toBe("partial");
    expect(saved.snapshot.evidence).toHaveLength(1);
  });

  it("sends a run with no sources and no paper back to the composer", () => {
    // It used to land on the run screen anyway: a blank pane with a dead
    // "Write the report" button.
    useResearch.setState({
      recents: [entry("empty", { evidence: [], lanes: [], sections: [] })],
    });
    useResearch.getState().openRecent("empty");

    const s = useResearch.getState();
    expect(s.stage).toBe("home");
    expect(s.question).toBe("question empty");  // kept, so it can be re-run
  });

  it("opens a finished investigation as a paper", () => {
    useResearch.setState({
      recents: [entry("done", {
        paper: { title: "A Paper", abstract: "" },
        sections: [{ id: "s1", heading: "Intro", order: 0, paragraphs: [] }],
      })],
    });
    useResearch.getState().openRecent("done");

    const s = useResearch.getState();
    expect(s.stage).toBe("report");
    expect(s.stopped).toBe(false);
  });

  it("opens an unfinished investigation ready to write", () => {
    useResearch.setState({ recents: [entry("r1")] });
    useResearch.getState().openRecent("r1");

    const s = useResearch.getState();
    expect(s.stage).toBe("run");
    expect(s.stopped).toBe(true);   // enables "Write the report"
    expect(s.writing).toBe(false);
  });

  it("survives an entry saved before lanes were persisted", () => {
    // Recents live in localStorage, so older entries outlive the schema that
    // wrote them. Missing lanes must degrade to an empty list, not throw.
    const old = entry("old");
    delete old.snapshot.lanes;
    useResearch.setState({ recents: [old] });
    expect(() => useResearch.getState().openRecent("old")).not.toThrow();
    expect(useResearch.getState().lanes).toEqual([]);
    expect(useResearch.getState().evidence).toHaveLength(2);
  });
});
