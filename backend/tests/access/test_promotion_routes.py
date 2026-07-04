"""
Role: HTTP-level tests for POST /access/promotions (PF-01 AC13 at the wire) — the owner's
      promotion succeeds end-to-end through the person-scoped session dependency, and the
      NON-NEGOTIABLE cross-tenant negative: org B's admin probing org A's message id gets 404
      (never 200-empty, never an existence leak); a same-org non-owner gets 403.
Used by: pytest (tests/access). Real ASGI app + DB + JWTs (no auth mocking).
Depends on: the root `client` fixture, tests.identity.conftest token helpers, the access
      conftest seed helpers.
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailMessage
from tests.access.conftest import (
    bind_verified_identity,
    grant_email_access,
    seed_connection,
    seed_email_message,
    seed_person,
    seed_user,
)
from tests.identity.conftest import bearer, company_token


async def _seed_promotable_message(db_session: AsyncSession, org):
    """Owner user + owned connection + restricted message; the owner is grant-bound to see it."""
    owner = await seed_user(db_session, org, email=f"owner-{org.hex[:8]}@acme.test")
    connection = await seed_connection(db_session, org, owner_user_id=owner.id)
    owner_person = await seed_person(db_session, org, "Owner Person")
    await bind_verified_identity(db_session, org, owner_person.id, "auth", str(owner.id))
    message = await seed_email_message(db_session, org, connection.id)
    await grant_email_access(
        db_session, org, owner_person.id, message.id, connection.id, provenance="owner"
    )
    await db_session.commit()
    return owner, message


async def test_owner_promotes_over_http(client: AsyncClient, db_session: AsyncSession) -> None:
    org = uuid4()
    owner, message = await _seed_promotable_message(db_session, org)

    response = await client.post(
        "/access/promotions",
        json={"object_type": "email_message", "object_id": str(message.id)},
        headers=bearer(company_token(owner.id, org, "member")),
    )

    assert response.status_code == 201
    assert "promotion_id" in response.json()
    flipped = (
        await db_session.execute(
            select(EmailMessage.visibility_scope).where(EmailMessage.id == message.id)
        )
    ).scalar_one()
    assert flipped == "org"


async def test_cross_tenant_promotion_probe_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Authenticate as org B, attempt to promote org A's message: 404, and org A's row untouched.
    org_a, org_b = uuid4(), uuid4()
    _owner_a, message_a = await _seed_promotable_message(db_session, org_a)
    intruder = await seed_user(db_session, org_b, email="intruder@beta.test")
    await db_session.commit()

    response = await client.post(
        "/access/promotions",
        json={"object_type": "email_message", "object_id": str(message_a.id)},
        headers=bearer(company_token(intruder.id, org_b, "company_admin")),
    )

    assert response.status_code == 404
    assert (
        "restricted"
        == (
            await db_session.execute(
                select(EmailMessage.visibility_scope).where(EmailMessage.id == message_a.id)
            )
        ).scalar_one()
    )


async def test_granted_non_owner_can_see_but_not_widen_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A RECIPIENT (grant-holder, verified auth binding) can SEE the message — but widening
    # belongs to the connection owner alone: 403, and the scope stays restricted.
    org = uuid4()
    _owner, message = await _seed_promotable_message(db_session, org)
    colleague = await seed_user(db_session, org, email="colleague@acme.test")
    colleague_person = await seed_person(db_session, org, "Colleague")
    await bind_verified_identity(db_session, org, colleague_person.id, "auth", str(colleague.id))
    await grant_email_access(db_session, org, colleague_person.id, message.id)
    await db_session.commit()

    response = await client.post(
        "/access/promotions",
        json={"object_type": "email_message", "object_id": str(message.id)},
        headers=bearer(company_token(colleague.id, org, "member")),
    )

    assert response.status_code == 403
    assert (
        "restricted"
        == (
            await db_session.execute(
                select(EmailMessage.visibility_scope).where(EmailMessage.id == message.id)
            )
        ).scalar_one()
    )


async def test_ungranted_non_owner_promotion_is_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Promotion runs on the WRITE plane (the row is loadable there); the ownership rule is the
    # authz: any same-org non-owner — granted or not — gets 403 and the scope stays restricted.
    org = uuid4()
    _owner, message = await _seed_promotable_message(db_session, org)
    colleague = await seed_user(db_session, org, email="ungranted@acme.test")
    await db_session.commit()

    response = await client.post(
        "/access/promotions",
        json={"object_type": "email_message", "object_id": str(message.id)},
        headers=bearer(company_token(colleague.id, org, "member")),
    )

    assert response.status_code == 403
