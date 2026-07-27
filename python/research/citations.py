"""Citation formatting: APA, MLA, Chicago, Harvard, IEEE, or a style the user
describes in their own words.

THE CENTRAL DECISION -- the model never writes citations.

The model writes `[3]` markers and nothing else. Turning `[3]` into
"(Okonjo et al., 2026)" or "Okonjo, Adaeze, et al." happens HERE, in ordinary
Python, from the metadata the provider already gave us. Three consequences,
all of them the reason it works this way:

1. Switching APA -> MLA is instant and free. Nothing regenerates, the paper
   does not change a word, only the rendering of its citations changes. Ask a
   model to rewrite a paper "in MLA" and you get a different paper.
2. Citations cannot be hallucinated. A reference is a pure function of a
   source record that a provider actually returned. There is no step where a
   model could invent a plausible-looking author or year.
3. It is testable. Every style below is covered by unit tests with known
   inputs -- which is not something you can meaningfully do to a generation.

The ONE exception is `custom`: when someone types "like APA but with the URL
in square brackets at the end", no amount of Python can anticipate that, so
that path does make a schema-constrained model call. It formats reference
STRINGS from metadata we already hold; it still cannot invent a source.

WHAT THIS IS NOT: a complete citation engine. Real APA has rules for edited
volumes, translations, conference proceedings, preprints-later-published, and
a dozen other cases. Arthur has web pages and journal articles, so it handles
web pages and journal articles correctly and does not pretend otherwise.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# id -> label shown in the format picker. `custom` is handled separately.
STYLES: dict[str, str] = {
    "apa": "APA 7th",
    "mla": "MLA 9th",
    "chicago": "Chicago (author-date)",
    "harvard": "Harvard",
    "ieee": "IEEE",
}

# What the references section is called in each style. Getting this wrong is a
# small thing that instantly reads as fake to anyone who works in the style.
HEADINGS: dict[str, str] = {
    "apa": "References",
    "mla": "Works Cited",
    "chicago": "Bibliography",
    "harvard": "Reference list",
    "ieee": "References",
    "custom": "References",
}


# ---------- name helpers ----------

def _initials(given: str) -> str:
    """"Adaeze Marie" -> "A. M." """
    return " ".join(f"{part[0]}." for part in (given or "").split() if part)


def _family(person: dict) -> str:
    return (person.get("family") or "").strip()


def _people(src: dict) -> list[dict]:
    """Structured authors, or [] for a web page with no byline. Everything
    below has to cope with [] -- most of the web has no author, and falling
    back to the title is what every style actually prescribes for that."""
    return [p for p in (src.get("authors_list") or []) if _family(p)]


def _year(src: dict) -> str:
    return (str(src.get("year") or "")).strip() or "n.d."


def _title(src: dict) -> str:
    return (src.get("title") or "Untitled").strip()


def _container(src: dict) -> str:
    """Where the work lives: the journal for a paper, the site for a page."""
    return (src.get("venue") or src.get("domain") or "").strip()


def _url(src: dict) -> str:
    doi = (src.get("doi") or "").strip()
    if doi:
        return f"https://doi.org/{doi}"
    return (src.get("url") or "").strip()


# ---------- in-text citations ----------

def in_text(src: dict, style: str) -> str:
    """The marker that replaces `[n]` in the running prose.

    IEEE is the odd one out and the reason `n` is carried on every source:
    it cites by number, so it needs the source's position in the reference
    list, not its author.
    """
    style = style if style in STYLES else "apa"
    people = _people(src)
    year = _year(src)

    if style == "ieee":
        return f"[{src.get('n', '?')}]"

    if not people:
        # No byline: every author-date style falls back to a shortened title.
        short = _short_title(_title(src))
        if style == "mla":
            return f'("{short}")'
        return f'("{short}," {year})' if style == "apa" else f'("{short}" {year})'

    # APA uses an ampersand INSIDE parentheses; every other author-date style
    # spells out "and". This is a real, checkable difference, not a stylistic
    # preference -- APA 7 §8.17.
    names = _author_signal(people, conjunction="&" if style == "apa" else "and")
    if style == "mla":
        # MLA wants a page number; we do not have reliable page numbers for
        # web sources or for PDFs we chunked, so author-only is correct here
        # rather than inventing one.
        return f"({names})"
    if style in ("apa", "harvard"):
        return f"({names}, {year})"
    return f"({names} {year})"  # chicago author-date


def _author_signal(people: list[dict], conjunction: str = "and") -> str:
    """Okonjo / Okonjo & Vasquez / Okonjo et al. -- the short form every
    author-date style uses in running text. Three or more authors collapse to
    "et al." from the FIRST citation in APA 7 (unlike APA 6, which spelled
    them out once)."""
    families = [_family(p) for p in people]
    if len(families) == 1:
        return families[0]
    if len(families) == 2:
        return f"{families[0]} {conjunction} {families[1]}"
    return f"{families[0]} et al."


def _short_title(title: str, words: int = 4) -> str:
    parts = title.split()
    return " ".join(parts[:words]) + ("..." if len(parts) > words else "")


# ---------- reference list entries ----------

def reference(src: dict, style: str) -> str:
    style = style if style in STYLES else "apa"
    return _FORMATTERS[style](src)


def _apa(src: dict) -> str:
    people = _people(src)
    if people:
        names = [f"{_family(p)}, {_initials(p.get('given', ''))}".strip().rstrip(",") for p in people[:20]]
        if len(names) == 1:
            authors = names[0]
        else:
            authors = ", ".join(names[:-1]) + f", & {names[-1]}"
        head = f"{authors} ({_year(src)}). {_title(src)}."
    else:
        head = f"{_title(src)}. ({_year(src)})."
    container = _container(src)
    tail = f" {container}." if container else ""
    url = _url(src)
    return f"{head}{tail} {url}".strip()


def _mla(src: dict) -> str:
    people = _people(src)
    if people:
        first = f"{_family(people[0])}, {(people[0].get('given') or '').strip()}".strip().rstrip(",")
        if len(people) == 1:
            authors = first
        elif len(people) == 2:
            second = f"{(people[1].get('given') or '').strip()} {_family(people[1])}".strip()
            authors = f"{first}, and {second}"
        else:
            authors = f"{first}, et al."
        head = f'{authors}. "{_title(src)}."'
    else:
        head = f'"{_title(src)}."'
    container = _container(src)
    bits = [b for b in (container, _year(src) if _year(src) != "n.d." else "") if b]
    tail = (" " + ", ".join(bits) + ".") if bits else ""
    url = _url(src)
    return f"{head}{tail} {url}".strip()


def _chicago(src: dict) -> str:
    people = _people(src)
    if people:
        first = f"{_family(people[0])}, {(people[0].get('given') or '').strip()}".strip().rstrip(",")
        rest = [f"{(p.get('given') or '').strip()} {_family(p)}".strip() for p in people[1:]]
        if not rest:
            authors = first
        elif len(rest) == 1:
            authors = f"{first}, and {rest[0]}"
        else:
            authors = f"{first}, " + ", ".join(rest[:-1]) + f", and {rest[-1]}"
        head = f'{authors}. {_year(src)}. "{_title(src)}."'
    else:
        head = f'"{_title(src)}." {_year(src)}.'
    container = _container(src)
    tail = f" {container}." if container else ""
    url = _url(src)
    return f"{head}{tail} {url}".strip()


def _harvard(src: dict) -> str:
    people = _people(src)
    if people:
        names = [f"{_family(p)}, {_initials(p.get('given', ''))}".strip().rstrip(",") for p in people[:20]]
        if len(names) == 1:
            authors = names[0]
        else:
            authors = ", ".join(names[:-1]) + f" and {names[-1]}"
        head = f"{authors} ({_year(src)}) '{_title(src)}'"
    else:
        head = f"'{_title(src)}' ({_year(src)})"
    container = _container(src)
    tail = f", {container}." if container else "."
    url = _url(src)
    return f"{head}{tail} Available at: {url}".strip() if url else f"{head}{tail}"


def _ieee(src: dict) -> str:
    people = _people(src)
    if people:
        names = [f"{_initials(p.get('given', ''))} {_family(p)}".strip() for p in people[:6]]
        if len(names) == 1:
            authors = names[0]
        else:
            authors = ", ".join(names[:-1]) + f", and {names[-1]}"
        head = f'{authors}, "{_title(src)},"'
    else:
        head = f'"{_title(src)},"'
    container = _container(src)
    bits = [b for b in (container, _year(src) if _year(src) != "n.d." else "") if b]
    tail = (" " + ", ".join(bits) + ".") if bits else ""
    url = _url(src)
    numbered = f"[{src.get('n', '?')}] {head}{tail}"
    return f"{numbered} {url}".strip()


_FORMATTERS = {
    "apa": _apa,
    "mla": _mla,
    "chicago": _chicago,
    "harvard": _harvard,
    "ieee": _ieee,
}


# ---------- reference list assembly ----------

def reference_list(sources: list[dict], style: str) -> list[dict]:
    """Returns [{n, text}] ready to render.

    Ordering is part of the style, not a detail: IEEE lists in CITATION order
    (so [1] is genuinely the first thing cited), every author-date style lists
    alphabetically. Getting this backwards is one of the fastest ways for a
    reference section to look wrong to someone who knows the style.
    """
    style = style if style in STYLES else "apa"
    entries = [{"n": s.get("n"), "id": s.get("id"), "text": reference(s, style)} for s in sources]
    if style != "ieee":
        entries.sort(key=lambda e: _sort_key(e["text"]))
    return entries


def _sort_key(text: str) -> str:
    return text.lstrip('"“\'').lower()


# ---------- custom (user-described) style ----------

CUSTOM_SCHEMA = {
    "type": "object",
    "properties": {
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["n", "text"],
            },
        }
    },
    "required": ["references"],
}


async def custom_reference_list(llm, model: str, sources: list[dict], description: str) -> list[dict]:
    """Format references in a style the user described in prose.

    This is the only citation path that touches the model, and it is scoped as
    narrowly as possible: it is handed metadata that already exists and asked
    to rearrange it. It cannot reach the paper text, and every `n` it returns
    is checked against the real source list before use, so a hallucinated
    entry is dropped rather than rendered.

    Falls back to APA if the model is unavailable or returns nothing usable --
    a paper with APA references is far better than a paper with none.
    """
    facts = [
        {
            "n": s.get("n"),
            "title": _title(s),
            "authors": [f"{_family(p)}|{(p.get('given') or '').strip()}" for p in _people(s)],
            "year": _year(s),
            "container": _container(s),
            "url": _url(s),
        }
        for s in sources
    ]
    msgs = [
        {"role": "system", "content":
            "You format bibliography entries. The user describes a citation style; you apply it to "
            "the supplied records. Authors are given as 'Family|Given'. Use ONLY the supplied "
            "fields -- never invent an author, year, page number or publisher. Return one entry "
            "per record, keeping its `n`."},
        {"role": "user", "content":
            f"Style to apply:\n{description}\n\nRecords:\n{facts}"},
    ]
    try:
        data = await llm.chat_json(model, msgs, CUSTOM_SCHEMA)
    except Exception as e:
        log.warning("custom citation formatting failed, falling back to APA: %s", e)
        return reference_list(sources, "apa")

    valid = {s.get("n"): s for s in sources}
    out = [
        {"n": r["n"], "id": valid[r["n"]].get("id"), "text": (r.get("text") or "").strip()}
        for r in ((data or {}).get("references") or [])
        if r.get("n") in valid and (r.get("text") or "").strip()
    ]
    if len(out) < len(sources) // 2:
        # The model dropped most of the list; a half-empty bibliography is
        # worse than a correct one in a style the user did not pick.
        log.info("custom citation output too sparse (%d/%d), using APA", len(out), len(sources))
        return reference_list(sources, "apa")
    return out


def render_in_text(text: str, sources_by_n: dict[int, dict], style: str) -> str:
    """Replace every `[n]` marker in a paragraph with its formatted in-text
    citation. Markers whose number has no matching source are left alone --
    they are visible in the UI as unresolved, which is the honest outcome.

    Used for EXPORT (docx/pdf). On screen the markers stay as clickable pills,
    because there they are navigation, not typography.
    """
    import re

    def sub(m):
        n = int(m.group(1))
        src = sources_by_n.get(n)
        return in_text(src, style) if src else m.group(0)

    return re.sub(r"\[(\d+)\]", sub, text or "")


def dedupe_adjacent(text: str) -> str:
    """"(Smith, 2024) (Smith, 2024)" -> "(Smith, 2024)". Two markers pointing
    at the same source land next to each other often enough after formatting
    to be worth cleaning up."""
    import re

    return re.sub(r"(\([^()]+\))\s*\1", r"\1", text or "")


def describe(style: str, custom_description: str = "") -> dict[str, Any]:
    """What the UI shows in the format picker and the export header."""
    if style == "custom":
        return {"id": "custom", "label": "Custom", "heading": HEADINGS["custom"],
                "description": custom_description}
    sid = style if style in STYLES else "apa"
    return {"id": sid, "label": STYLES[sid], "heading": HEADINGS[sid], "description": ""}
