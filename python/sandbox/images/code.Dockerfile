# Code-execution sandbox: run AI-generated Python with NO network.
# Combined with read-only rootfs and dropped capabilities, the worst a
# malicious snippet can do is burn its own CPU allowance for 45 seconds.
FROM python:3.12-slim
RUN useradd -m runner
USER nobody
# code arrives on stdin; -I = isolated mode (ignores env vars & user site-packages)
ENTRYPOINT ["python", "-I", "-"]
