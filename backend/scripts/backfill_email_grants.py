"""
Role: DEV driver — makes the disk-ingested corpus retrievable under PF-01 by (1) seeding the
      mailbox owner's login user + VERIFIED identity bindings (auth + email namespaces) and
      (2) reconciling per-message ACL grants over every stored email via the production
      GrantWriter (the same choke point ingest uses — reconcile-on-skip without re-parsing
      13k .eml files). Run once after a disk ingest; idempotent.
Used by: a developer, host-side against the dev DB:
  POSTGRES_HOST=localhost POSTGRES_PORT=55432 uv run python -m scripts.backfill_email_grants \
      [--org <uuid>] [--mailbox <address>]
Depends on: app.core (config/database), app.identity (User + repo), app.access (GrantWriter,
      PrincipalSourceIdentity), app.entities.services.email_normalizer, app.connectors models.
Key invariants:
  - DEV ONLY: refuses in any environment requiring secure secrets (same predicate as ingest).
  - Grants derive ONLY through verified principal_source_identity rows (AC8) — this script
    creates exactly two bindings (the owner's login + their own address), so participants other
    than the owner still resolve to NOTHING until their identities are verified. That is the
    intended fail-closed posture, not a bug.
  - All tenant writes on scoped_session(org) (the RLS write plane); verification queries on
    reader_session (the retrieval plane) so the check proves what the agent will actually see.
  - Idempotent: user matched by email; bindings upserted by (org, source_type, external_id);
    reconcile re-derives the same grants (ON CONFLICT no-op) and tombstones dropped principals.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.principal_source_identity import PrincipalSourceIdentity
from app.access.services.grant_writer import GrantWriter
from app.connectors.imap.parsing import DISCLOSED_RECIPIENT_KINDS
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.config import get_settings
from app.core.database import (
    engine,
    global_engine,
    reader_engine,
    reader_session,
    scoped_session,
    tenant_engine,
)
from app.entities.services.email_normalizer import normalize_email
from app.identity.enums import UserRole
from app.identity.models.user import User
from app.identity.security.password import hash_password

_DEV_INGEST_ORG = UUID("d1500000-0000-0000-0000-000000000001")
_OWNER_PASSWORD = "Owner-Dev-Only-2026!"  # dev backdoor, same class as scripts.seed_identity
_BATCH_COMMIT = 500


def _refuse_in_secure_env() -> None:
    """Abort anywhere that requires secure secrets — this seeds dev credentials + bindings."""
    settings = get_settings()
    if settings.requires_secure_secrets:
        raise SystemExit(
            f"Refusing grant backfill: app_env={settings.app_env!r} requires secure secrets."
        )


async def _resolve_connection(
    session: AsyncSession, org_id: UUID, mailbox: str | None
) -> ConnectorConnection:
    """Load the org's IMAP connection (by mailbox when given; must be exactly one match)."""
    query = select(ConnectorConnection).where(
        ConnectorConnection.org_id == org_id,
        ConnectorConnection.connector_type == "imap",
    )
    if mailbox:
        query = query.where(ConnectorConnection.username == mailbox)
    connections = (await session.execute(query)).scalars().all()
    if len(connections) != 1:
        raise SystemExit(
            f"Expected exactly one IMAP connection (got {len(connections)}) — pass --mailbox."
        )
    return connections[0]


async def _ensure_owner_user(session: AsyncSession, org_id: UUID, address: str) -> UUID:
    """GET-OR-CREATE the owner's login user in the target org (matched by email)."""
    existing = (
        await session.execute(select(User.id).where(User.email == address))
    ).scalar_one_or_none()
    if existing is not None:
        print(f"  [skipped] user {address} already exists")
        return existing
    user = User(
        org_id=org_id,
        email=address,
        full_name="Mailbox Owner (dev)",
        password_hash=hash_password(_OWNER_PASSWORD),
        role=UserRole.member.value,
    )
    session.add(user)
    await session.flush()
    print(f"  [created] user {address}")
    return user.id


async def _resolve_owner_person(session: AsyncSession, org_id: UUID, normalized: str) -> UUID:
    """The person the entity graph minted for the owner's own address (must exist post-ingest)."""
    person_id = (
        await session.execute(
            text(
                "SELECT person_id FROM person_email WHERE org_id = :org AND email = :addr "
                "ORDER BY created_at LIMIT 1"
            ),
            {"org": str(org_id), "addr": normalized},
        )
    ).scalar_one_or_none()
    if person_id is None:
        raise SystemExit(
            f"No person carries address {normalized!r} — has the disk ingest finished?"
        )
    return person_id


