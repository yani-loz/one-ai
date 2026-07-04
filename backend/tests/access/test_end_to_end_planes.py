"""
Role: THE cross-plane integration proof (2026-07-04 review M8 — write-path tests must not hide
      behind the BYPASSRLS session): one email travels the REAL production planes end-to-end —
      ingested person-less on the WRITE plane (oneai_app, RETURNING included), grants derived
      from verified identities, then served person-scoped on the READER plane (oneai_reader,
      visibility policies live) — and the reader's decision telemetry lands in audit_log
      through its own INSERT ... RETURNING (the P1 grant regression this file pins).
Used by: pytest (tests/access). Requires a migrated + role-provisioned DB.
Depends on: scoped_session + reader_session (the real seams), EmailIngestService,
      DecisionTelemetry, the access conftest seed helpers.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.acl_grant import AclGrant
from app.access.services.decision_telemetry import DecisionTelemetry
from app.connectors.imap.models.email import EmailMessage
from app.connectors.imap.services.email_ingest_service import EmailIngestService, IngestOutcome
from app.core.database import reader_session, scoped_session
from app.identity.models.audit_log import AuditLog
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditService
from tests.access.conftest import bind_verified_identity, seed_connection, seed_person, seed_user

_RAW_EMAIL = (
    b"From: sender@globex.test\r\nTo: alice@acme.test\r\n"
    b"Message-ID: <planes@x>\r\nSubject: cross-plane proof\r\n\r\nbody\r\n"
)


async def test_ingest_on_write_plane_then_serve_on_reader_plane(
    db_session: AsyncSession,
) -> None:
    org = uuid4()
    owner = await seed_user(db_session, org)
    connection = await seed_connection(db_session, org, owner_user_id=owner.id)
    alice = await seed_person(db_session, org, "Alice")
    await bind_verified_identity(db_session, org, alice.id, "email", "alice@acme.test")
    stranger = await seed_person(db_session, org, "Stranger")
    await db_session.commit()

    # WRITE PLANE: person-less ingest as the real oneai_app role — the INSERT ... RETURNING
    # path that the pre-reader-split design broke (review finding #1) must store the email.
    async with scoped_session(org) as write_session:
        outcome = await EmailIngestService(write_session, connection).ingest_email(_RAW_EMAIL)
        await write_session.commit()
    assert outcome is IngestOutcome.STORED

    grant = (await db_session.execute(select(AclGrant).where(AclGrant.org_id == org))).scalar_one()
    assert grant.person_id == alice.id  # derived from the verified binding at ingest

    # READER PLANE: the granted person sees the restricted email; an ungranted person sees zero.
    async with reader_session(org, alice.id) as alice_view:
        visible = (
            await alice_view.execute(select(func.count()).select_from(EmailMessage))
        ).scalar_one()
        assert visible == 1
        # ...and the reader's decision telemetry writes its audit row through the reader
        # role's own INSERT ... RETURNING (review P1: INSERT alone was not enough).
        telemetry = DecisionTelemetry(AuditService(AuditRepository(alice_view)))
        reduced = await telemetry.record_retrieval_decision(
            org, owner.id, alice.id, candidate_count=1, allowed_count=1, denied_object_ids=[]
        )
        await alice_view.commit()
    assert reduced is False

    async with reader_session(org, stranger.id) as stranger_view:
        assert (
            await stranger_view.execute(select(func.count()).select_from(EmailMessage))
        ).scalar_one() == 0

    audit_row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.org_id == org, AuditLog.action == "access.retrieval_decision"
            )
        )
    ).scalar_one()
    assert audit_row.actor_id == owner.id  # users-table id, never the person id (review M4)
    assert audit_row.details["person_id"] == str(alice.id)
