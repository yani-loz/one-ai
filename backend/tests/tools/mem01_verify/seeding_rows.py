"""
Role: Row builders, marker constants and the SeededOrg / SeededCorpus descriptors shared by the
      oracle's corpus seeders (seeding.py, seeding_big.py). Every ORM row the probe corpus
      contains is minted here so the three seeders agree on scopes, markers and domains.
Used by: tests.tools.mem01_verify.seeding, .seeding_big, conftest.py and the DB-backed tests.
Depends on: app.* ORM models (imported INSIDE the builders, only to arrange rows); stdlib.
Key invariants:
  - Only reserved domains and synthetic names; every subject/body/address carries a marker that
    must never reach stdout (R5); `language` stays NULL; rows are born restricted.
  - Rows are flushed parents-first (`flush_parents_first`): the app defines no relationship(),
    so the unit of work would otherwise insert children before their FK targets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

SMALL_SUBJECT_MARKER = "OracleSmallSubject"
BIG_SUBJECT_MARKER = "OracleBigSubject"
BODY_MARKER = "OracleBodyText"
PERSONA_NAME_MARKER = "OraclePersona"
ATTACHMENT_FILENAME_MARKER = "oracle-attachment"
EXTERNAL_PARENT_ID = "external-parent@partner.test"
SHARED_HASH_SMALL = sha256(b"oracle shared attachment small").hexdigest()
INLINE_IMAGE_HASH = sha256(b"oracle inline image").hexdigest()
UNSUPPORTED_HASH = sha256(b"oracle unsupported").hexdigest()
ISO_ATTACHMENT_HASH = sha256(b"oracle iso attachment").hexdigest()


@dataclass(frozen=True)
class SeededOrg:
    """What was seeded for one org and what the oracle expects from it."""

    org_id: UUID
    connection_id: UUID
    email_ids: tuple[UUID, ...]
    attachment_ids: tuple[UUID, ...]
    person_ids: tuple[UUID, ...]
    email_count: int
    attachment_count: int
    attachments_with_text: int
    null_body_email_ids: tuple[UUID, ...]
    null_subject_email_ids: tuple[UUID, ...]
    lang_class_by_email: Mapping[UUID, str]
    expected_groups: tuple[frozenset[UUID], ...]
    personal_markers: tuple[str, ...]
    bcc_count: int
    grant_count: int
    not_ready_attachment_count: int
    # §5.1 artifact id (`email_body:<id>`, `email_subject:<id>`, `attachment_text:<id>`) →
    # the exact stored text, None where the column is NULL (the verbatim snapshot seal)
    texts_by_artifact: Mapping[str, str | None] = field(default_factory=dict)

    @property
    def text_artifact_count(self) -> int:
        """§5.1: one body + one subject per email, one attachment_text per non-null text."""
        return 2 * self.email_count + self.attachments_with_text


@dataclass(frozen=True)
class SeededCorpus:
    """The three orgs of the session probe database."""

    database: str
    big: SeededOrg
    small: SeededOrg
    iso: SeededOrg
    seeded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _base_instant(offset_hours: int) -> datetime:
    return datetime(2026, 9, 7, 10, 0, tzinfo=UTC) + timedelta(hours=offset_hours)


async def _register_org(session: object, org_id: UUID, label: str) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.identity.models.organization import Organization

    await session.execute(  # type: ignore[attr-defined]  # AsyncSession, typed loosely on purpose
        pg_insert(Organization.__table__)
        .values(id=org_id, name=f"Oracle {label} {org_id.hex[:8]}", slug=f"oracle-{org_id.hex}")
        .on_conflict_do_nothing(index_elements=["id"])
    )


def _connection(org_id: UUID, connection_id: UUID, mailbox: str) -> object:
    from app.connectors.models.connector_connection import ConnectorConnection

    return ConnectorConnection(
        id=connection_id,
        org_id=org_id,
        connector_type="imap",
        owner_user_id=None,
        display_name="Oracle Mailbox",
        auth_method="app_password",
        username=mailbox,
        config={"host": "mail.example.test", "port": 993, "use_ssl": True},
        secret_ciphertext=b"\x00" * 32,
        secret_key_version=1,
        status="configured",
    )


def _email(org_id: UUID, connection_id: UUID, email_id: UUID, **columns: object) -> object:
    from app.connectors.imap.models.email import EmailMessage

    base = {
        "id": email_id,
        "org_id": org_id,
        "connection_id": connection_id,
        "visibility_scope": "restricted",
        "origin_scope": "restricted",
        "container_id": connection_id,
        "dedup_key": f"oracle-{email_id.hex}",
        "headers": {},
        "parse_status": "parsed",
        "language": None,
    }
    base.update(columns)
    return EmailMessage(**base)


def _recipient(
    org_id: UUID,
    connection_id: UUID,
    email_id: UUID,
    kind: str,
    address: str,
    person_id: UUID | None = None,
    name: str | None = None,
) -> object:
    from app.connectors.imap.models.email import EmailRecipient

    return EmailRecipient(
        org_id=org_id,
        email_id=email_id,
        visibility_scope="restricted",
        origin_scope="restricted",
        container_id=connection_id,
        kind=kind,
        address=address,
        name=name,
        person_id=person_id,
    )


def _attachment(
    org_id: UUID, connection_id: UUID, email_id: UUID, attachment_id: UUID, **columns: object
) -> object:
    from app.connectors.imap.models.email import EmailAttachment

    base = {
        "id": attachment_id,
        "org_id": org_id,
        "email_id": email_id,
        "visibility_scope": "restricted",
        "origin_scope": "restricted",
        "container_id": connection_id,
        "size_bytes": 1024,
        "is_inline": False,
    }
    base.update(columns)
    return EmailAttachment(**base)


def _grant(
    org_id: UUID,
    person_id: UUID,
    email_id: UUID,
    connection_id: UUID,
    revoked_at: datetime | None = None,
) -> object:
    from app.access.models.acl_grant import AclGrant

    return AclGrant(
        org_id=org_id,
        person_id=person_id,
        object_type="email_message",
        object_id=email_id,
        connection_id=connection_id,
        provenance="recipient",
        revoked_at=revoked_at,
    )


def _person(org_id: UUID, person_id: UUID, name: str) -> object:
    from app.entities.models.person import Person

    return Person(id=person_id, org_id=org_id, display_name=name, is_internal=True)


def _person_email(org_id: UUID, person_id: UUID, email: str) -> object:
    from app.entities.models.person import PersonEmail

    return PersonEmail(org_id=org_id, person_id=person_id, email=email, source="imap")


async def flush_parents_first(session: object, rows: list[object]) -> None:
    """Insert `rows` in FK order: parents first, one flush per stage.

    The app defines no `relationship()`, so a single add_all + flush lets SQLAlchemy's unit of
    work insert mappers in class-name order (AclGrant before Person, EmailRecipient before
    EmailMessage) and violate the foreign keys. Stages: connection + person → person_email →
    email_message → recipient / attachment / grant → anything else.
    """
    from app.access.models.acl_grant import AclGrant
    from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
    from app.connectors.models.connector_connection import ConnectorConnection
    from app.entities.models.person import Person, PersonEmail

    stages: tuple[tuple[type, ...], ...] = (
        (ConnectorConnection, Person),
        (PersonEmail,),
        (EmailMessage,),
        (EmailRecipient, EmailAttachment, AclGrant),
    )
    remaining = list(rows)
    for stage in stages:
        chosen = [row for row in remaining if isinstance(row, stage)]
        remaining = [row for row in remaining if not isinstance(row, stage)]
        if chosen:
            session.add_all(chosen)  # type: ignore[attr-defined]
            await session.flush()  # type: ignore[attr-defined]
    if remaining:
        session.add_all(remaining)  # type: ignore[attr-defined]
        await session.flush()  # type: ignore[attr-defined]


class ProbeGuardError(AssertionError):
    """Raised when a suite connection is not bound to a `mem01_probe_` database (§16.11)."""


async def assert_probe_connection(session: object) -> str:
    """§16.11 guard: verify `current_database()` on THIS connection starts with `mem01_probe_`.

    Called before the first write of every seeding step and before every content-table read the
    suite performs, independently of the instrument. Returns the database name.
    """
    from sqlalchemy import text

    name = (await session.execute(text("SELECT current_database()"))).scalar_one()  # type: ignore[attr-defined]
    if not isinstance(name, str) or not name.startswith("mem01_probe_"):
        raise ProbeGuardError(f"suite connection is bound to {name!r}, not a probe database")
    return name
