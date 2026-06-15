"""
Role: Lightweight ASGI middleware - request-body size guard.
Used by: app.main (registered on the FastAPI app from settings.max_request_body_bytes).
Depends on: starlette (external). Leaf module within the project.
Key invariants:
  - Rejects requests whose declared Content-Length exceeds the configured limit with
    413 before a route runs.
  - Counts streamed body bytes too, so chunked / missing-Content-Length requests cannot
    bypass the application limit.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class MaxBodySizeMiddleware:
    """Reject HTTP request bodies larger than `max_bytes` with 413."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        """Wrap `app`, rejecting bodies whose declared or streamed size exceeds `max_bytes`."""
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Return 413 when the declared or streamed body exceeds the limit."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None and content_length.isdigit():
            if int(content_length) > self._max_bytes:
                await self._reject(scope, receive, send)
                return

        received = 0
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] != "http.request":
                return message

            received += len(message.get("body", b""))
            if received <= self._max_bytes:
                return message

            rejected = True
            await self._reject(scope, receive, send)
            return {"type": "http.disconnect"}

        async def guarded_send(message: Message) -> None:
            if not rejected:
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if rejected:
                return
            raise

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(status_code=413, content={"detail": "Request body too large."})
        await response(scope, receive, send)
