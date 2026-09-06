"""
Role: Tests for the Ask layer's ONE true vendor boundary — the Together chat adapter. Covers
      the behaviour every caller assumes and nothing exercised: retry vs immediate failure,
      the per-call `usage_sink` accounting channel, and the promise that the API key never
      appears in a raised error.
Used by: pytest (tests/ask/adapters). No database and no network — the httpx transport is
      mocked at the boundary, which is exactly where testing.md says vendor mocks belong.
Depends on: app.ask.adapters.together_chat, httpx.MockTransport, app.core.config.
Key invariants:
  - Mocks the TRANSPORT, not the adapter's own methods. Mocking `chat` would test nothing;
    the whole point of this file is the retry/extract logic between the call and the caller.
  - Backoff is patched to return immediately. Real sleeps would make the retry cases take
    14 seconds and the suite would grow a reason to delete them.
  - `usage_sink` is asserted to receive THIS call's tokens only. It exists because a shared
    cumulative counter over-counted under concurrency (harness finding N11), so a test that
    only checked `total_usage` would pass while the bug it was written for came back.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ask.adapters import together_chat
from app.ask.exceptions import ReaderModelError

_ONE_REPLY = {
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
}


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the exponential backoff instant so retry paths cost no wall-clock."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(together_chat.asyncio, "sleep", _instant)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter refuses to construct without a key; supply a fake one."""
    settings = together_chat.get_settings()
    monkeypatch.setattr(settings, "together_api_key", "test-key-not-a-real-secret")


def _client_with(responses: list[httpx.Response], seen: list[httpx.Request]) -> Any:
    """A TogetherChatClient whose transport replays `responses` in order."""

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    client = together_chat.TogetherChatClient()
    original = httpx.AsyncClient

    class _MockedAsyncClient(original):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(_handler), **kwargs)

    together_chat.httpx.AsyncClient = _MockedAsyncClient  # type: ignore[misc]
    return client


@pytest.fixture(autouse=True)
def _restore_httpx() -> Any:
    """Put httpx.AsyncClient back after each test that swapped it."""
    original = httpx.AsyncClient
    yield
    together_chat.httpx.AsyncClient = original  # type: ignore[misc]


async def test_a_retryable_status_is_retried_and_then_succeeds() -> None:
    seen: list[httpx.Request] = []
    client = _client_with([httpx.Response(429), httpx.Response(200, json=_ONE_REPLY)], seen)

    message = await client.chat(messages=[{"role": "user", "content": "hi"}])

    assert message["content"] == "hello"
    assert len(seen) == 2


async def test_a_non_retryable_status_fails_immediately() -> None:
    # A 400 is a caller bug, not a transient fault: retrying it three more times wastes the
    # budget and delays the real error.
    seen: list[httpx.Request] = []
    client = _client_with([httpx.Response(400)], seen)

    with pytest.raises(ReaderModelError):
        await client.chat(messages=[{"role": "user", "content": "hi"}])

    assert len(seen) == 1


async def test_persistent_failure_raises_after_the_attempt_cap() -> None:
    seen: list[httpx.Request] = []
    client = _client_with([httpx.Response(503)], seen)

    with pytest.raises(ReaderModelError):
        await client.chat(messages=[{"role": "user", "content": "hi"}])

    assert len(seen) == together_chat._MAX_ATTEMPTS  # noqa: SLF001


async def test_the_api_key_never_appears_in_a_raised_error() -> None:
    seen: list[httpx.Request] = []
    client = _client_with([httpx.Response(400)], seen)

    with pytest.raises(ReaderModelError) as raised:
        await client.chat(messages=[{"role": "user", "content": "hi"}])

    assert "test-key-not-a-real-secret" not in str(raised.value)


async def test_usage_sink_receives_only_this_calls_tokens() -> None:
    # The sink exists because a shared cumulative counter over-counted under concurrency: a
    # test that only checked the running total would pass while that bug came back.
    seen: list[httpx.Request] = []
    client = _client_with([httpx.Response(200, json=_ONE_REPLY)], seen)
    sink: dict[str, int] = {}

    await client.chat(messages=[{"role": "user", "content": "hi"}], usage_sink=sink)
    await client.chat(messages=[{"role": "user", "content": "again"}])

    assert sink == {"prompt_tokens": 11, "completion_tokens": 7}


async def test_a_body_without_a_message_is_a_reader_error_not_a_key_error() -> None:
    seen: list[httpx.Request] = []
    client = _client_with([httpx.Response(200, json={"usage": {}})], seen)

    with pytest.raises(ReaderModelError):
        await client.chat(messages=[{"role": "user", "content": "hi"}])
