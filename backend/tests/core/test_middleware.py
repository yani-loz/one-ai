"""Tests for app.core.middleware."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.core.middleware import MaxBodySizeMiddleware


async def _echo_body_size_app(scope: Scope, receive: Receive, send: Send) -> None:
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        size += len(message.get("body", b""))
        if not message.get("more_body", False):
            break
    await JSONResponse({"bytes": size})(scope, receive, send)


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }


async def _run_app(
    app: Callable[[Scope, Receive, Send], Awaitable[None]],
    body_messages: list[Message],
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[Message]:
    messages = list(body_messages)
    sent: list[Message] = []

    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(_scope(headers), receive, send)
    return sent


def _status(sent: list[Message]) -> int:
    for message in sent:
        if message["type"] == "http.response.start":
            return int(message["status"])
    raise AssertionError("No response status sent")


def _body(sent: list[Message]) -> bytes:
    return b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )


async def test_declared_oversized_body_returns_413_before_app_runs() -> None:
    called = False

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True
        await _echo_body_size_app(scope, receive, send)

    middleware = MaxBodySizeMiddleware(app, max_bytes=1024)

    sent = await _run_app(
        middleware,
        [{"type": "http.request", "body": b"", "more_body": False}],
        headers=[(b"content-length", b"2048")],
    )

    assert _status(sent) == 413
    assert called is False


async def test_streamed_body_without_content_length_is_counted_and_rejected() -> None:
    middleware = MaxBodySizeMiddleware(_echo_body_size_app, max_bytes=1024)

    sent = await _run_app(
        middleware,
        [
            {"type": "http.request", "body": b"a" * 700, "more_body": True},
            {"type": "http.request", "body": b"b" * 400, "more_body": False},
        ],
    )

    assert _status(sent) == 413
    assert b"Request body too large" in _body(sent)


async def test_streamed_body_at_limit_reaches_app() -> None:
    middleware = MaxBodySizeMiddleware(_echo_body_size_app, max_bytes=1024)

    sent = await _run_app(
        middleware,
        [
            {"type": "http.request", "body": b"a" * 512, "more_body": True},
            {"type": "http.request", "body": b"b" * 512, "more_body": False},
        ],
    )

    assert _status(sent) == 200
    assert json.loads(_body(sent)) == {"bytes": 1024}
