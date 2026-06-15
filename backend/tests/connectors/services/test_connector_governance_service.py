"""
Role: Service-layer tests for ConnectorGovernanceService — the entitlement-CEILING rule (AC6) and
      the §7 metadata-only health roll-up, exercised directly against real repos + a real DB
      (cheaper + more focused than driving the full HTTP route for the pure ceiling logic).
Used by: pytest (tests/connectors/services).
Depends on: the connectors conftest (connector_schema + db_session), tests.connectors.co01_seed
            (seed_entitlement / seed_user for the composite FK), the governance service + real
            policy/override/connection/entitlement repos + audit, exceptions, enums, Principal.
Key invariants tested:
  - set_org_wide(enabled=True) on a NON-entitled org raises ConnectorNotEntitledError (the ceiling);
    set_org_wide(enabled=False) is always allowed (you can restrict within your plan).
  - set_override(grant) on a non-entitled org raises; set_override(deny) is always allowed.
  - get_governance returns metadata-only connection entries (no host/username/secret attributes).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.enums import ConnectorType, OverrideType
from app.connectors.exceptions import ConnectorNotEntitledError
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_entitlement_repository import (
    ConnectorEntitlementRepository,
)
from app.connectors.repositories.connector_policy_override_repository import (
    ConnectorPolicyOverrideRepository,
)
from app.connectors.repositories.connector_policy_repository import ConnectorPolicyRepository
from app.connectors.services.connector_governance_service import ConnectorGovernanceService
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditService
from tests.connectors.co01_seed import seed_entitlement, seed_user


def _service(session: AsyncSession) -> ConnectorGovernanceService:
    """Build the governance service with real repos on one (global) test session."""
    return ConnectorGovernanceService(
        policies=ConnectorPolicyRepository(session),
        overrides=ConnectorPolicyOverrideRepository(session),
        connections=ConnectorConnectionRepository(session),
        entitlements=ConnectorEntitlementRepository(session),
        audit=AuditService(AuditRepository(session)),
    )


def _admin(org_id: UUID) -> Principal:
    return Principal(subject_id=uuid4(), org_id=org_id, role="company_admin", subject_type="user")


async def test_set_org_wide_enable_without_entitlement_raises(db_session: AsyncSession) -> None:
    org_id = uuid4()
    await seed_user(org_id, role="company_admin")  # registers the org
    service = _service(db_session)

    with pytest.raises(ConnectorNotEntitledError):
        await service.set_org_wide(
            org_id=org_id,
            connector_type=ConnectorType.imap,
            org_wide_enabled=True,
            actor=_admin(org_id),
        )


async def test_set_org_wide_disable_without_entitlement_is_allowed(
    db_session: AsyncSession,
) -> None:
    org_id = uuid4()
    await seed_user(org_id, role="company_admin")
    service = _service(db_session)

    result = await service.set_org_wide(
        org_id=org_id,
        connector_type=ConnectorType.imap,
        org_wide_enabled=False,
        actor=_admin(org_id),
    )

    assert result.org_wide_enabled is False  # disabling within-plan never needs entitlement


async def test_set_override_grant_without_entitlement_raises(db_session: AsyncSession) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    service = _service(db_session)

    with pytest.raises(ConnectorNotEntitledError):
        await service.set_override(
            org_id=org_id,
            user_id=user_id,
            connector_type=ConnectorType.imap,
            override_type=OverrideType.grant,
            actor=_admin(org_id),
        )


async def test_set_override_deny_without_entitlement_is_allowed(db_session: AsyncSession) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    service = _service(db_session)

    result = await service.set_override(
        org_id=org_id,
        user_id=user_id,
        connector_type=ConnectorType.imap,
        override_type=OverrideType.deny,
        actor=_admin(org_id),
    )

    assert any(o.override_type == "deny" for o in result.overrides)


async def test_get_governance_entitled_reflects_seeded_entitlement(
    db_session: AsyncSession,
) -> None:
    org_id = uuid4()
    await seed_user(org_id, role="company_admin")
    await seed_entitlement(org_id, enabled=True)
    service = _service(db_session)

    governance = await service.get_governance(org_id, ConnectorType.imap)

    assert governance.entitled is True
    assert governance.connections == []  # no connections yet
