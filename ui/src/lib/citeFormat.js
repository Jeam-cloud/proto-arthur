// In-text citation rendering for the ON-SCREEN paper.
//
// This deliberately mirrors python/research/citations.py, and only the
// in-text half of it. WHY duplicate anything at all: switching APA -> MLA has
// to be instant and offline -- it is a dropdown, not a request. A round trip
// to re-render every citation in a 3000-word paper would make the picker feel
// broken, and the paper's text does not change, only the label on each pill.
//
// The REFERENCE LIST is not duplicated here. It is longer, style-specific,
// and only needed at export time, so it stays in Python where it is unit
// tested. On screen the references section renders from the same source
// records through a small formatter below, and export re-derives it properly.
//
// If you change a style here, change it in citations.py too -- the tests
// there are the authority.

export const STYLES = [
  { id: "apa", label: "APA 7th" },
  { id: "mla", label: "MLA 9th" },
  { id: "chicago", label: "Chicago" },
  { id: "harvard", label: "Harvard" },
  { id: "ieee", label: "IEEE" },
  { id: "custom", label: "Custom…" },
];

export const HEADINGS = {
  apa: "References",
  mla: "Works Cited",
  chicago: "References",
  harvard: "Reference list",
  ieee: "References",
  custom: "References",
};

function people(src) {
  return (src.authors_list || []).filter((p) => p && (p.family || "").trim());
}

function year(src) {
  return String(src.year || "").trim() || "n.d.";
}

// APA uses an ampersand inside parentheses (APA 7 §8.17); every other
// author-date style spells out "and". Mirrors _author_signal in citations.py.
function authorSignal(list, conjunction = "and") {
  const fams = list.map((p) => p.family.trim());
  if (fams.length === 1) return fams[0];
  if (fams.length === 2) return `${fams[0]} ${conjunction} ${fams[1]}`;
  return `${fams[0]} et al.`;
}

function shortTitle(title, words = 4) {
  const parts = String(title || "Untitled").split(/\s+/);
  return parts.slice(0, words).join(" ") + (parts.length > words ? "…" : "");
}

// The label shown inside a citation pill. IEEE stays numeric, which is why
// every source carries its `n` all the way to the UI.
export function inTextLabel(src, style) {
  if (!src) return "?";
  if (style === "ieee" || style === "custom") return String(src.n);

  const list = people(src);
  const y = year(src);
  if (!list.length) {
    const t = shortTitle(src.title);
    if (style === "mla") return `"${t}"`;
    return style === "apa" ? `"${t}," ${y}` : `"${t}" ${y}`;
  }

  const names = authorSignal(list, style === "apa" ? "&" : "and");
  if (style === "mla") return names;
  if (style === "apa" || style === "harvard") return `${names}, ${y}`;
  return `${names} ${y}`; // chicago author-date
}

// Whether the pill renders as (Author, Year) or [3]. Numeric styles keep the
// tight bracket look; author-date styles need the parentheses to read as a
// citation rather than as a footnote marker.
export function isNumericStyle(style) {
  return style === "ieee" || style === "custom";
}

// A readable reference-list line for the on-screen references section. Export
// re-derives this properly in Python; this is the reading view.
export function referenceLine(src, style) {
  const list = people(src);
  const y = year(src);
  const title = src.title || "Untitled";
  const container = src.venue || src.domain || "";
  const url = src.doi ? `https://doi.org/${src.doi}` : (src.url || "");

  if (style === "ieee") {
    const names = list.map((p) => `${initials(p.given)} ${p.family}`.trim()).join(", ");
    return `[${src.n}] ${names ? `${names}, ` : ""}"${title}," ${[container, y !== "n.d." ? y : ""].filter(Boolean).join(", ")}. ${url}`.trim();
  }
  if (style === "mla") {
    const first = list[0] ? `${list[0].family}, ${(list[0].given || "").trim()}`.replace(/,\s*$/, "") : "";
    const authors = !list.length ? "" : list.length > 2 ? `${first}, et al.` : first;
    return `${authors ? `${authors}. ` : ""}"${title}." ${[container, y !== "n.d." ? y : ""].filter(Boolean).join(", ")}. ${url}`.trim();
  }
  if (style === "chicago") {
    const first = list[0] ? `${list[0].family}, ${(list[0].given || "").trim()}`.replace(/,\s*$/, "") : "";
    return `${first ? `${first}. ` : ""}${y}. "${title}." ${container}. ${url}`.trim();
  }
  if (style === "harvard") {
    const names = list.map((p) => `${p.family}, ${initials(p.given)}`.replace(/,\s*$/, "")).join(", ");
    return `${names ? `${names} ` : ""}(${y}) '${title}', ${container}. Available at: ${url}`.trim();
  }
  // apa + custom fallback
  const names = list.map((p) => `${p.family}, ${initials(p.given)}`.replace(/,\s*$/, "")).join(", ");
  return `${names ? `${names} ` : ""}(${y}). ${title}. ${container}. ${url}`.trim();
}

function initials(given) {
  return (given || "").split(/\s+/).filter(Boolean).map((g) => `${g[0]}.`).join(" ");
}

// Reference lists are alphabetical in every author-date style and in citation
// order in IEEE. Getting this backwards is an instant tell.
export function orderReferences(sources, style) {
  const list = sources.slice();
  if (style === "ieee" || style === "custom") return list.sort((a, b) => a.n - b.n);
  return list.sort((a, b) =>
    referenceLine(a, style).replace(/^["'“]/, "").toLowerCase()
      .localeCompare(referenceLine(b, style).replace(/^["'“]/, "").toLowerCase()));
}
