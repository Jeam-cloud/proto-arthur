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
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

TIMEOUT = 20.0
UA = "Arthur/1.0 (local research assistant; https://github.com/arthur-app)"

# ---------------------------------------------------------------------------
# QUERY SHAPING AND RELEVANCE
#
# THE BUG THIS EXISTS TO FIX. Sub-questions arrive as natural-language
# sentences ("Differences in attention span and focus maintenance between ADHD
# and neurotypical individuals"). Every academic index here is a KEYWORD engine,
# not a semantic one: handed that sentence, they match the common words --
# differences, individuals, maintenance, performance -- and then rank what
# survives by citation count. The result was an investigation into ADHD coming
# back with a global asthma strategy, an attachment-theory paper, and a study of
# IT assets and firm performance, all of which are heavily cited and all of
# which contain the word "differences" or "individuals".
#
# So two deterministic gates, both cheap, neither involving a model:
#   1. Strip the sentence down to content words before it is sent (below).
#   2. Score what comes back against those words and drop the ones that do not
#      overlap at all (see `relevance` and the filter in `gather`).
#
# WHY not embeddings for the gate: the embedder is already used to pick the
# best PASSAGE inside a document we have decided to keep, which is a fine use of
# it. Running it over every candidate from six providers before we know whether
# any are worth reading would add a model round trip to the slowest part of the
# run to solve a problem that word overlap already solves. Cheap first.

# Deliberately NOT a general English stopword list. These are the words that
# make a research question read like a research question -- they carry the
# intent and none of the subject, so they are exactly what poisons a keyword
# match. Subject nouns are never in here.
_FILLER = frozenset("""
a an the and or but of in on at to for with without from by as is are was were
be been being do does did how what why when which who whom whose that this these
those it its their there here than then so such if between among across within
into over under about against during before after above below up down out off
i we you they he she them us our your his her
compare compared comparing comparison contrast difference differences differ
differing relationship relationships association associations link links
effect effects impact impacts influence influences role roles
study studies research researching investigate investigation examine examining
analysis analyse analyze evidence findings finding results outcome outcomes
review reviews overview summary literature paper papers article articles
use used using usage
individual individuals people person persons participants subjects
factor factors aspect aspects issue issues question questions topic topics
level levels type types kind kinds way ways
identify identifying determine determining assess assessing evaluate evaluating
understand understanding explore exploring
main major key primary significant important relevant various different several
more most less least much many any all some other others
new recent current modern latest existing
""".split())

# Terms shorter than this are usually noise ("of", "vs") -- except acronyms,
# which are handled separately because ADHD, PTSD and IQ are the whole point.
_MIN_TERM = 3


