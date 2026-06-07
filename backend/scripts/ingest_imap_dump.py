"""
Role: DEV driver (NOT the production sync path) — loads the on-disk spike .eml corpus into the DB
      through the real EmailIngestService, so we can SEE the parse → DB → person/company graph fill
      up end-to-end. The production path is the future in-app SyncRunner; this is the disk bridge.
Used by: a developer, run inside a container with the gitignored spike dump mounted read-only:
  docker compose run --rm -v "${PWD}/spikes:/spikes:ro" backend \
      uv run python -m scripts.ingest_imap_dump /spikes/imap_dump [--limit N] [--org <uuid>]
Depends on: app.core (database/config), app.connectors (ingest service, connection model, cipher).
Key invariants:
  - GET-OR-CREATE the connection by (org, 'imap', mailbox) — minting a fresh connection each run
    would re-ingest the whole corpus under a new connection_id and defeat dedup.
  - ONE transaction per email (commit each) → resumable; a re-run skips what's already stored.
    stored / skipped / failed are tallied separately (skipped ≫ 0 is EXPECTED — the same logical
    email appears in many IMAP folders and dedups by Message-ID).
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.services.email_ingest_service import EmailIngestService, IngestOutcome
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.security.credential_cipher import CredentialCipher
from app.core.config import get_settings
from app.core.database import GlobalSessionLocal

# A fixed DEV org for disk ingests — distinct from the demo seed orgs (00000000-…-0001/0002).
_DEV_INGEST_ORG = UUID("d1500000-0000-0000-0000-000000000001")


async def _assert_migrated() -> None:
    """Fail fast with a clear message if the Connect tables aren't migrated on the target DB."""
    async with GlobalSessionLocal() as session:
        result = await session.execute(text("SELECT to_regclass('public.email_message')"))
        if result.scalar() is None:
            raise SystemExit("Connect tables missing — run 'alembic upgrade head' first.")


async def _get_or_create_connection(
    session: AsyncSession, org_id: UUID, mailbox: str
) -> ConnectorConnection:
    """Return the (org, imap, mailbox) connection, creating one (encrypted placeholder) once."""
    result = await session.execute(
        select(ConnectorConnection).where(
            ConnectorConnection.org_id == org_id,
            ConnectorConnection.connector_type == "imap",
            ConnectorConnection.username == mailbox,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    cipher = CredentialCipher(get_settings().connector_secret_key, require_secure=False)
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name=f"Disk ingest: {mailbox}",
        auth_method="app_password",
        username=mailbox,
        config={"host": "disk-ingest", "port": 0, "use_ssl": False},
        secret_ciphertext=cipher.encrypt("disk-ingest-placeholder"),
        secret_key_version=cipher.key_version,
        status="configured",
    )
    session.add(connection)
    await session.flush()
    return connection


async def _ingest_mailbox(mailbox: str, files: list[Path], org_id: UUID) -> Counter:
    """Ingest one mailbox's .eml files under its connection, one transaction per email."""
    tally: Counter = Counter()
    async with GlobalSessionLocal() as session:
        connection = await _get_or_create_connection(session, org_id, mailbox)
        await session.commit()  # persist the connection (expire_on_commit=False keeps it usable)
        service = EmailIngestService(session, connection)

        for index, path in enumerate(files, start=1):
            try:
                raw = await asyncio.to_thread(path.read_bytes)  # don't block the loop on disk I/O
                outcome = await service.ingest_email(raw)
                await session.commit()
                tally[outcome.value] += 1
            except IntegrityError as exc:
                await session.rollback()
                # Only the dedup unique violation is an expected "already present"; any OTHER
                # integrity error (a real constraint breach) must surface as a failure, not hide.
                if "uq_email_message_dedup" in str(exc.orig):
                    tally["skipped"] += 1
                else:
                    tally["failed"] += 1
                    print(f"  [fail] {type(exc.orig).__name__} @ {path.name}", flush=True)
            except Exception as exc:  # one bad email must never abort the run
                await session.rollback()
                tally["failed"] += 1
                # Print the exception TYPE only — str(exc) can echo bound parameters (subject/body).
                print(f"  [fail] {type(exc).__name__} @ {path.name}", flush=True)
            if index % 500 == 0:
                print(f"  {mailbox}: {index}/{len(files)}  {dict(tally)}", flush=True)
    return tally


def _group_by_mailbox(root: Path, files: list[Path]) -> dict[str, list[Path]]:
    """Group .eml files by the <mailbox> path segment directly under the dump root."""
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        try:
            mailbox = path.relative_to(root).parts[0]
        except (ValueError, IndexError):
            mailbox = "unknown@local"
        groups[mailbox].append(path)
    return groups


async def _run(root: Path, limit: int, org_id: UUID) -> int:
    await _assert_migrated()
    files = await asyncio.to_thread(lambda: sorted(root.rglob("*.eml")))  # walk off the event loop
    if limit > 0:
        files = files[:: max(1, len(files) // limit)][:limit]
    groups = _group_by_mailbox(root, files)
    not_addresses = [m for m in groups if "@" not in m]
    if not_addresses:
        # The <mailbox> segment must be an email address; folder names here mean --root points one
        # level too deep (at an account dir), which would split one mailbox across many fake
        # "connections" and defeat dedup. Fail fast.
        raise SystemExit(
            f"--root looks wrong: {not_addresses[:3]} are not addresses; point it at the dump ROOT."
        )
    print(f"ingesting {len(files)} .eml across {len(groups)} mailbox(es) into org {org_id}")

    total: Counter = Counter()
    for mailbox, mailbox_files in groups.items():
        total += await _ingest_mailbox(mailbox, mailbox_files, org_id)

    print("=" * 60)
    print(
        f"DONE — stored={total['stored']} skipped={total['skipped']} failed={total['failed']} "
        f"(IngestOutcome.STORED={IngestOutcome.STORED.value})"
    )
    print("=" * 60)
    return 1 if total["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Disk → DB ingest of the spike .eml corpus (dev).")
    parser.add_argument("root", help="dump root (e.g. /spikes/imap_dump)")
    parser.add_argument("--limit", type=int, default=0, help="cap emails ingested (0 = all)")
    parser.add_argument("--org", default=str(_DEV_INGEST_ORG), help="target org_id (uuid)")
    args = parser.parse_args(argv)
    return asyncio.run(_run(Path(args.root), args.limit, UUID(args.org)))


if __name__ == "__main__":
    raise SystemExit(main())
