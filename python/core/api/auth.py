"""Bearer-token auth for the local API.

THE THREAT: "localhost" is not private. Any process on the machine — and any
browser tab, via JavaScript — can send requests to 127.0.0.1. Without auth,
a random website could read chat history or invoke computer control while
Arthur is running. This is the most commonly shipped vulnerability in
Electron+local-server apps.

THE FIX: Electron generates a random token per launch and hands it to this
process via environment variable; every request must present it. A web page
can't read the token (it lives in Electron's main process), so its requests
fail with 401.

WHY compare_digest: == short-circuits on the first differing byte, which
leaks how much of a guess was right through response timing. compare_digest
is constant-time. On localhost an attacker can time requests very precisely,
so this actually matters here.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    expected = request.app.state.arthur.settings.auth_token
    provided = credentials.credentials if credentials else ""
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="missing or invalid token")
