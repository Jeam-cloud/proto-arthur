# Finance sandbox: yfinance calls isolated from everything else.
# yfinance is an unofficial scraper of Yahoo endpoints — precisely the kind of
# dependency that should run where it can't touch anything if it misbehaves.
FROM python:3.12-slim
RUN pip install --no-cache-dir yfinance && useradd -m runner
COPY scripts/finance_query.py /app/finance_query.py
USER nobody
ENTRYPOINT ["python", "/app/finance_query.py"]
