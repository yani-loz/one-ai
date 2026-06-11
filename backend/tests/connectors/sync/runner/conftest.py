"""
Role: DB fixtures + fakes for the ConnectorSyncRunner INTEGRATION tests. The runner opens its OWN
      tenant scoped_session (RLS-enforced) and ingests real mail, so a test needs the FULL schema
      one sync touches (connector_connection + the two sync tables + the shared entity graph + the
      email Layer-1 tables) on the migrated DB, a real CredentialCipher (the runner decrypts), and a
      fake SupportsIncrementalFetch connector wired through the registry seam.
Used by: tests/connectors/sync/runner/test_connector_sync_runner.py.
Depends on: app.core.database, the connector/entity/email/sync models, the incremental-fetch
            contract, the credential cipher + registry. Requires a provisioned (migrated) DB — SKIPs
            otherwise, like the other DB conftests.
Key invariants:
  - Function-scoped; creates any missing tables and TRUNCATE ... CASCADE on teardown.
  - seed_connection stores a REAL AES-GCM ciphertext (under `sync_cipher`) so the runner's
    _build_connector decrypts successfully and reaches the registry's fake factory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.connectors.base.incremental_fetch import FetchBatch, FetchedMessage, FolderCursor
from app.connectors.base.registry import ConnectorRegistry
from app.connectors.enums import ConnectorType
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.connectors.security.credential_cipher import CredentialCipher
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from tests.conftest import register_org

# Parents before children (create order); TRUNCATE ... CASCADE handles FK order on teardown.
_RUNNER_TABLES = [
    ConnectorConnection.__table__,
    Person.__table__,
    Company.__table__,
    PersonEmail.__table__,
    PersonAlias.__table__,
    CompanyDomain.__table__,
    PersonCompany.__table__,
    EmailMessage.__table__,
    EmailRecipient.__table__,
    EmailAttachment.__table__,
    ConnectorSyncCursor.__table__,
    ConnectorSyncRun.__table__,
]


def _missing_tables(sync_connection: object) -> list[object]:
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _RUNNER_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def runner_schema() -> AsyncIterator[None]:
    """Give each test the full sync schema, then reset it (truncate or drop)."""
    if not await runtime_roles_present():
        pytest.skip(
            "Runtime DB roles missing — run `alembic upgrade head` then "
            "`python -m scripts.provision_roles` before the DB suite."
        )
    async with engine.begin() as connection:
        created = await connection.run_sync(_missing_tables)
        await connection.run_sync(Base.metadata.create_all, tables=created)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            pre_existing = [t for t in _RUNNER_TABLES if t not in created]
            if pre_existing:
                names = ", ".join(t.name for t in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(runner_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success GLOBAL session for seeding + asserts (runner self-opens)."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.fixture
def sync_cipher() -> CredentialCipher:
    """A cipher the test seeds credentials with AND hands the runner (so decrypt round-trips)."""
    return CredentialCipher("runner-test-key-not-secure-but-long-enough", require_secure=False)


class FakeIncrementalConnector:
    """A SupportsIncrementalFetch stand-in: yields canned batches + records the cursors it saw."""

    def __init__(self, batches: list[FetchBatch], *, pending: int | None = None) -> None:
        """Hold the batches to stream + an optional pending count (else the message sum)."""
        self._batches = batches
        self._pending = pending
        self.count_cursors: list[dict[str, FolderCursor]] = []
        self.fetch_cursors: list[dict[str, FolderCursor]] = []

    async def count_pending(self, cursors: Mapping[str, FolderCursor]) -> int:
        """Record the resume cursors and report how many messages will be streamed."""
        self.count_cursors.append(dict(cursors))
        if self._pending is not None:
            return self._pending
        return sum(len(batch.messages) for batch in self._batches)

    async def fetch_incremental(
        self, cursors: Mapping[str, FolderCursor]
    ) -> AsyncIterator[FetchBatch]:
        """Record the resume cursors and stream the canned batches in order."""
        self.fetch_cursors.append(dict(cursors))
        for batch in self._batches:
            yield batch


def make_registry(connector: FakeIncrementalConnector) -> ConnectorRegistry:
    """A registry whose 'imap' factory returns the SAME fake connector (the test inspects it)."""
    registry = ConnectorRegistry()
    registry.register(ConnectorType.imap, lambda config, secret: connector)
    return registry


def eml(message_id: str, *, frm: str = "boyan@globex.com", to: str = "owner@acme.com") -> bytes:
    """Build a minimal RFC822 message with a distinct Message-ID (so dedup_key differs per uid)."""
    raw = f"From: {frm}\r\nTo: {to}\r\nSubject: S\r\nMessage-ID: <{message_id}>\r\n\r\nHello."
    return raw.encode("utf-8")


def message(uid: int, message_id: str) -> FetchedMessage:
    """A FetchedMessage carrying a unique parseable email for `uid`."""
    return FetchedMessage(uid=uid, raw_bytes=eml(message_id), internal_date=None)


def folder_batch(
    folder: str,
    uidvalidity: int,
    *messages: FetchedMessage,
    requested_uids: list[int] | None = None,
) -> FetchBatch:
    """A FetchBatch for one folder. requested_uids defaults to the messages' UIDs; pass it
    explicitly (a superset of the returned UIDs) to model a partial/dropped FETCH."""
    returned = list(messages)
    return FetchBatch(
        folder=folder,
        uidvalidity=uidvalidity,
        messages=returned,
        requested_uids=requested_uids if requested_uids is not None else [m.uid for m in returned],
    )


async def seed_connection(
    session: AsyncSession,
    org_id: UUID,
    cipher: CredentialCipher,
    *,
    mailbox: str = "owner@acme.com",
    disabled: bool = False,
) -> ConnectorConnection:
    """Insert a connector_connection with a REAL encrypted credential (runner can decrypt).

    Registers the org row first (idempotent) — 0014's org-root FK rejects phantom tenants.
    """
    await register_org(session, org_id)
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name="Mailbox",
        auth_method="app_password",
        username=mailbox,
        config={"host": "mail.example.com", "port": 993, "use_ssl": True},
        secret_ciphertext=cipher.encrypt("app-password"),
        secret_key_version=cipher.key_version,
        status="configured",
        disabled_at=datetime.now(UTC) if disabled else None,
    )
    session.add(connection)
    await session.flush()
    return connection
