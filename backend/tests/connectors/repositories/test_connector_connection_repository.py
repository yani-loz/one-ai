"""
Role: Repository-layer cross-tenant isolation tests for ConnectorConnectionRepository — the
      letter of the testing.md non-negotiable at the repo layer (point 2's sync will call the
      repo/service directly, bypassing the route gate, so this is its load-bearing guard).
Used by: pytest (tests/connectors/repositories).
Depends on: the connectors conftest (connector_schema + db_session — a real DB), the model + repo.
Key invariants tested:
  - get_in_org / list_for_org / exists are all org-scoped: another org's id is None / excluded /
    absent. If any org_id filter were dropped, these fail.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from tests.conftest import seed_org


def _build_connection(org_id: UUID, username: str = "sales@example.com") -> ConnectorConnection:
    """Build a minimal valid connection row (ciphertext bytes are opaque to the repo)."""
    return ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name="Sales mailbox",
        auth_method="app_password",
        username=username,
        config={"host": "mail.example.com", "port": 993, "use_ssl": True},
        secret_ciphertext=b"\x00" * 32,
        secret_key_version=1,
        status="configured",
    )


async def test_get_in_org_other_org_returns_none(db_session: AsyncSession) -> None:
    repo = ConnectorConnectionRepository(db_session)
    org_a, org_b = await seed_org(), await seed_org()
    connection = await repo.insert(_build_connection(org_a))

    assert await repo.get_in_org(connection.id, org_b) is None
    assert await repo.get_in_org(connection.id, org_a) is not None


async def test_list_for_org_excludes_other_orgs(db_session: AsyncSession) -> None:
    repo = ConnectorConnectionRepository(db_session)
    org_a, org_b = await seed_org(), await seed_org()
    await repo.insert(_build_connection(org_a, "a@example.com"))

    assert await repo.list_for_org(org_b) == []
    assert len(await repo.list_for_org(org_a)) == 1


async def test_exists_is_org_scoped(db_session: AsyncSession) -> None:
    repo = ConnectorConnectionRepository(db_session)
    org_a, org_b = await seed_org(), await seed_org()
    await repo.insert(_build_connection(org_a, "shared@example.com"))

    assert await repo.exists(org_a, "imap", "shared@example.com") is True
    # Same (connector_type, username) in a DIFFERENT org must NOT collide (no cross-tenant leak).
    assert await repo.exists(org_b, "imap", "shared@example.com") is False
