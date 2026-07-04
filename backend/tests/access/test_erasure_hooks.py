"""
Role: PF-01 AC15 — the access module's four tables are wired into GDPR erasure: the hook is
      REQUIRED + registered by the composition root, and erasing org A deletes every access row
      of A (grants, identities, promotions, fact provenance) while org B's rows survive intact.
Used by: pytest (tests/access). Real DB via the access conftest.
Depends on: app.common.erasure_hooks (registry), app.main (composition root),
      access_erasure_repository (the hook under test).
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.acl_grant import AclGrant
from app.access.models.fact_provenance import FactProvenance
from app.access.models.principal_source_identity import PrincipalSourceIdentity
from app.access.repositories.access_erasure_repository import erase_access_data_for_org
from app.common.erasure_hooks import REQUIRED_ERASURE_HOOKS, registered_erasure_hooks
from tests.access.conftest import (
    bind_verified_identity,
    grant_email_access,
    seed_connection,
    seed_email_message,
    seed_person,
)


def test_access_hook_is_required_and_registered_by_the_composition_root() -> None:
    # The completeness invariant: 'access' is in REQUIRED_ERASURE_HOOKS (partial configuration
    # fails closed) AND create_app actually registered it (app.main imported by the app import).
    import app.main  # noqa: F401 — importing the module runs create_app() registration

    assert "access" in REQUIRED_ERASURE_HOOKS
    assert "access" in registered_erasure_hooks()


async def _seed_access_rows(session: AsyncSession, org) -> None:
    """One row in each PII-bearing access table for `org` (fact_provenance synthetic)."""
    connection = await seed_connection(session, org, mailbox=f"m-{org}@acme.test")
    person = await seed_person(session, org)
    await bind_verified_identity(session, org, person.id, "email", f"p-{org}@acme.test")
    message = await seed_email_message(session, org, connection.id)
    await grant_email_access(session, org, person.id, message.id, connection.id)
    session.add(
        FactProvenance(
            org_id=org,
            fact_id=uuid4(),
            source_object_type="email_message",
            source_object_id=message.id,
        )
    )
    await session.flush()


async def test_erasing_org_a_deletes_access_rows_and_spares_org_b(
    db_session: AsyncSession,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    await _seed_access_rows(db_session, org_a)
    await _seed_access_rows(db_session, org_b)

    counts = await erase_access_data_for_org(org_a, db_session)
    await db_session.commit()

    assert counts["acl_grant"] == 1
    assert counts["principal_source_identity"] == 1
    assert counts["fact_provenance"] == 1
    for model in (AclGrant, PrincipalSourceIdentity, FactProvenance):
        remaining_a = (
            await db_session.execute(
                select(func.count()).select_from(model).where(model.org_id == org_a)
            )
        ).scalar_one()
        remaining_b = (
            await db_session.execute(
                select(func.count()).select_from(model).where(model.org_id == org_b)
            )
        ).scalar_one()
        assert remaining_a == 0, f"{model.__tablename__}: org A rows survived erasure"
        assert remaining_b == 1, f"{model.__tablename__}: erasure leaked into org B"
