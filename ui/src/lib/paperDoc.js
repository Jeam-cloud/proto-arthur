// One renderer, three consumers: Copy, the local .doc fallback, and the local
// print-to-PDF fallback.
//
// WHY this file exists at all, when python/research/export.py already renders
// the paper properly: that renderer lives behind a network call to the local
// backend, and when that call fails the user is holding a finished paper they
// cannot get out of the app. A document you can see but cannot remove is worse
// than one that never rendered, so there is a second path that needs nothing
// but the browser -- no backend, no Python, no libraries.
//
// The Python renderer stays PRIMARY. It produces a real .docx with real Word
// tables and a properly hanging bibliography, and it is unit tested. This is
// the parachute, and the UI says so when it opens.
//
// WHY .doc-as-HTML rather than building a real .docx in the browser: a real
// .docx is a zip of several XML parts, which means shipping a zip library and
// hand-writing WordprocessingML for the sake of a fallback. Word has opened
// HTML saved with the Office namespaces since Word 2000 and renders headings,
// indents and tables from it correctly. It is an old trick, it is widely used
// for exactly this purpose, and it costs zero dependencies.

import { HEADINGS, inTextLabel, isNumericStyle, referenceLine, orderReferences } from "./citeFormat";

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Turns the [n] markers the model wrote into the citation the chosen style
// prescribes. Same rule as the Python exporter: on screen a citation is a
// control you can click, in a file it is text, so it stops being a number and
// becomes "(Smith & Jones, 2021)".
//
// A marker with no matching source is left exactly as it is. Deleting it would
// hide the fact that a claim points at nothing, which is the one thing this
// app is built not to do.
function renderCitations(text, byN, style) {
  const numeric = isNumericStyle(style);
  return String(text || "").replace(/\[(\d+)\]/g, (whole, n) => {
    const src = byN[Number(n)];
    if (!src) return whole;
    const label = inTextLabel(src, style);
    return numeric ? `[${label}]` : `(${label})`;
  });
}

// "(Smith, 2020) (Smith, 2020)" happens whenever the model repeats a marker in
// one sentence. Mirrors dedupe_adjacent in python/research/citations.py.
function dedupeAdjacent(text) {
  return text.replace(/(\([^()]+\)|\[[^[\]]+\])(\s*\1)+/g, "$1");
}

/**
 * The paper as one HTML string.
 *
 * `forWord` switches on the bits Word needs and a browser does not (the Office
 * namespaces and a @page rule for margins). Everything else is identical, so
 * what you copy to the clipboard and what you open in Word cannot disagree.
 */
export function paperToHtml({ paper, sections, evidence, style }, { forWord = false } = {}) {
  const byN = {};
  (evidence || []).forEach((e) => { byN[e.n] = e; });

  const citedNumbers = new Set();
  (sections || []).forEach((sec) =>
    (sec.paragraphs || []).forEach((p) => (p.citations || []).forEach((c) => citedNumbers.add(c))));
  const cited = (evidence || []).filter((e) => citedNumbers.has(e.n));
  const references = orderReferences(cited, style);

  const parts = [];
  parts.push(`<h1 class="t">${esc(paper?.title || "Untitled paper")}</h1>`);

  if (paper?.abstract) {
    parts.push('<h2 class="c">Abstract</h2>');
    parts.push(`<p class="noind">${esc(paper.abstract)}</p>`);
  }

  for (const sec of sections || []) {
    parts.push(`<h2>${esc(sec.heading || "")}</h2>`);
    for (const p of sec.paragraphs || []) {
      if (p.kind === "table") {
        parts.push(tableHtml(p));
        continue;
      }
      parts.push(`<p>${esc(dedupeAdjacent(renderCitations(p.text, byN, style)))}</p>`);
    }
  }

  if (references.length) {
    // Page break before the reference list: required by APA and Chicago,
    // harmless everywhere else. `page-break-before` is what both Word and the
    // browser's print engine honour.
    parts.push(`<h2 class="c brk">${esc(HEADINGS[style] || "References")}</h2>`);
    for (const src of references) parts.push(`<p class="ref">${esc(referenceLine(src, style))}</p>`);
  }

  // Double-spaced 12pt Times on 1in margins -- the same defaults the Python
  // renderer uses, so the two outputs look like the same document.
  const css = `
    body { font-family: "Times New Roman", Times, serif; font-size: 12pt; line-height: 2; margin: 1in; }
    h1.t { font-size: 12pt; font-weight: bold; text-align: center; margin: 0 0 1em; }
    h2 { font-size: 12pt; font-weight: bold; margin: 1em 0 0; }
    h2.c { text-align: center; }
    h2.brk { page-break-before: always; }
    p { margin: 0; text-indent: 0.5in; text-align: justify; }
    p.noind { text-indent: 0; }
    p.ref { text-indent: 0; margin-left: 0.5in; }
    /* Hanging indent, the single most obvious tell of a real bibliography. */
    p.ref { padding-left: 0; text-indent: -0.5in; }
    table { border-collapse: collapse; margin: 12pt 0; font-size: 10pt; line-height: 1.2; }
    th, td { border: 1px solid #000; padding: 4pt 6pt; text-align: left; vertical-align: top; }
    th { font-weight: bold; }
    p.cap { text-indent: 0; font-style: italic; font-size: 10pt; line-height: 1.2; }
    @page { margin: 1in; }
  `;

  const ns = forWord
    ? ' xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word"'
    : "";

  return `<!DOCTYPE html><html${ns}><head><meta charset="utf-8">`
    + `<title>${esc(paper?.title || "Paper")}</title><style>${css}</style></head>`
    + `<body>${parts.join("\n")}</body></html>`;
}

