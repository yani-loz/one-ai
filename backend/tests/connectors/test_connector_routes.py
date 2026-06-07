"""
Role: HTTP contract + tenancy tests for the /connectors endpoints — configure/list/get/test/
      delete, the cross-tenant 404 (the non-negotiable), role + auth gates, and no-secret-leak.
Used by: pytest (tests/connectors).
Depends on: the connectors conftest (DB schema fixture, fake-registry client, JWT helpers).
Key invariants tested:
  - A company_admin manages ONLY their own org's connections; another org's connection id is a
    404 for read/test/delete (never a 403/200-empty existence leak, never B-data in the body).
  - The credential never appears in any response body.
  - member -> 403, missing token -> 401, duplicate -> 409, test reports status without raising.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient

import app.connectors.dependencies as connector_dependencies
from app.connectors.base.connector import ConnectionCheck
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


async def test_create_connection_returns_201_without_secret(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4())

    response = await client.post("/connectors", json=_payload(), headers=bearer(token))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "configured"
    assert body["username"] == "sales@example.com"
    assert body["host"] == "mail.example.com"
    assert "password" not in body
    assert "imap-app-pw-123" not in response.text


async def test_create_duplicate_connection_returns_409(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4())
    await client.post("/connectors", json=_payload(), headers=bearer(token))

    response = await client.post("/connectors", json=_payload(), headers=bearer(token))

    assert response.status_code == 409


async def test_list_returns_the_orgs_connections(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4())
    await client.post("/connectors", json=_payload(), headers=bearer(token))

    response = await client.get("/connectors", headers=bearer(token))

    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_get_unknown_connection_returns_404(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4())

    response = await client.get(f"/connectors/{uuid4()}", headers=bearer(token))

    assert response.status_code == 404


async def test_test_connection_success_sets_status_connected(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4())
    created = await client.post("/connectors", json=_payload(), headers=bearer(token))
    connection_id = created.json()["id"]

    response = await client.post(f"/connectors/{connection_id}/test", headers=bearer(token))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["last_error"] is None


async def test_test_connection_failure_sets_status_error(
    client: AsyncClient, stub_outcome: dict[str, ConnectionCheck]
) -> None:
    stub_outcome["check"] = ConnectionCheck(
        ok=False, message="Authentication failed — check the username and password."
    )
    token = company_token(uuid4(), uuid4())
    created = await client.post("/connectors", json=_payload(), headers=bearer(token))
    connection_id = created.json()["id"]

    response = await client.post(f"/connectors/{connection_id}/test", headers=bearer(token))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "uthentication" in body["last_error"]


async def test_delete_connection_returns_204_then_404(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4())
    created = await client.post("/connectors", json=_payload(), headers=bearer(token))
    connection_id = created.json()["id"]

    delete_response = await client.delete(f"/connectors/{connection_id}", headers=bearer(token))
    get_response = await client.get(f"/connectors/{connection_id}", headers=bearer(token))

    assert delete_response.status_code == 204
    assert get_response.status_code == 404


async def test_cannot_read_another_orgs_connection_returns_404(client: AsyncClient) -> None:
    org_a = company_token(uuid4(), uuid4())
    created = await client.post("/connectors", json=_payload(), headers=bearer(org_a))
    connection_id = created.json()["id"]
    org_b = company_token(uuid4(), uuid4())

    get_b = await client.get(f"/connectors/{connection_id}", headers=bearer(org_b))
    test_b = await client.post(f"/connectors/{connection_id}/test", headers=bearer(org_b))
    delete_b = await client.delete(f"/connectors/{connection_id}", headers=bearer(org_b))

    assert get_b.status_code == 404
    assert test_b.status_code == 404
    assert delete_b.status_code == 404
    # Org B never sees Org A's data in any field.
    assert "sales@example.com" not in get_b.text


async def test_another_org_cannot_delete_then_owner_still_has_it(client: AsyncClient) -> None:
    org_a = company_token(uuid4(), uuid4())
    created = await client.post("/connectors", json=_payload(), headers=bearer(org_a))
    connection_id = created.json()["id"]
    org_b = company_token(uuid4(), uuid4())

    await client.delete(f"/connectors/{connection_id}", headers=bearer(org_b))
    owner_get = await client.get(f"/connectors/{connection_id}", headers=bearer(org_a))

    assert owner_get.status_code == 200  # B's delete did not touch A's row


async def test_member_cannot_manage_connections_returns_403(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4(), role="member")

    response = await client.get("/connectors", headers=bearer(token))

    assert response.status_code == 403


async def test_missing_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/connectors")

    assert response.status_code == 401


async def test_list_excludes_other_orgs_connections(client: AsyncClient) -> None:
    org_a = company_token(uuid4(), uuid4())
    await client.post("/connectors", json=_payload(), headers=bearer(org_a))
    org_b = company_token(uuid4(), uuid4())

    response = await client.get("/connectors", headers=bearer(org_b))

    assert response.status_code == 200
    assert response.json() == []
    assert "sales@example.com" not in response.text


async def test_duplicate_check_is_org_scoped(client: AsyncClient) -> None:
    org_a = company_token(uuid4(), uuid4())
    await client.post("/connectors", json=_payload(), headers=bearer(org_a))
    org_b = company_token(uuid4(), uuid4())

    # The SAME (connector_type, username) in a DIFFERENT org must be allowed, not a 409 — a 409
    # here would both leak that org A configured this mailbox and deny org B.
    response = await client.post("/connectors", json=_payload(), headers=bearer(org_b))

    assert response.status_code == 201


async def test_member_cannot_create_connection_returns_403(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4(), role="member")

    response = await client.post("/connectors", json=_payload(), headers=bearer(token))

    assert response.status_code == 403


async def test_test_connection_failure_persists_to_the_row(
    client: AsyncClient, stub_outcome: dict[str, ConnectionCheck]
) -> None:
    stub_outcome["check"] = ConnectionCheck(
        ok=False, message="Authentication failed — check the username and password."
    )
    token = company_token(uuid4(), uuid4())
    connection_id = (
        await client.post("/connectors", json=_payload(), headers=bearer(token))
    ).json()["id"]

    await client.post(f"/connectors/{connection_id}/test", headers=bearer(token))
    persisted = await client.get(f"/connectors/{connection_id}", headers=bearer(token))

    assert persisted.json()["status"] == "error"
    assert "uthentication" in persisted.json()["last_error"]


async def test_connector_endpoint_returns_503_when_key_insecure_in_nondev(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a non-dev env still using the dev/weak connector key: the cipher fails closed and
    # the endpoint must surface 503 (not a leaked 500), without echoing the key.
    monkeypatch.setattr(
        connector_dependencies,
        "get_settings",
        lambda: SimpleNamespace(
            connector_secret_key="dev-only-insecure-connector-secret-change-me",
            requires_secure_secrets=True,
        ),
    )
    token = company_token(uuid4(), uuid4())

    response = await client.post("/connectors", json=_payload(), headers=bearer(token))

    assert response.status_code == 503
    assert "dev-only-insecure-connector-secret-change-me" not in response.text
