"""SUITE C (ingest + runner legs) — dedup poisoning, regression holds, and the C01 runner-fail chain.

This script touches the DB. It runs ingest on `GlobalSessionLocal` (the BYPASSRLS global engine —
exactly how EmailIngestService runs today per E08), and drives a real ConnectorSyncRunner for the
C01 runner-fail leg. Every row is created under a RUN-STAMPED throwaway org (uuid4) and deleted in
the finally block ACROSS the full surface — the entity graph (person/company/...) does NOT cascade,
so each table is purged explicitly by org_id.

Cases:
  C02  Dedup poisoning — ingest a real email, then a DIFFERENT email reusing the same Message-ID:
       exists() short-circuits -> the genuine second email is SILENTLY SKIPPED (one row survives).
  C04  (ingest leg) C0 control chars (\x01/\x07/\x1b) survive into the STORED subject + body_text.
  C09  Regression (7c90f55) — over-255 Content-Type capped + NUL in a text attachment stripped ->
       the email STILL ingests (no silent drop). CONFIRMS-FIXED.
  C10  (ingest leg) NUL in subject/body/Message-ID -> ingests with no insert crash. CONFIRMS-FIXED.
  C01  (runner leg) a deep-nested multipart whose parse raises RecursionError, fed through a real
       ConnectorSyncRunner: _ingest_one catches it -> 'failed' -> tracker.fail(uid) -> the cursor
       advances PAST the uid + records it in failed_uids -> the crafted email is dropped FOREVER
       (cleared only on a UIDVALIDITY reset). Ledger: failed=1, stored=0.

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/suite_c_ingest_runner.py
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base.incremental_fetch import FetchBatch, FetchedMessage, FolderCursor
from app.connectors.base.registry import ConnectorRegistry
from app.connectors.enums import ConnectorType
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.imap.services.email_ingest_service import EmailIngestService, IngestOutcome
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_sync_cursor_repository import (
    ConnectorSyncCursorRepository,
)
from app.connectors.repositories.connector_sync_run_repository import ConnectorSyncRunRepository
from app.connectors.security.credential_cipher import CredentialCipher
from app.connectors.sync.connector_sync_runner import ConnectorSyncRunner
from app.core.database import GlobalSessionLocal

STAMP = uuid.uuid4().hex[:12]
MAILBOX = "owner@acme.com"
results: list[tuple[str, bool, str]] = []

# Every org_id we create rows under — cleaned up by org_id in the finally (entity graph + emails +
# connector + sync tables). uuid4 throwaway orgs, so WHERE org_id = X is complete and safe.
created_orgs: set[uuid.UUID] = set()

# Tables that hold rows org-scoped by our ingest/runner runs (entity graph does NOT cascade).
_PURGE_TABLES = (
    "email_recipient",
    "email_attachment",
    "email_message",
    "person_company",
    "person_alias",
    "person_email",
    "company_domain",
    "person",
    "company",
    "connector_sync_cursor",
    "connector_sync_run",
    "connector_connection",
)


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def new_org() -> uuid.UUID:
    org = uuid.uuid4()
    created_orgs.add(org)
    return org


async def seed_connection(session: AsyncSession, org_id: uuid.UUID, mailbox: str = MAILBOX) -> ConnectorConnection:
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name="Mailbox",
        auth_method="app_password",
        username=mailbox,
        config={"host": "mail.example.com", "port": 993, "use_ssl": True},
        secret_ciphertext=b"\x00" * 32,
        secret_key_version=1,
        status="configured",
    )
    session.add(connection)
    await session.flush()
    return connection


async def count(session: AsyncSession, model: type, org_id: uuid.UUID) -> int:
    return (
        await session.execute(select(func.count()).select_from(model).where(model.org_id == org_id))
    ).scalar_one()


# ---------------------------------------------------------------- C02 dedup poisoning
async def c02_dedup_poisoning() -> None:
    org = new_org()
    async with GlobalSessionLocal() as session:
        connection = await seed_connection(session, org)
        service = EmailIngestService(session, connection)
        shared_id = f"<thread-{STAMP}@bank.example>"
        # 1) the attacker (or an innocent earlier message) plants the Message-ID first.
        decoy = (
            f"From: attacker@evil.example\r\nTo: {MAILBOX}\r\nSubject: decoy\r\n"
            f"Message-ID: {shared_id}\r\n\r\nNothing to see."
        ).encode()
        # 2) the GENUINE email arrives later reusing the SAME Message-ID but different content/From.
        genuine = (
            f"From: cfo@acme.com\r\nTo: {MAILBOX}\r\nSubject: WIRE THE 2M NOW\r\n"
            f"Message-ID: {shared_id}\r\n\r\nApprove the payment immediately."
        ).encode()

        first = await service.ingest_email(decoy)
        await session.commit()
        second = await service.ingest_email(genuine)
        await session.commit()

        rows = (
            (await session.execute(select(EmailMessage).where(EmailMessage.org_id == org)))
            .scalars()
            .all()
        )
        stored_subjects = [r.subject for r in rows]
    # DEFENCE would store BOTH (or flag the collision). It SKIPS the genuine one -> one row -> FAIL.
    check(
        "C02_reused_message_id_silently_skips_genuine_email",
        not (first is IngestOutcome.STORED and second is IngestOutcome.SKIPPED and len(rows) == 1),
        f"first={first} second={second} stored_count={len(rows)} subjects={stored_subjects} "
        f"(genuine 'WIRE THE 2M NOW' suppressed by the decoy's Message-ID)",
    )


# ---------------------------------------------------------------- C04 ingest leg (controls stored)
async def c04_controls_stored() -> None:
    org = new_org()
    async with GlobalSessionLocal() as session:
        connection = await seed_connection(session, org)
        service = EmailIngestService(session, connection)
        raw = (
            f"From: a@x.com\r\nTo: {MAILBOX}\r\nSubject: ev\x1b[31mil\x01\r\n"
            f"Message-ID: <ctl-{STAMP}@x>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            "bell\x07 esc\x1b[2Jclear"
        ).encode()
        outcome = await service.ingest_email(raw)
        await session.commit()
        row = (
            await session.execute(select(EmailMessage).where(EmailMessage.org_id == org))
        ).scalar_one()
        body_ctl = "\x1b" in (row.body_text or "") and "\x07" in (row.body_text or "")
        subj_ctl = row.subject is not None and "\x1b" in row.subject
    # DEFENCE would strip/escape C0. They are STORED verbatim -> log/terminal injection -> FAIL.
    check(
        "C04_control_chars_persisted_into_db_columns",
        not (outcome is IngestOutcome.STORED and body_ctl and subj_ctl),
        f"outcome={outcome} body_has_esc/bel={body_ctl} subj_has_esc={subj_ctl} subject={row.subject!r}",
    )


# ---------------------------------------------------------------- C09 regression holds
async def c09_regression_holds() -> None:
    org = new_org()
    async with GlobalSessionLocal() as session:
        connection = await seed_connection(session, org)
        service = EmailIngestService(session, connection)
        giant_ct = b"a" * 300 + b"/" + b"b" * 300  # >255 content-type
        raw = (
            b"From: a@x.com\r\nTo: " + MAILBOX.encode() + b"\r\nMessage-ID: <reg-" + STAMP.encode() + b"@x>\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
            b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
            b'--B\r\nContent-Type: text/csv\r\nContent-Disposition: attachment; filename="a.csv"\r\n\r\n'
            b"x,y\x00z\r\n"  # NUL in a TEXT attachment
            b"--B\r\nContent-Type: " + giant_ct + b'\r\nContent-Disposition: attachment; filename="b"\r\n\r\nZ\r\n'
            b"--B--\r\n"
        )
        outcome = await service.ingest_email(raw)
        await session.commit()
        atts = (
            (await session.execute(select(EmailAttachment).where(EmailAttachment.org_id == org)))
            .scalars()
            .all()
        )
        ct_capped = all((a.content_type is None) or len(a.content_type) <= 255 for a in atts)
        no_nul = all("\x00" not in (a.extracted_text or "") for a in atts)
    # DEFENCE HELD = the email ingested + both fixes held -> PASS (CONFIRMS-FIXED).
    check(
        "C09_oversize_ct_and_nul_attachment_still_ingests",
        outcome is IngestOutcome.STORED and len(atts) == 2 and ct_capped and no_nul,
        f"outcome={outcome} attachments={len(atts)} ct_capped={ct_capped} no_nul_text={no_nul}",
    )


# ---------------------------------------------------------------- C10 ingest leg
async def c10_nul_ingests() -> None:
    org = new_org()
    async with GlobalSessionLocal() as session:
        connection = await seed_connection(session, org)
        service = EmailIngestService(session, connection)
        raw = (
            b"From: a@x.com\r\nTo: " + MAILBOX.encode() + b"\r\nMessage-ID: <n\x00ul-" + STAMP.encode() + b"@x>\r\n"
            b"Subject: su\x00bj\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nbo\x00dy"
        )
        outcome = await service.ingest_email(raw)
        await session.commit()
        row = (
            await session.execute(select(EmailMessage).where(EmailMessage.org_id == org))
        ).scalar_one()
        clean = (
            "\x00" not in (row.subject or "")
            and "\x00" not in (row.body_text or "")
            and "\x00" not in (row.dedup_key or "")
        )
    # DEFENCE HELD = stored with no NUL anywhere, no crash -> PASS (CONFIRMS-FIXED).
    check(
        "C10_nul_bearing_email_ingests_without_crash",
        outcome is IngestOutcome.STORED and clean,
        f"outcome={outcome} subject={row.subject!r} body={row.body_text!r} dedup_key={row.dedup_key!r}",
    )


# ---------------------------------------------------------------- C01 runner-fail chain
@dataclass
class _FakeConnector:
    """Inline SupportsIncrementalFetch: streams ONE crafted batch + records cursors."""

    batches: list[FetchBatch]

    def __post_init__(self) -> None:
        self.count_cursors: list[dict] = []
        self.fetch_cursors: list[dict] = []

    async def count_pending(self, cursors: Mapping[str, FolderCursor]) -> int:
        self.count_cursors.append(dict(cursors))
        return sum(len(b.messages) for b in self.batches)

    async def fetch_incremental(self, cursors: Mapping[str, FolderCursor]) -> AsyncIterator[FetchBatch]:
        self.fetch_cursors.append(dict(cursors))
        for batch in self.batches:
            yield batch


def _deep_multipart_bytes() -> bytes:
    body = "Content-Type: text/plain\r\n\r\nhello\r\n"
    for i in range(300):
        b = f"B{i}"
        body = f'Content-Type: multipart/mixed; boundary="{b}"\r\n\r\n--{b}\r\n{body}\r\n--{b}--\r\n'
    return (f"From: a@x.com\r\nTo: {MAILBOX}\r\nMessage-ID: <deep-{STAMP}@x>\r\n" + body).encode()


async def c01_runner_drops_forever() -> None:
    org = new_org()
    cipher = CredentialCipher("c01-runner-key-not-secure-but-long-enough", require_secure=False)
    run_id = uuid.uuid4()
    async with GlobalSessionLocal() as session:
        # Seed a connection with a REAL ciphertext so the runner's _build_connector decrypts.
        connection = ConnectorConnection(
            org_id=org,
            connector_type="imap",
            display_name="Mailbox",
            auth_method="app_password",
            username=MAILBOX,
            config={"host": "mail.example.com", "port": 993, "use_ssl": True},
            secret_ciphertext=cipher.encrypt("app-password"),
            secret_key_version=cipher.key_version,
            status="configured",
        )
        session.add(connection)
        await session.flush()
        connection_id = connection.id
        await ConnectorConnectionRepository(session).claim_for_sync(org, connection_id, run_id, stale_seconds=300)
        await ConnectorSyncRunRepository(session).start_run(org, connection_id, run_id)
        await session.commit()

    poison = FetchedMessage(uid=42, raw_bytes=_deep_multipart_bytes(), internal_date=None)
    batch = FetchBatch(folder="INBOX", uidvalidity=777, messages=[poison], requested_uids=[42])
    connector = _FakeConnector(batches=[batch])
    registry = ConnectorRegistry()
    registry.register(ConnectorType.imap, lambda config, secret: connector)
    runner = ConnectorSyncRunner(cipher, registry, heartbeat_seconds=3600)

    await runner.run(org, connection_id, run_id)

    async with GlobalSessionLocal() as session:
        emails = await count(session, EmailMessage, org)
        ledger = (
            await session.execute(
                select(ConnectorSyncRun).where(ConnectorSyncRun.run_id == run_id)
            )
        ).scalar_one()
        cursor = (await ConnectorSyncCursorRepository(session).list_for_connection(org, connection_id)).get("INBOX")

    stepped_over = (
        emails == 0
        and ledger.messages_failed == 1
        and ledger.messages_stored == 0
        and cursor is not None
        and cursor.last_seen_uid >= 42
        and 42 in cursor.failed_uids
    )
    # DEFENCE would surface/retry the unparseable mail. Instead it is failed + cursor steps PAST it +
    # recorded in failed_uids -> permanent silent drop -> FAIL.
    check(
        "C01_runner_steps_over_recursionerror_email_forever",
        not stepped_over,
        f"emails={emails} ledger(failed={ledger.messages_failed},stored={ledger.messages_stored},status={ledger.status}) "
        f"cursor(last_seen={cursor.last_seen_uid if cursor else None},failed_uids={cursor.failed_uids if cursor else None}) "
        f"(uid 42 dropped FOREVER — cleared only on UIDVALIDITY reset)",
    )


async def cleanup() -> None:
    if not created_orgs:
        return
    async with GlobalSessionLocal() as session:
        org_list = list(created_orgs)
        total = 0
        for table in _PURGE_TABLES:
            res = await session.execute(
                text(f"DELETE FROM {table} WHERE org_id = ANY(:orgs)"), {"orgs": org_list}
            )
            total += res.rowcount or 0
        await session.commit()
    print(f"cleanup: deleted {total} rows across {len(_PURGE_TABLES)} tables for {len(org_list)} run-stamped orgs")


async def main() -> None:
    print(f"=== SUITE C (ingest + runner) — stamp {STAMP} ===")
    try:
        await c02_dedup_poisoning()
        await c04_controls_stored()
        await c09_regression_holds()
        await c10_nul_ingests()
        await c01_runner_drops_forever()
    finally:
        await cleanup()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} DEFENCE checks held (a non-held check = a reproduced finding)")


asyncio.run(main())