function tableHtml(block) {
  const cols = block.columns || [];
  const rows = block.rows || [];
  const srcs = block.row_sources || [];
  if (!cols.length || !rows.length) return "";
  const head = `<tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}<th>Src</th></tr>`;
  const body = rows.map((row, ri) => {
    const n = srcs[ri];
    // The Src column stays a bare number in every style: inside a table it is
    // a locator back to the reference list, not a running-text citation.
    return `<tr>${row.slice(0, cols.length).map((c) => `<td>${esc(c)}</td>`).join("")}`
      + `<td>${n ? `[${esc(n)}]` : ""}</td></tr>`;
  }).join("");
  const cap = block.caption ? `<p class="cap">${esc(block.caption)}</p>` : "";
  return `<table>${head}${body}</table>${cap}`;
}

/** The same document as plain text, for pasting anywhere that is not rich. */
export function paperToText({ paper, sections, evidence, style }) {
  const byN = {};
  (evidence || []).forEach((e) => { byN[e.n] = e; });

  const out = [paper?.title || "Untitled paper", ""];
  if (paper?.abstract) out.push("Abstract", paper.abstract, "");

  const citedNumbers = new Set();
  for (const sec of sections || []) {
    out.push(sec.heading || "");
    for (const p of sec.paragraphs || []) {
      (p.citations || []).forEach((c) => citedNumbers.add(c));
      if (p.kind === "table") {
        const cols = p.columns || [];
        out.push([...cols, "Src"].join("\t"));
        (p.rows || []).forEach((row, ri) => {
          const n = (p.row_sources || [])[ri];
          out.push([...row.slice(0, cols.length), n ? `[${n}]` : ""].join("\t"));
        });
        if (p.caption) out.push(p.caption);
      } else {
        out.push(dedupeAdjacent(renderCitations(p.text, byN, style)));
      }
      out.push("");
    }
  }

  const refs = orderReferences((evidence || []).filter((e) => citedNumbers.has(e.n)), style);
  if (refs.length) {
    out.push(HEADINGS[style] || "References");
    refs.forEach((src) => out.push(referenceLine(src, style)));
  }
  return out.join("\n");
}

/**
 * Local .doc fallback. Returns a Blob Word opens natively.
 *
 * The BOM matters: without it Word guesses the encoding from the locale and
 * mangles every accented author name, which on an academic bibliography is
 * most of them.
 */
export function paperToDocBlob(state) {
  const html = paperToHtml(state, { forWord: true });
  return new Blob(["﻿", html], { type: "application/msword" });
}

/**
 * Local PDF fallback: render the paper into an offscreen iframe and hand it to
 * the print dialog, where "Save as PDF" is a destination on every OS.
 *
 * WHY not generate PDF bytes in the browser: doing it properly means shipping
 * a PDF library and re-implementing pagination, hanging indents and font
 * embedding that python/research/export.py already does correctly. The print
 * engine is a PDF writer that is already installed, already paginates, and
 * already embeds fonts.
 */
export function printPaper(state) {
  const iframe = document.createElement("iframe");
  // Positioned offscreen rather than display:none -- a hidden iframe is not
  // laid out, and an unlaid-out document prints as a blank page.
  iframe.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
  document.body.appendChild(iframe);

  const cleanup = () => { if (iframe.parentNode) iframe.parentNode.removeChild(iframe); };

  iframe.onload = () => {
    try {
      iframe.contentWindow.focus();
      iframe.contentWindow.print();
    } finally {
      // The print dialog is modal and synchronous in Chromium, but removing
      // the frame the instant print() returns can cut the render short on
      // slower machines. A short delay costs nothing and avoids a blank PDF.
      setTimeout(cleanup, 1000);
    }
  };

  const doc = iframe.contentWindow.document;
  doc.open();
  doc.write(paperToHtml(state));
  doc.close();
}
