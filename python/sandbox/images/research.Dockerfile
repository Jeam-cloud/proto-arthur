# Research sandbox: fetch + extract readable text from web pages.
# Outbound HTTP is this container's entire purpose; it still runs read-only,
# unprivileged, memory-capped, and receives NO credentials (the Tavily key is
# used by the backend process itself — search happens outside, fetching inside).
FROM python:3.12-slim
RUN pip install --no-cache-dir httpx[http2] trafilatura pypdf && \
    useradd -m runner
COPY scripts/fetch_pages.py /app/fetch_pages.py
USER nobody
ENTRYPOINT ["python", "/app/fetch_pages.py"]
