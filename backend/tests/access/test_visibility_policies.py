"""
Role: LIVE-enforcement tests for the PF-01 visibility policies + triggers (migration 0019) —
      the within-tenant teeth ON THE READER PLANE: a granted person sees a restricted email, an
      ungranted org member sees ZERO rows, a person-less reader session sees org-scope rows only
      (AC2/AC3), revocation hides immediately (AC14), children bind to the parent's grants
      (AC22), the AC5 lineage guard rejects un-promoted widening on BOTH statement paths +
      origin_scope mutation, and the reader role cannot WRITE at all (SELECT-only plane).
Used by: pytest (tests/access). Requires a migrated + role-provisioned DB (policies/triggers are
      migration-only); the access_schema fixture skips loudly otherwise.
Depends on: app.core.database.reader_session (the REAL retrieval seam — exactly what the Ask
      layer will use), the access conftest seed helpers.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.repositories.acl_grant_repository import AclGrantRepository
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.core.database import reader_session
from tests.access.conftest import (
    grant_email_access,
    seed_connection,
    seed_email_message,
    seed_person,
    seed_user,
)


async def _visible_message_count(org_id, person_id) -> int:
    """Count email_message rows visible through the REAL person-scoped reader seam."""
    async with reader_session(org_id, person_id) as session:
        result = await session.execute(select(func.count()).select_from(EmailMessage))
        return result.scalar_one()


async def _visible_child_counts(org_id, person_id) -> tuple[int, int]:
    """(recipient, attachment) row counts visible through the person-scoped reader seam."""
    async with reader_session(org_id, person_id) as session:
        recipients = (
            await session.execute(select(func.count()).select_from(EmailRecipient))
        ).scalar_one()
        attachments = (
            await session.execute(select(func.count()).select_from(EmailAttachment))
        ).scalar_one()
        return recipients, attachments


async def test_granted_person_sees_restricted_email_ungranted_sees_zero(
    db_session: AsyncSession,
) -> None:
    # AC2: the per-table negative — a principal with no grant sees ZERO restricted rows.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    granted = await seed_person(db_session, org, "Granted")
    ungranted = await seed_person(db_session, org, "Ungranted")
    message = await seed_email_message(db_session, org, connection.id)
    await grant_email_access(db_session, org, granted.id, message.id, connection.id)
    await db_session.commit()

    assert await _visible_message_count(org, granted.id) == 1
    assert await _visible_message_count(org, ungranted.id) == 0


async def test_person_guc_unset_yields_org_scope_rows_only(db_session: AsyncSession) -> None:
    # AC3: a person-less session (ingest/system, or a buggy retrieval path that forgot the GUC)
    # fails CLOSED — restricted rows invisible, only org-scope (promoted) rows served.
    org = uuid4()
    approver = await seed_user(db_session, org)
    connection = await seed_connection(db_session, org, owner_user_id=approver.id)
    person = await seed_person(db_session, org)
    restricted = await seed_email_message(db_session, org, connection.id)
    await grant_email_access(db_session, org, person.id, restricted.id, connection.id)
    await seed_email_message(
        db_session,
        org,
        connection.id,
        visibility_scope="org",
        subject="promoted org-visible row",
        promotion_approver_user_id=approver.id,
    )
    await db_session.commit()

    assert await _visible_message_count(org, None) == 1  # ONLY the org-visible row
    assert await _visible_message_count(org, person.id) == 2  # grant + org row


async def test_children_bind_to_the_parents_grants(db_session: AsyncSession) -> None:
    # AC22 (exercised on today's children): recipient + attachment rows are visible to exactly
    # the principals who can see their parent message — never more, never fewer.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    granted = await seed_person(db_session, org, "Granted")
    ungranted = await seed_person(db_session, org, "Ungranted")
    message = await seed_email_message(db_session, org, connection.id)
    await grant_email_access(db_session, org, granted.id, message.id, connection.id)
    await db_session.commit()

    assert await _visible_child_counts(org, granted.id) == (1, 1)
    assert await _visible_child_counts(org, ungranted.id) == (0, 0)


async def test_revoked_grant_hides_rows_immediately(db_session: AsyncSession) -> None:
    # AC14 (tombstone half): revocation sets revoked_at and the very next query stops matching.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    person = await seed_person(db_session, org)
    message = await seed_email_message(db_session, org, connection.id)
    await grant_email_access(db_session, org, person.id, message.id, connection.id)
    await db_session.commit()
    assert await _visible_message_count(org, person.id) == 1

    await AclGrantRepository(db_session).tombstone_grants(
        org, "email_message", message.id, {person.id}
    )
    await db_session.commit()

    assert await _visible_message_count(org, person.id) == 0


async def test_cross_tenant_person_sees_nothing(db_session: AsyncSession) -> None:
    # The NON-NEGOTIABLE: org B's principal (even with a forged same-shape grant in org B)
    # sees zero of org A's rows — org_isolation AND visibility are both in force.
    org_a, org_b = uuid4(), uuid4()
    connection_a = await seed_connection(db_session, org_a, mailbox="a@acme.test")
    await seed_connection(db_session, org_b, mailbox="b@beta.test")
    person_b = await seed_person(db_session, org_b, "Org B Person")
    message_a = await seed_email_message(db_session, org_a, connection_a.id)
    # A grant row in ORG B pointing at org A's message id (no cross-org FK on object_id):
    # must still expose nothing — the message row itself is outside org B's org_isolation.
    await grant_email_access(db_session, org_b, person_b.id, message_a.id)
    await db_session.commit()

    assert await _visible_message_count(org_b, person_b.id) == 0


async def test_update_widening_without_promotion_row_is_rejected(
    db_session: AsyncSession,
) -> None:
    # AC5 (UPDATE path): flipping a restricted-origin row to org without lineage fails at the DB.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    message = await seed_email_message(db_session, org, connection.id)
    await db_session.commit()

    with pytest.raises(DBAPIError, match="promotion lineage"):
        await db_session.execute(
            update(EmailMessage).where(EmailMessage.id == message.id).values(visibility_scope="org")
        )
    await db_session.rollback()


async def test_insert_org_row_of_restricted_origin_without_lineage_is_rejected(
    db_session: AsyncSession,
) -> None:
    # AC5 (INSERT path): a restricted-origin row born directly as org-visible is rejected —
    # an UPDATE-only guard would miss this route (the epic's review correction). Raw table
    # insert on purpose: the seed helper refuses to build this row, which is the point.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    await db_session.commit()

    with pytest.raises(DBAPIError, match="promotion lineage"):
        await db_session.execute(
            EmailMessage.__table__.insert().values(
                org_id=org,
                connection_id=connection.id,
                visibility_scope="org",
                origin_scope="restricted",
                container_id=connection.id,
                dedup_key="lineage-less-org-insert",
                headers={},
            )
        )
    await db_session.rollback()


async def test_origin_scope_is_immutable(db_session: AsyncSession) -> None:
    # AC5 (discriminator integrity): origin_scope is set once at ingest; any change is rejected,
    # so the lineage guard's restricted-origin discrimination cannot be laundered away.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    message = await seed_email_message(db_session, org, connection.id)
    await db_session.commit()

    with pytest.raises(DBAPIError, match="origin_scope is immutable"):
        await db_session.execute(
            update(EmailMessage).where(EmailMessage.id == message.id).values(origin_scope="org")
        )
    await db_session.rollback()


async def test_reader_role_cannot_write_content(db_session: AsyncSession) -> None:
    # The reader plane is SELECT-only BY ROLE: even a fully-granted person's session cannot
    # INSERT/UPDATE content — a compromised or hallucinating retrieval tool physically cannot
    # mutate tenant data (stronger than the epic's original single-role design).
    org = uuid4()
    connection = await seed_connection(db_session, org)
    person = await seed_person(db_session, org)
    message = await seed_email_message(db_session, org, connection.id)
    await grant_email_access(db_session, org, person.id, message.id, connection.id)
    await db_session.commit()

    async with reader_session(org, person.id) as session:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await session.execute(
                update(EmailMessage)
                .where(EmailMessage.id == message.id)
                .values(subject="defaced by the retrieval plane")
            )
        await session.rollback()
