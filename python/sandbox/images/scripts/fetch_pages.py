"""Runs INSIDE the research container. stdin: JSON {"urls": [...]}.
stdout: one JSON object per line {"url","title","text","error"}.
Trafilatura strips nav/ads/scripts — the model should reason over prose,
not HTML soup (which also wastes precious local-model context).

PDFs are handled here too, because that is what a paper actually is. A
research tool that only reads HTML can never read the primary source; it can
only read blog posts about the primary source. Detection is by Content-Type
first and file extension second — plenty of arXiv-style URLs serve a PDF from
a path that does not end in .pdf."""

import io
import json
import sys

import httpx
import trafilatura

MAX_BYTES = 2_000_000
MAX_PDF_PAGES = 40  # a 300-page thesis would blow the model's context anyway
UA = "Mozilla/5.0 (compatible; ArthurResearch/1.0)"


def _pdf_text(data: bytes) -> tuple[str, int]:
    """Returns (text, pages_actually_read). The page count is a REAL number
    read off the PDF, not an estimate -- it becomes the "14p read" badge on
    the evidence card, and that badge is only trustworthy if it never lies."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    read = reader.pages[:MAX_PDF_PAGES]
    pages = [(p.extract_text() or "") for p in read]
    return "\n\n".join(pages), len(read)


def _read_request() -> dict:
    """The request, from argv if given, else stdin.

    ARGV IS THE PRIMARY PATH ON PURPOSE. Piping stdin into a container relies
    on docker-py's attach_socket, which behaves differently on Windows named
    pipes than on Unix sockets — and when the write does not land, this script
    blocks in stdin.read() waiting for an EOF that never arrives, prints
    nothing, and is killed by the timeout. The failure looks like a slow fetch
    while no fetch was ever attempted.

    A list of URLs fits in argv comfortably. Docker receives it as a list and
    execs directly, so nothing is shell-interpreted.

    stdin stays as a fallback so an older image and a newer app still work.
    """
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return json.loads(sys.argv[1])
    return json.loads(sys.stdin.read())


def main() -> None:
    payload = _read_request()
    urls = payload.get("urls", [])[:8]
    for url in urls:
        row = {"url": url, "title": "", "text": "", "error": None, "is_pdf": False, "pages": 0}
        try:
            if not url.startswith(("http://", "https://")):
                raise ValueError("only http(s) URLs")
            with httpx.Client(follow_redirects=True, timeout=25.0, headers={"User-Agent": UA}) as client:
                resp = client.get(url)
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").lower()
                is_pdf = "application/pdf" in ctype or url.lower().split("?")[0].endswith(".pdf")
                raw = resp.content[:MAX_BYTES]
                html = "" if is_pdf else resp.text[:MAX_BYTES]

            if is_pdf:
                row["is_pdf"] = True
                row["title"] = url.rsplit("/", 1)[-1] or url
                text, pages_read = _pdf_text(raw)
                row["text"] = text[:40_000]
                row["pages"] = pages_read
            else:
                extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
                meta = trafilatura.extract_metadata(html)
                row["title"] = (meta.title if meta else "") or url
                row["text"] = (extracted or "")[:40_000]
        except Exception as e:  # one bad page must not sink the batch
            row["error"] = str(e)[:200]
        sys.stdout.write(json.dumps(row) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
