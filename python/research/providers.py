"""Search providers. One `SearchHit` shape, four very different APIs behind it.

WHY four providers instead of just Tavily: Tavily is a general web index. It
will happily return a blog post summarising a paper and never return the paper.
For a research tool that is the wrong bias, so academic questions also go to:

  * OpenAlex  - the open successor to Microsoft Academic. Best coverage,
                gives citation counts and venue, no key, no rate limit worth
                worrying about (they ask for a mailto so they can contact
                abusers instead of banning IPs -- we send the app name).
  * arXiv     - preprints. The only one of the three that reliably hands you a
                PDF you are actually allowed to download.
  * Crossref  - the DOI registry. Weakest full-text story, best metadata, and
                it is the authority on "does this DOI exist".

All three are free and keyless ON PURPOSE: Research mode already requires a
Tavily key, and needing a second signup to read papers would mean nobody ever
turns academic sources on.

Every provider is best-effort. A dead provider returns [] and the run
continues -- one flaky API must never sink an investigation.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

TIMEOUT = 20.0
UA = "Arthur/1.0 (local research assistant; https://github.com/arthur-app)"
# OpenAlex/Crossref both use "polite pool" routing keyed off a contact address.
# It is a courtesy header, not authentication, and it gets us the faster pool.
POLITE = "arthur-local@example.invalid"


@dataclass
class SearchHit:
    """One candidate source, before we have read it.

    `kind` drives which card the UI renders (web vs paper), `provider` is shown
    on the card so the user can see WHERE a claim came from, not just what.
    """

    url: str
    title: str
    kind: str = "web"          # web | paper
    provider: str = "tavily"   # tavily | openalex | arxiv | crossref
    snippet: str = ""
    domain: str = ""
    date: str = ""
    type: str = "docs"         # news | docs | blog | forum | paper
    # paper-only metadata (empty for web hits)
    authors: str = ""
    # Structured authors, one {"given", "family"} per person. The joined
    # `authors` string above is for display only -- APA wants "Okonjo, A.",
    # MLA wants "Okonjo, Adaeze", IEEE wants "A. Okonjo", and none of those
    # can be recovered from a pre-joined string without guessing. Keeping the
    # parts is the only way the citation formatter can be correct.
    authors_list: list[dict] = field(default_factory=list)
    year: str = ""
    venue: str = ""
    cites: int = 0
    doi: str = ""
    pdf_url: str = ""
    text: str = ""             # filled in later by the fetch step
    error: str = ""
    # Filled in by the fetch step (research/engine.py._read) when the fetched
    # document was actually a PDF -- real numbers only, never guessed. Used to
    # show "PDF · 14p read" on the evidence card so a person can tell the app
    # opened the primary source rather than reading a page ABOUT it.
    is_pdf: bool = False
    pages: int = 0
    extra: dict = field(default_factory=dict)


def split_name(display: str) -> dict:
    """"Adaeze Okonjo" -> {"given": "Adaeze", "family": "Okonjo"}.

    OpenAlex and arXiv only give a single display string, so the split has to
    be inferred: last whitespace-separated token is the family name, the rest
    is given names. This is RIGHT for the overwhelming majority of records and
    WRONG for compound surnames ("van der Berg", "de la Cruz") and for the
    name orders that do not put the family name last.

    Crossref is not run through this at all -- it hands us given/family as
    separate fields, so we use those verbatim. Where the data is good we keep
    it; this function only exists for the providers whose data is not.
    """
    parts = (display or "").strip().split()
    if not parts:
        return {"given": "", "family": ""}
    if len(parts) == 1:
        return {"given": "", "family": parts[0]}
    return {"given": " ".join(parts[:-1]), "family": parts[-1]}


def join_names(people: list[dict], limit: int = 3) -> str:
    """Display-only join, e.g. "Okonjo, A., Vasquez, R. et al." -- what the
    evidence card shows. The citation formatter never reads this."""
    shown = []
    for p in people[:limit]:
        given = (p.get("given") or "").strip()
        family = (p.get("family") or "").strip()
        initials = " ".join(f"{g[0]}." for g in given.split() if g)
        shown.append(f"{family}, {initials}".strip().rstrip(",") if initials else family)
    out = ", ".join(x for x in shown if x)
    if len(people) > limit:
        out += " et al."
    return out


def root_domain(url: str) -> str:
    """news.bbc.co.uk -> bbc.co.uk. Used for independence grouping: two hits
    from the same publisher are one voice, not two."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # crude but right for the shapes that matter (co.uk, com.au, ac.uk...)
    if parts[-2] in {"co", "com", "ac", "gov", "org", "net"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _classify(url: str, snippet: str) -> str:
    """Cheap type badge. Deliberately NOT a model call -- this is a label on a
    card, and spending a generation on it would slow every search down."""
    host = (urlparse(url).hostname or "").lower()
    if any(k in host for k in ("docs.", "developer.", "learn.", "/docs")):
        return "docs"
    if any(k in host for k in ("news", "times", "post", "reuters", "bloomberg", "wired", "verge")):
        return "news"
    if any(k in host for k in ("blog", "medium.com", "substack", "dev.to")):
        return "blog"
    if any(k in host for k in ("reddit", "stackoverflow", "news.ycombinator", "forum")):
        return "forum"
    return "docs"


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict | None:
    try:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # provider down / rate limited / schema changed
        log.info("provider request failed: %s (%s)", url, e)
        return None


# ---------- Tavily (general web, needs the key) ----------

async def search_web(
    api_key: str,
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[SearchHit]:
    if not api_key:
        return []
    try:
        from tavily import TavilyClient

        kwargs: dict = {"max_results": max_results}
        if include_domains:
            kwargs["include_domains"] = include_domains
        if exclude_domains:
            kwargs["exclude_domains"] = exclude_domains
        res = await asyncio.to_thread(
            lambda: TavilyClient(api_key=api_key).search(query, **kwargs)
        )
    except Exception as e:
        log.info("tavily search failed: %s", e)
        return []

    hits = []
    for r in res.get("results", []):
        url = r.get("url", "")
        if not url:
            continue
        hits.append(SearchHit(
            url=url,
            title=r.get("title") or url,
            kind="web",
            provider="tavily",
            snippet=(r.get("content") or "")[:600],
            domain=root_domain(url),
            date=(r.get("published_date") or "")[:10],
            type=_classify(url, r.get("content", "")),
        ))
    return hits


# ---------- OpenAlex ----------

async def search_openalex(query: str, max_results: int = 4) -> list[SearchHit]:
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as client:
        data = await _get_json(client, "https://api.openalex.org/works", {
            "search": query,
            "per-page": max_results,
            "mailto": POLITE,
            # Only things we can actually show the user a page for.
            "filter": "has_abstract:true",
        })
    if not data:
        return []

    hits = []
    for w in data.get("results", [])[:max_results]:
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        oa = w.get("best_oa_location") or {}
        pdf = oa.get("pdf_url") or ""
        landing = oa.get("landing_page_url") or w.get("id") or ""
        people = [
            split_name((a.get("author") or {}).get("display_name", ""))
            for a in (w.get("authorships") or [])
        ]
        people = [p for p in people if p["family"]]
        hits.append(SearchHit(
            url=pdf or landing,
            title=w.get("display_name") or "Untitled work",
            kind="paper",
            provider="openalex",
            snippet=_undo_inverted_index(w.get("abstract_inverted_index"))[:800],
            domain="openalex.org",
            type="paper",
            authors=join_names(people),
            authors_list=people,
            year=str(w.get("publication_year") or ""),
            venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name", "") or "Preprint",
            cites=int(w.get("cited_by_count") or 0),
            doi=doi,
            pdf_url=pdf,
        ))
    return hits


def _undo_inverted_index(inv: dict | None) -> str:
    """OpenAlex ships abstracts as {word: [positions]} because of a publisher
    licensing quirk -- they may distribute the index, not the prose. Rebuilding
    it is legal and is what every OpenAlex client does."""
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


# ---------- arXiv ----------

async def search_arxiv(query: str, max_results: int = 4) -> list[SearchHit]:
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as client:
        try:
            r = await client.get("http://export.arxiv.org/api/query", params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": max_results,
                "sortBy": "relevance",
            })
            r.raise_for_status()
            xml = r.text
        except Exception as e:
            log.info("arxiv search failed: %s", e)
            return []

    # arXiv speaks Atom, not JSON. ElementTree is stdlib and this document is
    # small and from a known host, so no third-party XML parser is needed.
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    hits = []
    for entry in root.findall("a:entry", ns)[:max_results]:
        title = (entry.findtext("a:title", "", ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("a:summary", "", ns) or "").strip().replace("\n", " ")
        published = (entry.findtext("a:published", "", ns) or "")[:10]
        abs_url, pdf_url = "", ""
        for link in entry.findall("a:link", ns):
            if link.get("type") == "application/pdf":
                pdf_url = link.get("href", "")
            elif link.get("rel") == "alternate":
                abs_url = link.get("href", "")
        names = [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)]
        people = [split_name(n) for n in names if n]
        people = [p for p in people if p["family"]]
        hits.append(SearchHit(
            url=pdf_url or abs_url,
            title=title or "arXiv preprint",
            kind="paper",
            provider="arxiv",
            snippet=summary[:800],
            domain="arxiv.org",
            type="paper",
            authors=join_names(people),
            authors_list=people,
            year=published[:4],
            venue="arXiv",
            doi=(entry.findtext("{http://arxiv.org/schemas/atom}doi", "", {}) or ""),
            pdf_url=pdf_url,
        ))
    return hits


# ---------- Crossref ----------

async def search_crossref(query: str, max_results: int = 3) -> list[SearchHit]:
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as client:
        data = await _get_json(client, "https://api.crossref.org/works", {
            "query.bibliographic": query,
            "rows": max_results,
            "select": "DOI,title,author,issued,container-title,is-referenced-by-count,abstract,URL,link",
            "mailto": POLITE,
        })
    if not data:
        return []

    hits = []
    for it in (data.get("message", {}) or {}).get("items", [])[:max_results]:
        titles = it.get("title") or []
        # Crossref is the ONE provider that gives given/family separately, so
        # it skips split_name() entirely -- no guessing where the surname ends.
        people = [
            {"given": (a.get("given") or "").strip(), "family": (a.get("family") or "").strip()}
            for a in (it.get("author") or [])
        ]
        people = [p for p in people if p["family"]]
        parts = ((it.get("issued") or {}).get("date-parts") or [[]])[0]
        pdf = ""
        for link in it.get("link") or []:
            if link.get("content-type") == "application/pdf":
                pdf = link.get("URL", "")
                break
        hits.append(SearchHit(
            url=pdf or it.get("URL", ""),
            title=(titles[0] if titles else "Untitled record"),
            kind="paper",
            provider="crossref",
            # Crossref abstracts are JATS XML fragments; strip the tags crudely
            # rather than pulling in a parser for a preview string.
            snippet=_strip_tags(it.get("abstract", ""))[:800],
            domain="crossref.org",
            type="paper",
            authors=join_names(people),
            authors_list=people,
            year=str(parts[0]) if parts else "",
            venue=(it.get("container-title") or [""])[0] or "Journal",
            cites=int(it.get("is-referenced-by-count") or 0),
            doi=it.get("DOI", ""),
            pdf_url=pdf,
        ))
    return hits


def _strip_tags(s: str) -> str:
    out, depth = [], 0
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split())


# ---------- fan-out ----------

async def gather(
    query: str,
    kinds: list[str],
    tavily_key: str,
    per_provider: int = 4,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[SearchHit]:
    """Run every selected provider CONCURRENTLY and merge.

    Concurrency matters more here than anywhere else in the app: four sequential
    HTTP round trips to four different continents is most of a lane's wall time.
    """
    jobs = []
    wants_web = any(k in kinds for k in ("web", "news", "docs"))
    if wants_web:
        jobs.append(search_web(tavily_key, query, per_provider + 1, include_domains, exclude_domains))
    if "academic" in kinds:
        jobs.extend([
            search_openalex(query, per_provider),
            search_arxiv(query, per_provider),
            search_crossref(query, max(2, per_provider - 1)),
        ])

    if not jobs:
        return []

    results = await asyncio.gather(*jobs, return_exceptions=True)
    merged: list[SearchHit] = []
    seen: set[str] = set()
    for res in results:
        if isinstance(res, BaseException):
            continue
        for hit in res:
            key = (hit.doi or hit.url).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    return merged
