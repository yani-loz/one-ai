"""
Role: Per-request forensic context (client IP + request id) propagated via a ContextVar,
      so services can stamp audit-log rows WITHOUT threading the Request object through
      every call. Pure app.core — it must never import app.identity.
Used by: app.main (registers RequestContextMiddleware); app.identity.services.audit_service
         (reads current_request_context() when building an audit row).
Depends on: starlette (external). Leaf module within app.core.
Key invariants:
  - The context is set at the start of each HTTP request and RESET in a finally (ContextVar
    token reset), so it never leaks across the pooled worker between requests.
  - IP comes from request.client.host ONLY and is kept only if it parses as a real IP.
    X-Forwarded-For is NOT trusted here — a spoofed header would poison the trail's
    forensic value; behind a trusted proxy, wire XFF via a trusted-hops parser as a
    production step (docs/FIX_BEFORE_PROD.md).
  - request_id reuses an inbound X-Request-ID (proxy correlation) when present, else a
    fresh uuid4 hex; it is echoed back on the response for end-to-end correlation.
"""

from __future__ import annotations

import ipaddress
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Bound the request id to the audit_log.request_id column width (String(64)). The inbound
# X-Request-ID is an UNVALIDATED external header; if it reached a same-transaction audit
# INSERT over-length, Postgres would RAISE (varchar does not truncate) and roll back the
# whole request — undoing an otherwise-successful action. Clamping at the source keeps the
# request id a bounded, safe input. Must match String(64) in app.identity.models.audit_log.
REQUEST_ID_MAX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Forensic metadata about the current HTTP request (never any body content)."""

    ip_address: str | None
    request_id: str


_request_context: ContextVar[RequestContext | None] = ContextVar(
    "request_context", default=None
)


def current_request_context() -> RequestContext | None:
    """Return the current request's context, or None outside a request (CLI/tests/seed)."""
    return _request_context.get()


def _validated_client_ip(request: Request) -> str | None:
    """Return request.client.host iff it parses as a real IP, else None.

    Guards the audit row's ip_address so a non-IP peer label (e.g. the in-process ASGI
    test client) never reaches the column; X-Forwarded-For is deliberately ignored.
    """
    host = request.client.host if request.client is not None else None
    if host is None:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a RequestContext (client IP + request id) for the lifetime of each request."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap `app`; no configuration — context is derived per request."""
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Set the request context, run the request, then reset the ContextVar."""
        context = RequestContext(
            ip_address=_validated_client_ip(request),
            request_id=(request.headers.get("x-request-id") or uuid4().hex)[
                :REQUEST_ID_MAX_LENGTH
            ],
        )
        token = _request_context.set(context)
        try:
            response = await call_next(request)
        finally:
            _request_context.reset(token)
        response.headers["x-request-id"] = context.request_id
        return response
