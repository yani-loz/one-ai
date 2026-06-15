"""
Role: HTTP contract + tenancy tests for the /admin/connectors/{id}/sync endpoints — POST triggers
      (202) and GET polls the live status, the cross-tenant 404 (the non-negotiable), the disabled/
      already-running 409s, and the role + auth gates. The background runner is NEVER actually
      spawned: get_sync_task_spawner is overridden with a capture, so these tests exercise the real
      route → service → claim/ledger → DB path while asserting the spawn happened, not running it.
Used by: pytest (tests/connectors/sync). Real DB via the sync conftest (connection + cursor + run).
Depends on: the sync conftest schema, app.main, the spawn-override seam, the company-JWT helpers,
            the identity AuditLog model (the sync.started actor-plumbing read-back, report H-5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Coroutine
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.dependencies import get_sync_task_spawner
from app.identity.models.audit_log import AuditLog
from app.main import app
from tests.connectors.co01_seed import seed_entitled_org
from tests.connectors.conftest import bearer, company_token


def _payload() -> dict[str, object]:
    """A valid IMAP create-connection request body."""
    return {
        "connector_type": "imap",
        "display_name": "Sales mailbox",
        "host": "mail.example.com",
        "port": 993,
        "use_ssl": True,
        "username": "sales@example.com",
        "password": "imap-app-pw-123",
    }


@pytest.fixture
def spawn_calls() -> list[str]:
    """Collects the labels the route would have spawned (the runner itself never runs in-test)."""
    return []


@pytest_asyncio.fixture
async def sync_client(sync_schema: None, spawn_calls: list[str]) -> AsyncIterator[AsyncClient]:
    """An HTTP client whose sync spawner is captured — POST /sync claims + records, never runs."""

    def _capturing_spawner() -> object:
        def spawn(coro: Coroutine[Any, Any, None], *, label: str) -> None:
            spawn_calls.append(label)
            coro.close()  # we assert the spawn happened; we don't drive the background runner
            return None

        return spawn

    app.dependency_overrides[get_sync_task_spawner] = _capturing_spawner
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_sync_task_spawner, None)


async def _create(client: AsyncClient, token: str) -> str:
    """Create a connection via the real API and return its id."""
    created = await client.post("/admin/connectors", json=_payload(), headers=bearer(token))
    assert created.status_code == 201
    return created.json()["id"]


async def test_start_sync_returns_202_running_and_spawns(
    sync_client: AsyncClient, spawn_calls: list[str]
) -> None:
    token = company_token(uuid4(), await seed_entitled_org())
    connection_id = await _create(sync_client, token)

    response = await sync_client.post(
        f"/admin/connectors/{connection_id}/sync", headers=bearer(token)
    )

    assert response.status_code == 202
    body = response.json()
    assert body["sync_status"] == "running"
    assert body["run_id"] is not None
    assert body["synced_count"] == 0
    assert spawn_calls == [f"sync:{connection_id}"]


async def test_start_sync_on_a_disabled_connection_returns_409(
    sync_client: AsyncClient, spawn_calls: list[str]
) -> None:
    token = company_token(uuid4(), await seed_entitled_org())
    connection_id = await _create(sync_client, token)
    await sync_client.post(f"/admin/connectors/{connection_id}/disable", headers=bearer(token))

    response = await sync_client.post(
        f"/admin/connectors/{connection_id}/sync", headers=bearer(token)
    )

    assert response.status_code == 409
    assert spawn_calls == []  # never spawned a runner for a disabled connection


async def test_start_sync_when_already_running_returns_409(
    sync_client: AsyncClient, spawn_calls: list[str]
) -> None:
    token = company_token(uuid4(), await seed_entitled_org())
    connection_id = await _create(sync_client, token)
    first = await sync_client.post(f"/admin/connectors/{connection_id}/sync", headers=bearer(token))

    second = await sync_client.post(
        f"/admin/connectors/{connection_id}/sync", headers=bearer(token)
    )

    assert first.status_code == 202
    assert second.status_code == 409  # the fresh claim is still held
    assert spawn_calls == [f"sync:{connection_id}"]  # only the first triggered a spawn


async def test_start_sync_cross_tenant_returns_404(
    sync_client: AsyncClient, spawn_calls: list[str]
) -> None:
    # NON-NEGOTIABLE: org B triggering a sync on org A's connection is a 404, never a leak/spawn.
    org_a = company_token(uuid4(), await seed_entitled_org())
    connection_id = await _create(sync_client, org_a)
    org_b = company_token(uuid4(), await seed_entitled_org())

    response = await sync_client.post(
        f"/admin/connectors/{connection_id}/sync", headers=bearer(org_b)
    )

    assert response.status_code == 404
    assert spawn_calls == []
    assert "sales@example.com" not in response.text  # no B-visible leak of A's data


async def test_get_sync_status_returns_idle_for_a_new_connection(sync_client: AsyncClient) -> None:
    token = company_token(uuid4(), await seed_entitled_org())
    connection_id = await _create(sync_client, token)

    response = await sync_client.get(
        f"/admin/connectors/{connection_id}/sync", headers=bearer(token)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["connection_id"] == connection_id
    assert body["sync_status"] == "idle"
    assert body["synced_count"] == 0
    assert body["run_id"] is None


async def test_get_sync_status_cross_tenant_returns_404(sync_client: AsyncClient) -> None:
    # NON-NEGOTIABLE: org B reading org A's sync status is a 404, never a cross-org peek.
    org_a = company_token(uuid4(), await seed_entitled_org())
    connection_id = await _create(sync_client, org_a)
    org_b = company_token(uuid4(), await seed_entitled_org())

    response = await sync_client.get(
        f"/admin/connectors/{connection_id}/sync", headers=bearer(org_b)
    )

    assert response.status_code == 404
    assert "sales@example.com" not in response.text


async def test_start_sync_audit_row_carries_the_jwt_actor(
    sync_client: AsyncClient, db_session: AsyncSession
) -> None:
    # H-5 end to end: the route plumbs the verified principal into start_sync, so the
    # sync.started row records WHO triggered the run (never 'system' for a human trigger).
    user_id, org_id = uuid4(), await seed_entitled_org()
    token = company_token(user_id, org_id)
    connection_id = await _create(sync_client, token)

    response = await sync_client.post(
        f"/admin/connectors/{connection_id}/sync", headers=bearer(token)
    )

    assert response.status_code == 202
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "sync.started")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].actor_type == "user"
    assert rows[0].actor_id == user_id  # the JWT subject — not discarded at the route boundary
    assert str(rows[0].entity_id) == connection_id
    assert rows[0].details == {"run_id": response.json()["run_id"]}


async def test_start_sync_as_member_returns_403(sync_client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4(), role="member")

    response = await sync_client.post(f"/admin/connectors/{uuid4()}/sync", headers=bearer(token))

    assert response.status_code == 403


async def test_get_sync_status_missing_token_returns_401(sync_client: AsyncClient) -> None:
    response = await sync_client.get(f"/admin/connectors/{uuid4()}/sync")

    assert response.status_code == 401
