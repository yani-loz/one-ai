"""
Role: Shared pytest fixtures for the backend suite.
Used by: all tests under backend/tests/.
Depends on: httpx, app.main.
Key invariants:
  - `client` talks to the real ASGI app via in-process transport (no network).
  - DB-backed tests require a reachable Postgres (compose `db` service or CI).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncIterator[None]:
    """Dispose the shared async engine after each test.

    pytest-asyncio runs each test in its own event loop; asyncpg connections are
    bound to the loop that created them. Without disposal, a pooled connection
    from one test is reused by the next test's loop and fails. Disposing keeps
    every test's DB access on its own loop.
    """
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client bound to the in-process ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
