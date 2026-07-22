"""Runs INSIDE the research container. stdin: JSON {"urls": [...]}.
stdout: one JSON object per line {"url","title","text","error"}.
Trafilatura strips nav/ads/scripts — the model should reason over prose,
not HTML soup (which also wastes precious local-model context)."""

import json
import sys

import httpx
import trafilatura

MAX_BYTES = 2_000_000
UA = "Mozilla/5.0 (compatible; ArthurResearch/1.0)"


def main() -> None:
    payload = json.loads(sys.stdin.read())
    urls = payload.get("urls", [])[:8]
    for url in urls:
        row = {"url": url, "title": "", "text": "", "error": None}
        try:
            if not url.startswith(("http://", "https://")):
                raise ValueError("only http(s) URLs")
            with httpx.Client(follow_redirects=True, timeout=20.0, headers={"User-Agent": UA}) as client:
                resp = client.get(url)
                resp.raise_for_status()
                html = resp.text[:MAX_BYTES]
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
