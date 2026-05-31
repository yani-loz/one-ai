"""
Role: Lightweight ASGI middleware — a coarse request-body size guard.
Used by: app.main (registered on the FastAPI app from settings.max_request_body_bytes).
Depends on: starlette (external). Leaf module within the project.
Key invariants:
  - Rejects any request whose DECLARED Content-Length exceeds the configured limit with
    413 before a route runs — a cheap DoS guard. It does not cover chunked / missing-
    Content-Length bodies; full enforcement belongs at the reverse proxy (see
    docs/FIX_BEFORE_PROD.md).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject requests whose declared Content-Length exceeds `max_bytes` with 413."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        """Wrap `app`, rejecting bodies whose Content-Length exceeds `max_bytes`."""
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Return 413 when the declared Content-Length exceeds the limit, else proceed."""
        content_length = request.headers.get("content-length")
        if content_length is not None and content_length.isdigit():
            if int(content_length) > self._max_bytes:
                return JSONResponse(
                    status_code=413, content={"detail": "Request body too large."}
                )
        return await call_next(request)