async def _upsert_verified_binding(
    session: AsyncSession,
    org_id: UUID,
    person_id: UUID,
    source_type: str,
    external_id: str,
    actor_user_id: UUID,
) -> None:
    """Idempotently bind + verify one external identity to the owner person."""
    existing = (
        await session.execute(
            select(PrincipalSourceIdentity).where(
                PrincipalSourceIdentity.org_id == org_id,
                PrincipalSourceIdentity.source_type == source_type,
                PrincipalSourceIdentity.external_id == external_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not existing.verified:
            existing.verified = True
            existing.verified_at = datetime.now(UTC)
            existing.verified_by_user_id = actor_user_id
            print(f"  [verified] existing {source_type} binding")
        else:
            print(f"  [skipped] {source_type} binding already verified")
        return
    session.add(
        PrincipalSourceIdentity(
            org_id=org_id,
            person_id=person_id,
            source_type=source_type,
            external_id=external_id,
            verified=True,
            verified_at=datetime.now(UTC),
            verified_by_user_id=actor_user_id,
        )
    )
    await session.flush()
    print(f"  [created] verified {source_type} binding -> person {person_id}")


async def _reconcile_all_messages(
    session: AsyncSession, org_id: UUID, connection_id: UUID, owner_user_id: UUID
) -> int:
    """Reconcile grants for every stored message of the connection (batched commits)."""
    recipient_rows = await session.execute(
        text(
            # DISCLOSED kinds only, and bound as a PARAMETER rather than written into the SQL:
            # this script and the ingest service are two writers on ONE reconciling choke point,
            # so if their derivation rules differ they OSCILLATE — backfill mints a bcc grant,
            # the next re-ingest hits the dedup fast path and tombstones it as `live -
            # derivable`, the next backfill re-mints it. That is the same bug class the ingest
            # fix removed, reappearing BETWEEN the two writers. Sharing the constant is what
            # stops the rule drifting apart again.
            "SELECT r.email_id, r.address FROM email_recipient r "
            "JOIN email_message m ON m.id = r.email_id AND m.org_id = r.org_id "
            "WHERE m.org_id = :org AND m.connection_id = :conn "
            "AND r.kind = ANY(:disclosed_kinds)"
        ),
        {
            "org": str(org_id),
            "conn": str(connection_id),
            "disclosed_kinds": sorted(DISCLOSED_RECIPIENT_KINDS),
        },
    )
    recipients_by_message: dict[UUID, list[str]] = defaultdict(list)
    for email_id, address in recipient_rows:
        recipients_by_message[email_id].append(address)

    messages = (
        await session.execute(
            text(
                "SELECT id, from_address FROM email_message "
                "WHERE org_id = :org AND connection_id = :conn ORDER BY created_at"
            ),
            {"org": str(org_id), "conn": str(connection_id)},
        )
    ).all()

    writer = GrantWriter(session)
    processed = 0
    for message_id, from_address in messages:
        await writer.reconcile_email_message_grants(
            org_id,
            message_id,
            connection_id,
            owner_user_id,
            from_address,
            recipients_by_message.get(message_id, []),
        )
        processed += 1
        if processed % _BATCH_COMMIT == 0:
            await session.commit()
            print(f"  reconciled {processed}/{len(messages)}", flush=True)
    await session.commit()
    return processed


async def _report_visibility(org_id: UUID, person_id: UUID) -> None:
    """Prove the retrieval plane sees the corpus: person-scoped vs unbound reader counts."""
    count_messages = text("SELECT count(*) FROM email_message")
    async with reader_session(org_id, person_id) as session:
        visible = (await session.execute(count_messages)).scalar_one()
    async with reader_session(org_id) as session:
        unbound = (await session.execute(count_messages)).scalar_one()
    print(f"reader visibility: person-scoped={visible}  unbound={unbound} (expect 0 unbound)")


async def _run(org_id: UUID, mailbox: str | None) -> None:
    """Drive the backfill: user + bindings + owner column + full grant reconciliation."""
    _refuse_in_secure_env()
    async with scoped_session(org_id) as session:
        connection = await _resolve_connection(session, org_id, mailbox)
        owner_address = normalize_email(connection.username) or connection.username.lower()
        print(f"connection {connection.id} mailbox={connection.username}")

        owner_user_id = await _ensure_owner_user(session, org_id, owner_address)
        person_id = await _resolve_owner_person(session, org_id, owner_address)
        await _upsert_verified_binding(
            session, org_id, person_id, "auth", str(owner_user_id), owner_user_id
        )
        await _upsert_verified_binding(
            session, org_id, person_id, "email", owner_address, owner_user_id
        )
        if connection.owner_user_id != owner_user_id:
            connection.owner_user_id = owner_user_id
            print("  [updated] connection.owner_user_id set")
        await session.commit()

        processed = await _reconcile_all_messages(session, org_id, connection.id, owner_user_id)
        live_grants = (
            await session.execute(
                text("SELECT count(*) FROM acl_grant WHERE org_id = :org AND revoked_at IS NULL"),
                {"org": str(org_id)},
            )
        ).scalar_one()
    print("=" * 60)
    print(f"DONE — messages reconciled={processed}  live grants={live_grants}")
    await _report_visibility(org_id, person_id)
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: parse args, run, dispose engines cleanly."""
    parser = argparse.ArgumentParser(description="PF-01 grant backfill over a disk-ingested org.")
    parser.add_argument("--org", default=str(_DEV_INGEST_ORG), help="target org_id (uuid)")
    parser.add_argument("--mailbox", default=None, help="IMAP username if several connections")

    args = parser.parse_args(argv)

    async def _main() -> None:
        try:
            await _run(UUID(args.org), args.mailbox)
        finally:
            for eng in (engine, tenant_engine, global_engine, reader_engine):
                await eng.dispose()

    asyncio.run(_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
