// The local renderer is the parachute: it only ever runs when the backend
// export has already failed, which means nobody exercises it by accident. If
// it is quietly broken, the user finds out at the worst possible moment. So
// the things it MUST get right -- rendering citations, not padding the
// bibliography, escaping user-controlled text -- are pinned here.
import { describe, expect, it } from "vitest";
import { paperToHtml, paperToText } from "./paperDoc";

const src = (n, family, year, extra = {}) => ({
  n,
  id: `e${n}`,
  title: `Source ${n}`,
  domain: "example.com",
  year,
  authors_list: [{ family, given: "A" }],
  ...extra,
});

const state = (overrides = {}) => ({
  paper: { title: "A Paper", abstract: "An abstract." },
  sections: [{
    id: "s1",
    heading: "Findings",
    order: 0,
    paragraphs: [{ id: "p1", text: "Smoking impairs recall [1].", citations: [1] }],
  }],
  evidence: [src(1, "Smith", 2020), src(2, "Jones", 2019)],
  style: "apa",
  ...overrides,
});

describe("paperToHtml", () => {
  it("renders [n] markers as the citation the style prescribes", () => {
    const html = paperToHtml(state());
    expect(html).toContain("(Smith, 2020)");
    expect(html).not.toContain("[1]");
  });

  it("keeps numeric markers numeric in IEEE", () => {
    const html = paperToHtml(state({ style: "ieee" }));
    expect(html).toContain("[1]");
  });

  it("leaves a marker with no matching source visibly unresolved", () => {
    // Deleting it would hide the fact that a claim points at nothing, which is
    // the one thing this app is built not to do.
    const s = state();
    s.sections[0].paragraphs[0].text = "An orphan claim [9].";
    expect(paperToHtml(s)).toContain("[9]");
  });

  it("lists only the sources the paper actually cites", () => {
    const html = paperToHtml(state());
    expect(html).toContain("Smith");
    expect(html).not.toContain("Jones"); // found, never cited
  });

  it("escapes angle brackets in source titles", () => {
    const s = state();
    s.evidence[0].title = "Effects of <script> tags";
    s.paper.title = "A <b>Paper</b>";
    const html = paperToHtml(s);
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;b&gt;Paper&lt;/b&gt;");
  });

  it("collapses a citation repeated back to back", () => {
    const s = state();
    s.sections[0].paragraphs[0].text = "A claim [1] [1].";
    const html = paperToHtml(s);
    expect(html.match(/\(Smith, 2020\)/g)).toHaveLength(1);
  });

  it("adds the Word namespaces only when asked", () => {
    expect(paperToHtml(state(), { forWord: true })).toContain("urn:schemas-microsoft-com:office:word");
    expect(paperToHtml(state())).not.toContain("urn:schemas-microsoft-com");
  });

  it("titles a paper that never got one", () => {
    expect(paperToHtml(state({ paper: null }))).toContain("Untitled paper");
  });

  it("renders a table block as a real table with a source column", () => {
    const s = state();
    s.sections[0].paragraphs = [{
      id: "p1", kind: "table", columns: ["Study", "Effect"],
      rows: [["Alpha", "Large"]], row_sources: [1], caption: "Comparison.",
    }];
    const html = paperToHtml(s);
    expect(html).toContain("<th>Study</th>");
    expect(html).toContain("<td>Alpha</td>");
    expect(html).toContain("<th>Src</th>");
    expect(html).toContain("[1]");
  });
});

describe("paperToText", () => {
  it("renders citations the same way the HTML does", () => {
    const text = paperToText(state());
    expect(text).toContain("(Smith, 2020)");
    expect(text).toContain("A Paper");
    expect(text).toContain("References");
  });

  it("emits no reference list when nothing is cited", () => {
    const s = state();
    s.sections[0].paragraphs[0].citations = [];
    s.sections[0].paragraphs[0].text = "An uncited claim.";
    expect(paperToText(s)).not.toContain("References");
  });
});
