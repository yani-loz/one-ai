"""
Role: Promotion-path tests (PF-01 AC5/AC13) — the owner's one-click widening is atomic
      (audit row + lineage row + scope flip in one transaction), non-owners and admin-style
      callers cannot widen, shared (owner-less) mailboxes have no promoter, and the
      visibility_promotion history is append-only.
Used by: pytest (tests/access). Real DB via the access conftest.
Depends on: PromotionService + the DB lineage-guard/append-only triggers (migration 0019).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.exceptions import ContentObjectNotFoundError, PromotionNotAllowedError
from app.access.models.visibility_promotion import VisibilityPromotion
from app.access.services.promotion_service import PromotionService
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.identity.models.audit_log import AuditLog
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditService
from tests.access.conftest import seed_connection, seed_email_message, seed_user


def _service(session: AsyncSession) -> PromotionService:
    return PromotionService(session, AuditService(AuditRepository(session)))


async def test_owner_one_click_promotion_is_atomic_and_audited(
    db_session: AsyncSession,
) -> None:
    # AC13: one call → audit row + lineage row + scope flip on the message AND children,
    # all in the same transaction.
    org = uuid4()
    owner = await seed_user(db_session, org)
    connection = await seed_connection(db_session, org, owner_user_id=owner.id)
    message = await seed_email_message(db_session, org, connection.id)

    promotion_id = await _service(db_session).promote_email_message(
        org, owner.id, owner.email, message.id
    )
    await db_session.commit()

    promotion = (
        await db_session.execute(
            select(VisibilityPromotion).where(VisibilityPromotion.id == promotion_id)
        )
    ).scalar_one()
    assert promotion.approved_by_user_id == owner.id
    assert promotion.object_id == message.id
    audit_row = (
        await db_session.execute(select(AuditLog).where(AuditLog.id == promotion.audit_log_id))
    ).scalar_one()
    assert audit_row.action == "access.visibility_promoted"
    scopes = set()
    for model in (EmailMessage, EmailRecipient, EmailAttachment):
        column = model.id if model is EmailMessage else model.email_id
        rows = await db_session.execute(select(model.visibility_scope).where(column == message.id))
        scopes.update(rows.scalars().all())
    assert scopes == {"org"}  # message + both children flipped together (AC22 inheritance)


async def test_non_owner_cannot_promote(db_session: AsyncSession) -> None:
    org = uuid4()
    owner = await seed_user(db_session, org, email="owner@acme.test")
    other = await seed_user(db_session, org, email="other@acme.test")
    connection = await seed_connection(db_session, org, owner_user_id=owner.id)
    message = await seed_email_message(db_session, org, connection.id)

    with pytest.raises(PromotionNotAllowedError, match="owner"):
        await _service(db_session).promote_email_message(org, other.id, other.email, message.id)


async def test_shared_mailbox_has_no_promoter(db_session: AsyncSession) -> None:
    # A shared (owner-less) connection: NOBODY may widen — fail-closed, not admin-open
    # ('admin_widen' does not exist by design).
    org = uuid4()
    admin = await seed_user(db_session, org, email="admin@acme.test")
    connection = await seed_connection(db_session, org, owner_user_id=None)
    message = await seed_email_message(db_session, org, connection.id)

    with pytest.raises(PromotionNotAllowedError):
        await _service(db_session).promote_email_message(org, admin.id, admin.email, message.id)


async def test_cross_tenant_promotion_probe_returns_not_found(
    db_session: AsyncSession,
) -> None:
    # The NON-NEGOTIABLE cross-tenant negative: org B's user probing org A's message id gets
    # NOT FOUND — no existence leak, no cross-org widening.
    org_a, org_b = uuid4(), uuid4()
    owner_a = await seed_user(db_session, org_a, email="a@acme.test")
    connection_a = await seed_connection(db_session, org_a, owner_user_id=owner_a.id)
    message_a = await seed_email_message(db_session, org_a, connection_a.id)
    intruder = await seed_user(db_session, org_b, email="b@beta.test")
    await seed_connection(db_session, org_b, mailbox="b@beta.test")

    with pytest.raises(ContentObjectNotFoundError):
        await _service(db_session).promote_email_message(
            org_b, intruder.id, intruder.email, message_a.id
        )


async def test_already_org_visible_message_cannot_be_repromoted(
    db_session: AsyncSession,
) -> None:
    org = uuid4()
    owner = await seed_user(db_session, org)
    connection = await seed_connection(db_session, org, owner_user_id=owner.id)
    message = await seed_email_message(
        db_session,
        org,
        connection.id,
        visibility_scope="org",
        promotion_approver_user_id=owner.id,
    )

    with pytest.raises(PromotionNotAllowedError, match="already"):
        await _service(db_session).promote_email_message(org, owner.id, owner.email, message.id)


async def test_email_content_cannot_be_born_org_visible(db_session: AsyncSession) -> None:
    # 2026-07-04 review M6: the lineage guard legitimately passes born-org rows, so email pins
    # origin_scope='restricted' at the DB — a writer cannot launder a widening by lying about
    # origin on these tables.
    org = uuid4()
    connection = await seed_connection(db_session, org)

    with pytest.raises(DBAPIError, match="ck_email_message_origin_scope"):
        await db_session.execute(
            EmailMessage.__table__.insert().values(
                org_id=org,
                connection_id=connection.id,
                visibility_scope="org",
                origin_scope="org",
                container_id=connection.id,
                dedup_key="born-org-attempt",
                headers={},
            )
        )
    await db_session.rollback()


async def test_promotion_history_is_append_only(db_session: AsyncSession) -> None:
    # AC5/S3: the widening record cannot be rewritten — UPDATE is rejected at the DB.
    org = uuid4()
    owner = await seed_user(db_session, org)
    connection = await seed_connection(db_session, org, owner_user_id=owner.id)
    message = await seed_email_message(db_session, org, connection.id)
    promotion_id = await _service(db_session).promote_email_message(
        org, owner.id, owner.email, message.id
    )
    await db_session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            update(VisibilityPromotion)
            .where(VisibilityPromotion.id == promotion_id)
            .values(approved_by_user_id=owner.id)
        )
    await db_session.rollback()
