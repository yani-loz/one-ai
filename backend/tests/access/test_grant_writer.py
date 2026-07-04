"""
Role: Grant-capture tests (PF-01 AC8/AC10/AC21) through the REAL ingest path — UNKNOWN ⇒ DENY
      (unverified/unmappable principals write no grant), verified recipients + the connection
      owner get per-message grants, and reconciliation tombstones principals that drop out
      (SHRINK ⇒ TOMBSTONE — without any object deletion).
Used by: pytest (tests/access). Real DB via the access conftest.
Depends on: EmailIngestService (the production capture call site), GrantWriter (reconciliation),
      the access conftest seed helpers.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.acl_grant import AclGrant
from app.access.models.principal_source_identity import PrincipalSourceIdentity
from app.access.services.grant_writer import GrantWriter
from app.connectors.imap.models.email import EmailMessage
from app.connectors.imap.services.email_ingest_service import EmailIngestService
from tests.access.conftest import (
    bind_verified_identity,
    seed_connection,
    seed_person,
    seed_user,
)

_RAW_EMAIL = (
    b"From: sender@globex.test\r\nTo: alice@acme.test\r\nCc: bob@acme.test\r\n"
    b"Message-ID: <grants@x>\r\nSubject: hello\r\n\r\nbody\r\n"
)


async def _grants(session: AsyncSession, org_id) -> list[AclGrant]:
    result = await session.execute(select(AclGrant).where(AclGrant.org_id == org_id))
    return list(result.scalars().all())


async def test_unmappable_principals_write_no_grant(db_session: AsyncSession) -> None:
    # AC8 (UNKNOWN ⇒ DENY): recipients exist, the resolver mints persons — but with no VERIFIED
    # identity mapping, not one grant row appears.
    org = uuid4()
    connection = await seed_connection(db_session, org)

    await EmailIngestService(db_session, connection).ingest_email(_RAW_EMAIL)

    assert await _grants(db_session, org) == []


async def test_unverified_identity_writes_no_grant(db_session: AsyncSession) -> None:
    # AC8: an UNVERIFIED mapping is exactly as powerless as no mapping.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    person = await seed_person(db_session, org, "Alice")
    await bind_verified_identity(
        db_session, org, person.id, "email", "alice@acme.test", verified=False
    )

    await EmailIngestService(db_session, connection).ingest_email(_RAW_EMAIL)

    assert await _grants(db_session, org) == []


async def test_verified_recipients_and_owner_get_per_message_grants(
    db_session: AsyncSession,
) -> None:
    # AC10: verified To + Cc principals and the connection owner each hold ONE live grant on the
    # message; a verified org member who was NOT a participant holds none.
    org = uuid4()
    owner_user = await seed_user(db_session, org)
    connection = await seed_connection(db_session, org, owner_user_id=owner_user.id)
    alice = await seed_person(db_session, org, "Alice")
    owner_person = await seed_person(db_session, org, "Owner")
    bystander = await seed_person(db_session, org, "Bystander")
    await bind_verified_identity(db_session, org, alice.id, "email", "alice@acme.test")
    await bind_verified_identity(db_session, org, owner_person.id, "auth", str(owner_user.id))
    await bind_verified_identity(db_session, org, bystander.id, "email", "bystander@acme.test")

    await EmailIngestService(db_session, connection).ingest_email(_RAW_EMAIL)

    grants = {grant.person_id: grant for grant in await _grants(db_session, org)}
    assert set(grants) == {alice.id, owner_person.id}  # bystander: NO grant (AC10 negative)
    assert grants[alice.id].provenance == "recipient"
    assert grants[owner_person.id].provenance == "owner"
    assert all(grant.revoked_at is None for grant in grants.values())


async def test_rewriting_grants_is_idempotent(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    alice = await seed_person(db_session, org, "Alice")
    await bind_verified_identity(db_session, org, alice.id, "email", "alice@acme.test")
    service = EmailIngestService(db_session, connection)
    await service.ingest_email(_RAW_EMAIL)
    message_id = (await _grants(db_session, org))[0].object_id

    writer = GrantWriter(db_session)
    await writer.write_email_grants(
        org,
        message_id,
        connection.id,
        None,
        "sender@globex.test",
        ["alice@acme.test", "bob@acme.test"],
    )

    assert len(await _grants(db_session, org)) == 1  # still exactly one live grant


async def test_reconciliation_tombstones_principal_whose_verification_was_revoked(
    db_session: AsyncSession,
) -> None:
    # AC21 (SHRINK ⇒ TOMBSTONE): the message survives, nothing is deleted at the source — but a
    # principal whose verified identity was withdrawn stops being derivable, and reconciliation
    # revokes the grant.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    alice = await seed_person(db_session, org, "Alice")
    await bind_verified_identity(db_session, org, alice.id, "email", "alice@acme.test")
    await EmailIngestService(db_session, connection).ingest_email(_RAW_EMAIL)
    grant = (await _grants(db_session, org))[0]
    assert grant.revoked_at is None

    await db_session.execute(
        update(PrincipalSourceIdentity)
        .where(PrincipalSourceIdentity.org_id == org)
        .values(verified=False)
    )
    tombstoned = await GrantWriter(db_session).reconcile_email_message_grants(
        org,
        grant.object_id,
        connection.id,
        None,
        "sender@globex.test",
        ["alice@acme.test", "bob@acme.test"],
    )

    assert tombstoned == 1
    await db_session.refresh(grant)
    assert grant.revoked_at is not None


async def test_reingest_skip_path_reconciles_grants(db_session: AsyncSession) -> None:
    # THE production reconciliation caller (2026-07-04 review P1): re-ingesting an already-stored
    # email (dedup SKIP) must re-derive grants — a binding verified AFTER the first ingest starts
    # matching on the next sync pass, which is also the pre-0019 corpus's grant backfill path.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    await service.ingest_email(_RAW_EMAIL)
    assert await _grants(db_session, org) == []  # nobody verified yet → UNKNOWN ⇒ DENY

    alice = await seed_person(db_session, org, "Alice")
    await bind_verified_identity(db_session, org, alice.id, "email", "alice@acme.test")
    outcome = await service.ingest_email(_RAW_EMAIL)  # the same email, re-seen

    assert outcome.value == "skipped"
    grants = await _grants(db_session, org)
    assert [grant.person_id for grant in grants] == [alice.id]
    assert grants[0].revoked_at is None


async def test_reconciliation_adds_newly_verified_principal(db_session: AsyncSession) -> None:
    # The other half of AC21: a verified binding created AFTER ingest starts matching on the
    # next reconciliation pass — grant writing is reconciliation, not a one-shot append.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    await service.ingest_email(_RAW_EMAIL)
    assert await _grants(db_session, org) == []
    alice = await seed_person(db_session, org, "Alice")
    await bind_verified_identity(db_session, org, alice.id, "email", "alice@acme.test")

    message_id = (
        await db_session.execute(select(EmailMessage.id).where(EmailMessage.org_id == org))
    ).scalar_one()
    await GrantWriter(db_session).reconcile_email_message_grants(
        org,
        message_id,
        connection.id,
        None,
        "sender@globex.test",
        ["alice@acme.test", "bob@acme.test"],
    )

    grants = await _grants(db_session, org)
    assert [grant.person_id for grant in grants] == [alice.id]
    assert grants[0].revoked_at is None
