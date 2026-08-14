"""Docker sandbox runner — one hardened way to run risky code.

Every container gets the same lockdown, and tools choose ONLY the network
policy. The flags, and what each one is for:

  network_mode "none"|"bridge"  — code execution gets NO network (stolen data
                                  has nowhere to go); research/finance get
                                  outbound HTTP because fetching IS their job
  read_only rootfs + tmpfs /tmp — payload can't persist anything or trojan the
                                  image for the next run
  cap_drop ALL                  — no Linux capabilities: no raw sockets, no
                                  mknod, no chown games
  no-new-privileges             — setuid binaries inside can't escalate
  mem/cpu/pids limits           — a fork bomb or 8GB allocation dies quietly
                                  instead of freezing the user's laptop
  user "nobody"                 — never root, even inside the sandbox

WHY docker SDK calls run in a thread: docker-py is synchronous; on the event
loop it would freeze all SSE streams while an image builds.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from core.errors import DockerUnavailableError

log = logging.getLogger(__name__)

IMAGES_DIR = Path(__file__).parent / "images"


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxRunner:
    def __init__(self):
        self._client = None

    def _docker(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    async def is_available(self) -> bool:
        try:
            await asyncio.to_thread(lambda: self._docker().ping())
            return True
        except Exception:
            self._client = None  # stale socket after Docker Desktop restart
            return False

    async def ensure_image(self, tag: str, dockerfile: str) -> None:
        def _build():
            client = self._docker()
            try:
                client.images.get(tag)
                return
            except Exception:
                pass
            log.info("building sandbox image %s (first use)…", tag)
            client.images.build(
                path=str(IMAGES_DIR), dockerfile=dockerfile, tag=tag, rm=True
            )

        try:
            await asyncio.to_thread(_build)
        except Exception as e:
            raise DockerUnavailableError(f"Could not build sandbox image {tag}: {e}") from e

    async def run(
        self,
        image: str,
        command: list[str],
        *,
        stdin_data: str | None = None,
        network: str = "none",
        timeout_s: int = 45,
        mem_limit: str = "512m",
    ) -> SandboxResult:
        if not await self.is_available():
            raise DockerUnavailableError()

        def _run() -> SandboxResult:
            client = self._docker()
            container = client.containers.create(
                image=image,
                command=command,
                network_mode=network,
                read_only=True,
                tmpfs={"/tmp": "size=64m"},
                mem_limit=mem_limit,
                nano_cpus=1_000_000_000,  # 1 CPU
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                user="nobody",
                environment={},  # empty on purpose: secrets NEVER enter a sandbox
                stdin_open=stdin_data is not None,
                detach=True,
            )
            try:
                if stdin_data is not None:
                    sock = container.attach_socket(params={"stdin": 1, "stream": 1})
                    container.start()
                    # WINDOWS. docker-py's attach_socket returns different
                    # objects per platform: on Unix a wrapper whose real socket
                    # hangs off `._sock`, on Windows an NpipeSocket that IS the
                    # socket and has no `._sock` at all. Reaching straight for
                    # `._sock` therefore raised
                    #
                    #   'NpipeSocket' object has no attribute '_sock'
                    #
                    # on every Windows machine — which meant run_python, both
                    # research fetch paths and all of Finance had never worked
                    # there. This app is Windows-first, so that was most of the
                    # sandboxed feature set.
                    raw = getattr(sock, "_sock", sock)
                    raw.sendall(stdin_data.encode())
                    # Half-close so the child sees EOF on stdin and stops
                    # reading. Not every transport implements shutdown, hence
                    # the guard; close() alone is enough for npipe.
                    try:
                        raw.shutdown(1)  # SHUT_WR
                    except (OSError, AttributeError, NotImplementedError):
                        pass
                    raw.close()
                else:
                    container.start()
                try:
                    res = container.wait(timeout=timeout_s)
                    exit_code = res.get("StatusCode", 1)
                    timed_out = False
                except Exception:
                    container.kill()
                    exit_code, timed_out = 124, True
                stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
                return SandboxResult(exit_code, stdout[-64_000:], stderr[-16_000:], timed_out)
            finally:
                container.remove(force=True)  # never leak containers

        return await asyncio.to_thread(_run)