def key_terms(query: str) -> list[str]:
    """The content words of a research question, in order, deduplicated.

    Acronyms survive whatever their length: a question about ADHD, IQ or PTSD
    is ABOUT that acronym, and dropping it for being short would discard the
    single most discriminating token in the sentence.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[^A-Za-z0-9+#-]+", query or ""):
        if not raw:
            continue
        # An all-caps token of 2+ chars in the original is an acronym.
        is_acronym = len(raw) >= 2 and raw.isupper() and raw.isalpha()
        word = raw.lower()
        if word in seen:
            continue
        if is_acronym:
            seen.add(word)
            out.append(word)
            continue
        if len(word) < _MIN_TERM or word in _FILLER or word.isdigit():
            continue
        seen.add(word)
        out.append(word)
    return out


def keyword_query(query: str, limit: int = 8) -> str:
    """What actually gets sent to a keyword index.

    Falls back to the original string when stripping leaves nothing -- a
    question made entirely of filler is unlikely, but returning an empty query
    would make the provider return its most-cited works, which is precisely the
    failure being fixed here.
    """
    terms = key_terms(query)[:limit]
    return " ".join(terms) if terms else (query or "").strip()


def relevance(hit: SearchHit, terms: list[str]) -> float:
    """Fraction of the question's content words that appear in this hit.

    Title matches count double: a paper whose TITLE contains the subject is
    about the subject, whereas an abstract may merely mention it in passing.
    Stems are compared by prefix so "attention" matches "attentional" without
    dragging in a stemming dependency.
    """
    if not terms:
        return 1.0
    title = (hit.title or "").lower()
    body = (hit.snippet or "").lower()
    score = 0.0
    for t in terms:
        stem = t[:6]
        in_title = stem in title
        in_body = stem in body
        if in_title:
            score += 2.0
        elif in_body:
            score += 1.0
    # Normalised against the best achievable (every term in the title).
    return score / (2.0 * len(terms))


# A hit matching NONE of the question's content words is not a weak result, it
# is a wrong one. The floor is deliberately low -- it exists to catch the
# asthma-for-ADHD case, not to second-guess a librarian.
RELEVANCE_FLOOR = 0.08
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
    domain: str = ""      # real host of the URL, for display
    # WHO PUBLISHED THIS -- deliberately separate from `provider` (who FOUND
    # it) and from `domain`. Getting these confused is a real bug we shipped
    # once: every OpenAlex result carried domain="openalex.org", so forty
    # papers from forty different journals looked like forty reprints of one
    # publisher, collapsed to a single evidence card, and dragged every
    # confidence score down to "thin" because the publisher set had size 1.
    # A search index is not a publisher. For a paper this is the journal; for
    # a web page it is the site.
    publisher: str = ""
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


def _venue_or_host(venue: str, url: str) -> str:
    """The independence key for a paper: its journal, or failing that the host
    that serves it. Never the API that found it."""
    v = (venue or "").strip()
    if v and v.lower() not in {"preprint", "journal", "unknown"}:
        return v
    return root_domain(url) or "unattributed"


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
            publisher=root_domain(url),  # for a web page, the site IS the publisher
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
            domain=root_domain(pdf or landing) or "openalex.org",
            publisher=_venue_or_host(
                ((w.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                pdf or landing,
            ),
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
            # Every arXiv preprint really is published by arXiv, so unlike the
            # index providers this one is honest -- but it does mean two arXiv
            # preprints count as one publisher for confidence, which is the
            # correct conservative reading of two unreviewed papers.
            publisher="arXiv",
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
            domain=root_domain(pdf or it.get("URL", "")) or "crossref.org",
            publisher=_venue_or_host(
                (it.get("container-title") or [""])[0], pdf or it.get("URL", "")
            ),
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


# ---------- Semantic Scholar ----------

async def search_semanticscholar(query: str, max_results: int = 4) -> list[SearchHit]:
    """Best general-purpose academic index of the free ones: real abstracts,
    citation counts, and direct open-access PDF links across every field.

    Keyless requests are rate limited rather than rejected, so a 429 here is
    normal under load and simply yields no hits for this lane -- never an
    error the user sees."""
    fields = "title,abstract,year,venue,citationCount,externalIds,openAccessPdf,authors,url"
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as client:
        data = await _get_json(client, "https://api.semanticscholar.org/graph/v1/paper/search", {
            "query": query, "limit": max_results, "fields": fields,
        })
    if not data:
        return []

    hits = []
    for p in (data.get("data") or [])[:max_results]:
        if not (p.get("abstract") or "").strip():
            continue  # no abstract means nothing to extract a passage from
        people = [split_name(a.get("name", "")) for a in (p.get("authors") or [])]
        people = [x for x in people if x["family"]]
        oa = (p.get("openAccessPdf") or {}).get("url") or ""
        doi = (p.get("externalIds") or {}).get("DOI", "") or ""
        landing = p.get("url") or (f"https://doi.org/{doi}" if doi else "")
        venue = (p.get("venue") or "").strip()
        hits.append(SearchHit(
            url=oa or landing,
            title=p.get("title") or "Untitled work",
            kind="paper", provider="semanticscholar",
            snippet=(p.get("abstract") or "")[:800],
            domain=root_domain(oa or landing) or "semanticscholar.org",
            publisher=_venue_or_host(venue, oa or landing),
            type="paper",
            authors=join_names(people), authors_list=people,
            year=str(p.get("year") or ""),
            venue=venue or "Preprint",
            cites=int(p.get("citationCount") or 0),
            doi=doi, pdf_url=oa,
        ))
    return hits


# ---------- Europe PMC ----------

async def search_europepmc(query: str, max_results: int = 4) -> list[SearchHit]:
    """Deepest free biomedical corpus, and unusually generous: a large share
    of records carry OPEN FULL TEXT, not just an abstract. For medical
    questions this returns better evidence than any general index."""
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as client:
        data = await _get_json(client, "https://www.ebi.ac.uk/europepmc/webservices/rest/search", {
            "query": query, "format": "json", "pageSize": max_results,
            "resultType": "core",
        })
    if not data:
        return []

    hits = []
    for r in ((data.get("resultList") or {}).get("result") or [])[:max_results]:
        abstract = (r.get("abstractText") or "").strip()
        if not abstract:
            continue
        # authorList is structured; authorString is a pre-joined fallback.
        people = [
            {"given": (a.get("firstName") or "").strip(), "family": (a.get("lastName") or "").strip()}
            for a in ((r.get("authorList") or {}).get("author") or [])
        ]
        people = [x for x in people if x["family"]]
        if not people and r.get("authorString"):
            people = [split_name(n.strip()) for n in r["authorString"].split(",")[:6] if n.strip()]
            people = [x for x in people if x["family"]]

        doi = (r.get("doi") or "").strip()
        pmcid = (r.get("pmcid") or "").strip()
        landing = (f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id', '')}"
                   if r.get("id") else (f"https://doi.org/{doi}" if doi else ""))
        venue = (r.get("journalTitle") or "").strip()
        hits.append(SearchHit(
            url=landing,
            title=(r.get("title") or "Untitled work").strip().rstrip("."),
            kind="paper", provider="europepmc",
            snippet=_strip_tags(abstract)[:800],
            domain=root_domain(landing) or "europepmc.org",
            publisher=_venue_or_host(venue, landing),
            type="paper",
            authors=join_names(people), authors_list=people,
            year=str(r.get("pubYear") or ""),
            venue=venue or "Preprint",
            cites=int(r.get("citedByCount") or 0),
            doi=doi,
            # Open-access full text sits behind a stable PMC URL when present.
            pdf_url=(f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""),
        ))
    return hits


# ---------- PubMed ----------

async def search_pubmed(query: str, max_results: int = 4) -> list[SearchHit]:
    """The authoritative biomedical index. Two calls by design: esearch returns
    IDs, esummary turns them into records -- that is simply how E-utilities
    works, and the alternative (efetch XML) is heavier for no gain here.

    PubMed summaries carry no abstract, so the snippet is built from the
    title and journal. Europe PMC usually covers the same paper WITH an
    abstract, and the URL-level dedupe in gather() keeps whichever arrives
    first, so this mostly adds reach into records Europe PMC lacks."""
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as client:
        found = await _get_json(client, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", {
            "db": "pubmed", "term": query, "retmax": max_results,
            "retmode": "json", "sort": "relevance",
        })
        ids = ((found or {}).get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return []
        summary = await _get_json(client, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi", {
            "db": "pubmed", "id": ",".join(ids), "retmode": "json",
        })
    if not summary:
        return []

    result = summary.get("result") or {}
    hits = []
    for pmid in ids:
        r = result.get(pmid)
        if not isinstance(r, dict):
            continue
        people = [split_name((a.get("name") or "")) for a in (r.get("authors") or [])]
        people = [x for x in people if x["family"]]
        venue = (r.get("fulljournalname") or r.get("source") or "").strip()
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        doi = ""
        for aid in (r.get("articleids") or []):
            if aid.get("idtype") == "doi":
                doi = aid.get("value", "")
                break
        hits.append(SearchHit(
            url=url,
            title=(r.get("title") or "Untitled work").strip().rstrip("."),
            kind="paper", provider="pubmed",
            snippet=f"{r.get('title', '')} {venue}. {r.get('pubdate', '')}".strip(),
            domain="pubmed.ncbi.nlm.nih.gov",
            publisher=_venue_or_host(venue, url),
            type="paper",
            authors=join_names(people), authors_list=people,
            year=str(r.get("pubdate", ""))[:4],
            venue=venue or "Journal",
            doi=doi,
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
    # The web index gets the QUESTION; the academic indexes get the KEYWORDS.
    # Tavily is a semantic search engine and reads a full sentence correctly --
    # stripping it to bare nouns would actively hurt it. OpenAlex, Crossref,
    # PubMed and friends are keyword engines and choke on the sentence. Same
    # intent, two encodings, because they are two different kinds of index.
    academic_q = keyword_query(query)
    terms = key_terms(query)
    if wants_web:
        jobs.append(search_web(tavily_key, query, per_provider + 1, include_domains, exclude_domains))
    if "academic" in kinds:
        # Six indexes, all fanned out at once. They overlap heavily -- the same
        # paper often comes back from four of them -- which is fine and in fact
        # the point: the DOI/URL dedupe below keeps the FIRST copy, and because
        # they are ordered best-metadata-first, the surviving record tends to
        # be the one with an abstract and an open-access PDF attached.
        jobs.extend([
            search_openalex(academic_q, per_provider),
            search_semanticscholar(academic_q, per_provider),
            search_europepmc(academic_q, per_provider),
            search_arxiv(academic_q, per_provider),
            search_crossref(academic_q, max(2, per_provider - 1)),
            search_pubmed(academic_q, max(2, per_provider - 1)),
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

    # Score, drop the unrelated, and order by relevance.
    #
    # WHY the ordering change matters as much as the filter: this list used to
    # come out in PROVIDER order, so the first four sources of every lane were
    # whatever OpenAlex happened to return, and everything downstream -- which
    # passages get read, which sources a section is written from -- takes the
    # front of this list. Ranking by relevance means the best material is the
    # material the writer actually sees.
    #
    # The filter is skipped when it would empty the list. A thin lane that the
    # gap pass can still rescue is a better outcome than a lane reported as
    # blocked because the scorer was too strict about a wording it did not
    # recognise.
    scored = [(relevance(h, terms), h) for h in merged]
    kept = [(s, h) for s, h in scored if s >= RELEVANCE_FLOOR]
    if not kept:
        log.info("relevance filter would empty %d hits for %r; keeping all", len(merged), query)
        kept = scored
    else:
        dropped = len(scored) - len(kept)
        if dropped:
            log.info("dropped %d of %d off-topic hits for %r", dropped, len(scored), query)
    kept.sort(key=lambda pair: pair[0], reverse=True)
    return [h for _, h in kept]
