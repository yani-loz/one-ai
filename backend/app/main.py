"""
Role: FastAPI application factory + composition root. Wires middleware, routers, and
      domain exception handlers.
Used by: uvicorn (app.main:app); tests import the ASGI app.
Depends on: app.core.config, app.core.database, app.core.middleware,
            app.api.routes.*, app.identity.
Key invariants:
  - The ONLY place routers, middleware, and exception handlers are registered.
  - CORS origins come from settings, never hardcoded.
  - main.py may import app.identity (it is the composition root, not core) — the
    strict rule is that app.core must never import app.identity.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError

from app.api.routes.health import router as health_router
from app.connectors import connectors_router, register_connector_exception_handlers
from app.core.config import get_settings
from app.core.database import engine
from app.core.middleware import MaxBodySizeMiddleware
from app.core.request_context import RequestContextMiddleware
from app.identity import identity_router, register_identity_exception_handlers


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: dispose the DB engine cleanly on shutdown."""
    yield
    await engine.dispose()


async def _handle_data_error(_request: Request, _exc: Exception) -> JSONResponse:
    """Map an unexpected bad-data DB error to 422 instead of leaking a 500 (DYN-03).

    Defense in depth behind the request validators: if a value they missed (e.g. an odd
    encoding) reaches the INSERT and asyncpg raises a DataError, the caller gets a clean
    422 rather than an opaque Internal Server Error.
    """
    return JSONResponse(status_code=422, content={"detail": "Invalid input value."})


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Bind the per-request forensic context (client IP + request id) for audit rows, then
    # reset the ContextVar after each request. Sits inside the body-size guard.
    app.add_middleware(RequestContextMiddleware)
    # Added last → outermost: reject oversized bodies before anything buffers them.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_request_body_bytes)
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(connectors_router)
    register_identity_exception_handlers(app)
    register_connector_exception_handlers(app)
    app.add_exception_handler(DataError, _handle_data_error)
    return app


app = create_app()
