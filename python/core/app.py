"""FastAPI application factory.

WHY a factory taking optional pre-built state: production calls create_app()
and gets the real dependency graph; tests call create_app(state=fake_state)
and get the identical app wired to fakes. Same code path, different edges —
that's what makes the API tests honest.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from core.api.routes import public, router
from core.config import Settings, get_settings
from core.deps import AppState, build_state
from core.errors import ArthurError

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, state: AppState | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.arthur = state or await build_state(settings)
        log.info("Arthur backend ready on %s:%s (scanner=%s)",
                 settings.host, settings.port, app.state.arthur.gateway.backend_name)
        yield
        await app.state.arthur.db.close()

    app = FastAPI(title="Arthur Backend", version="0.2.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)  # no public API surface to advertise

    # DNS-rebinding defense: a malicious site pointing evil.com at 127.0.0.1
    # produces requests with Host: evil.com — rejected here before any route.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    # The renderer's origin differs from the API's (file:// in prod -> "null",
    # the Vite server in dev), so CORS must allow exactly those two. The auth
    # token — which browsers can't obtain — is the actual security boundary;
    # CORS is scoping, not the lock.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["null", "http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(ArthurError)
    async def arthur_error_handler(_request: Request, exc: ArthurError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, **exc.detail}},
        )

    app.include_router(public)
    app.include_router(router)
    return app
