"""
Integration test for /health. Requires a reachable Postgres (compose `db` or CI)
because the endpoint performs a real DB round-trip — that is the point of the probe.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok_and_db_reachable(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "reachable"
