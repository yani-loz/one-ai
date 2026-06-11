"""
Role: DEV driver (NOT the production sync path) — loads the on-disk spike .eml corpus into the DB
      through the real EmailIngestService, so we can SEE the parse → DB → person/company graph fill
      up end-to-end. The production path is the future in-app SyncRunner; this is the disk bridge.
Used by: a developer, run inside a container with the gitignored spike dump mounted read-only:
  docker compose run --rm -v "${PWD}/spikes:/spikes:ro" backend \
      uv run python -m scripts.ingest_imap_dump /spikes/imap_dump [--limit N] [--org <uuid>]
Depends on: app.core (database/config), app.connectors (ingest service, connection model, cipher),
            app.identity (Organization model + repository — the org-registry get-or-create).
Key invariants:
  - DEV ONLY: refuses to run in any environment that requires secure secrets (everything except
    app_env 'local' | 'test' — Settings.requires_secure_secrets, the config boot-guard predicate).
  - GET-OR-CREATE the organizations row for --org BEFORE any tenant write (audit H-2: no phantom
    tenants — erasure/lifecycle anchor on the org row, and migration 0014's org-root FKs reject
    unregistered org_ids). organizations is the PLATFORM plane (the tenant root has no org_id of
    its own), so this one write runs on the GLOBAL session factory — same as scripts.seed_identity.
  - All OTHER rows are tenant rows, written on scoped_session(org_id) — the RLS seam, same as the
    production sync runner — never on the BYPASSRLS global engine, so RLS guards even this driver.
  - GET-OR-CREATE the connection by (org, 'imap', mailbox) — minting a fresh connection each run
    would re-ingest the whole corpus under a new connection_id and defeat dedup.
  - A missing/non-directory root or a root with ZERO .eml files exits non-zero with a clear
    message — a typo'd path must never report a successful DONE stored=0 run.
  - ONE transaction per email (commit each) → resumable; a re-run skips what's already stored.
    stored / skipped / failed are tallied separately (skipped ≫ 0 is EXPECTED — the same logical
    email appears re-serialized in many IMAP folders and dedups by CONTENT IDENTITY: Message-ID +
    normalized headers + decoded body text + attachment content hashes, so folder copies whose
    raw bytes differ only by regenerated MIME boundaries still collapse to one row).
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
from app.core.database import GlobalSessionLocal, scoped_session
from app.identity.models.organization import Organization
from app.identity.repositories.organization_repository import OrganizationRepository

# A fixed, recognizable DEV org id for disk ingests. The demo seed orgs (scripts.seed_identity)
# get server-generated random UUIDs (gen_random_uuid()), so this id cannot collide with them.
_DEV_INGEST_ORG = UUID("d1500000-0000-0000-0000-000000000001")
# The org-registry row minted for a not-yet-registered --org (matched by id, so re-runs and
# pre-registered orgs — e.g. a seeded demo org — skip the insert entirely).
_DEV_ORG_SLUG = "dev-ingest"
_DEV_ORG_NAME = "Dev Disk-Ingest Org"


def _refuse_in_secure_env() -> None:
    """Abort in any environment that requires secure secrets — this is a dev-only driver.

    Gates on the SAME predicate as the config secret guard (Settings.requires_secure_secrets):
    only app_env 'local' | 'test' may run a disk ingest. A staging/production box (or a typo'd
    app_env) must never have spike-corpus tenant rows minted into it.
    """
    settings = get_settings()
    if settings.requires_secure_secrets:
        raise SystemExit(
            f"Refusing disk ingest: app_env={settings.app_env!r} requires secure secrets. "
            "This DEV-ONLY driver may only run when app_env is 'local' or 'test'."
        )


async def _assert_migrated() -> None:
    """Fail fast with a clear message if the Connect tables aren't migrated on the target DB."""
    async with GlobalSessionLocal() as session:
        result = await session.execute(text("SELECT to_regclass('public.email_message')"))
        if result.scalar() is None:
            raise SystemExit("Connect tables missing — run 'alembic upgrade head' first.")


async def _ensure_org_registered(org_id: UUID) -> None:
    """GET-OR-CREATE the organizations row for the target org BEFORE any tenant write (H-2).

    Contract: matched by id — an already-registered org (a seeded demo org, or a prior run's
    dev org) is left untouched; an unregistered id is inserted as the recognizable dev-ingest
    org (slug 'dev-ingest', active). organizations is the PLATFORM plane, so this single write
    uses the GLOBAL session factory (mirroring scripts.seed_identity); every tenant row stays on
    scoped_session. Without this row, migration 0014's org-root FKs reject the very first tenant
    insert — and GDPR erasure/lifecycle could never see the tenant at all (the phantom-tenant
    hazard this function closes).

    Edge case: two DIFFERENT unregistered --org ids would both claim the unique 'dev-ingest'
    slug; the second insert fails loudly on the slug UNIQUE — register that org yourself first.
    """
    async with GlobalSessionLocal() as session:
        organizations = OrganizationRepository(session)
        if await organizations.get_by_id(org_id) is not None:
            return
        await organizations.add(
            Organization(id=org_id, slug=_DEV_ORG_SLUG, name=_DEV_ORG_NAME, status="active")
        )
        await session.commit()
        print(f"  [created] organizations row {org_id} (slug={_DEV_ORG_SLUG})", flush=True)


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
    """Ingest one mailbox's .eml files under its connection, one transaction per email.

    Runs on scoped_session(org_id) — the tenant/RLS seam (same as the production sync
    runner) — so every write is RLS-checked against the target org, never BYPASSRLS.
    """
    tally: Counter = Counter()
    async with scoped_session(org_id) as session:
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
    """Drive the whole disk ingest: env guard, migration check, org registration, then ingest."""
    _refuse_in_secure_env()
    await _assert_migrated()

    def _collect_eml_files() -> list[Path] | None:
        """Disk walk off the event loop: None ⇒ root missing/not a dir; [] ⇒ zero .eml files."""
        return sorted(root.rglob("*.eml")) if root.is_dir() else None

    files = await asyncio.to_thread(_collect_eml_files)
    if files is None:
        raise SystemExit(f"root {root} does not exist or is not a directory — nothing to ingest.")
    if not files:
        # A typo'd path must exit non-zero, never report a successful DONE stored=0 run.
        raise SystemExit(f"no .eml files found under {root} — is the dump root path right?")
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
    await _ensure_org_registered(org_id)  # the org row must exist before any tenant write (H-2)
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
