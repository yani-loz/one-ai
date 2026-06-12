"""
Role: DEV driver (NOT the production sync path) — one-shot extraction backfill (design §4.2): walks
      the on-disk spike .eml corpus, recomputes each attachment's content_hash through the SAME
      parser the ingest used, and runs the NEW extraction seam over every email_attachment row
      still marked extraction_status='pending', updating extracted_text + status + extractor
      provenance in place. No IMAP re-fetch: the corpus on disk is the byte source.
Used by: a developer, run inside a container with the gitignored spike dump mounted read-only:
  docker compose run --rm -v "${PWD}/spikes:/spikes:ro" backend \
      uv run python -m scripts.backfill_attachment_extraction /spikes/imap_dump [--org <uuid>]
  (Per the connect-ingest memory: run it ALONE — the pytest suite truncates these tables.)
Depends on: app.core (database/config), app.connectors.imap.parsing (parse_email + extract_text +
            ParsedAttachment + the ExtractionResult contract), app.connectors.imap.models.email
            (EmailAttachment).
Key invariants:
  - DEV ONLY: refuses to run in any environment that requires secure secrets (everything except
    app_env 'local' | 'test' — Settings.requires_secure_secrets, the config boot-guard predicate)
    — mirrored verbatim from scripts.ingest_imap_dump.
  - PER-ROW DISPATCH (2026-06-11 review fix): each pending DB ROW is extracted under ITS OWN
    declared content_type — a content_hash shared by an application/pdf copy and an
    application/octet-stream copy must NOT cross-stamp one outcome onto both (that froze the
    other copy's verdict wrong forever). Disk supplies only the payload bytes (hash → bytes);
    outcomes are memoized per (content_hash, content_type) so identical rows extract once.
  - IDEMPOTENT: only rows with extraction_status='pending' are touched (each UPDATE re-checks the
    status by row id, so a re-run — or a row another process already filled — never overwrites).
  - All tenant writes run on scoped_session(org_id) — the RLS seam, same as the ingest driver —
    never on the BYPASSRLS global engine.
  - HASH FIDELITY: attachments are re-derived via parse_email (the exact decode path that
    computed content_hash at ingest), so disk bytes match DB rows byte-for-byte or not at all.
  - MEMORY: payload bytes for every pending hash are held in RAM during the run (dev-only
    trade-off; the corpus dedup keeps one copy per distinct hash and the walk stops early once
    every wanted hash is found).
  - A missing/non-directory root or a root with ZERO .eml files exits non-zero with a clear
    message — a typo'd path must never report a successful DONE 0-updates run.
  - Batched commits (every ~200 updated rows) → resumable; progress lines per 500 files; the
    final summary tallies updated rows per extraction status + the pending residue.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from pathlib import Path
from uuid import UUID

from sqlalchemy import Row, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment
from app.connectors.imap.parsing import (
    ExtractionResult,
    ParsedAttachment,
    extract_text,
    parse_email,
)
from app.core.config import get_settings
from app.core.database import GlobalSessionLocal, scoped_session

# The fixed DEV ingest org (mirrors scripts.ingest_imap_dump._DEV_INGEST_ORG — the org the disk
# corpus was ingested under; --org overrides for a re-ingest done under a different tenant).
_DEV_INGEST_ORG = UUID("d1500000-0000-0000-0000-000000000001")

# Commit after this many newly-updated rows (resumable, bounded transaction size).
_COMMIT_BATCH_ROWS = 200

# Rows whose stored content_type is NULL still dispatch deterministically: RFC 2046's default for
# unlabeled binary content (extract_text then reports unsupported_format honestly).
_FALLBACK_CONTENT_TYPE = "application/octet-stream"


def _refuse_in_secure_env() -> None:
    """Abort in any environment that requires secure secrets — this is a dev-only driver.

    Gates on the SAME predicate as the config secret guard (Settings.requires_secure_secrets):
    only app_env 'local' | 'test' may run a disk backfill (mirrors scripts.ingest_imap_dump).
    """
    settings = get_settings()
    if settings.requires_secure_secrets:
        raise SystemExit(
            f"Refusing backfill: app_env={settings.app_env!r} requires secure secrets. "
            "This DEV-ONLY driver may only run when app_env is 'local' or 'test'."
        )


async def _assert_migrated() -> None:
    """Fail fast unless migration 0015's extraction_status column exists on the target DB."""
    async with GlobalSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'email_attachment' AND column_name = 'extraction_status'"
            )
        )
        if result.scalar() is None:
            raise SystemExit(
                "email_attachment.extraction_status missing — run 'alembic upgrade head' "
                "(migration 0015) first."
            )


async def _load_pending_rows(session: AsyncSession, org_id: UUID) -> list[Row]:
    """The work queue: every pending row's (id, content_hash, content_type) — content_type is
    PER ROW because the same payload bytes can arrive under different declared types."""
    result = await session.execute(
        select(
            EmailAttachment.id, EmailAttachment.content_hash, EmailAttachment.content_type
        ).where(
            EmailAttachment.org_id == org_id,
            EmailAttachment.extraction_status == "pending",
            EmailAttachment.content_hash.is_not(None),
        )
    )
    return list(result.all())


async def _collect_disk_payloads(
    root: Path, files: list[Path], wanted_hashes: set[str]
) -> tuple[dict[str, bytes], int]:
    """Walk the corpus and collect payload bytes for every wanted content_hash.

    Returns (hash → payload bytes, unreadable-file count). Stops reading files early once every
    wanted hash has been found. One bad email never aborts the walk.
    """
    payloads: dict[str, bytes] = {}
    files_failed = 0
    for index, path in enumerate(files, start=1):
        if len(payloads) == len(wanted_hashes):
            break  # every pending hash found — no need to read the rest of the corpus
        try:
            raw = await asyncio.to_thread(path.read_bytes)
            mailbox = _mailbox_of(root, path)
            # parse_email is pure CPU (the ingest's exact decode path) — off the loop.
            parsed = await asyncio.to_thread(parse_email, raw, mailbox)
        except Exception as exc:  # one bad email must never abort the backfill
            files_failed += 1
            # Print the exception TYPE only — str(exc) can echo message content.
            print(f"  [fail] {type(exc).__name__} @ {path.name}", flush=True)
            continue
        for attachment in parsed.attachments:
            if attachment.content_hash in wanted_hashes and attachment.content_hash not in payloads:
                payloads[attachment.content_hash] = attachment.payload
        if index % 500 == 0:
            print(
                f"  {index}/{len(files)} files; "
                f"{len(wanted_hashes) - len(payloads)} hashes still unmatched",
                flush=True,
            )
    return payloads, files_failed


async def _row_extraction(
    cache: dict[tuple[str, str], ExtractionResult],
    content_hash: str,
    declared_content_type: str | None,
    payload: bytes,
) -> ExtractionResult:
    """One row's extraction outcome under ITS declared content_type, memoized per
    (content_hash, content_type) — identical rows extract once, divergent declarations never
    cross-stamp. extract_text is pure CPU (pdfplumber/pypdf) — off the loop; the seam never
    raises."""
    content_type = declared_content_type or _FALLBACK_CONTENT_TYPE
    key = (content_hash, content_type)
    if key not in cache:
        attachment = ParsedAttachment(
            filename=None,
            content_type=content_type,
            size_bytes=len(payload),
            content_hash=content_hash,
            is_inline=False,
            content_id=None,
            payload=payload,
        )
        cache[key] = await asyncio.to_thread(extract_text, attachment)
    return cache[key]


async def _update_pending_row(
    session: AsyncSession, org_id: UUID, row_id: UUID, outcome: ExtractionResult
) -> int:
    """Write one extraction outcome onto ONE still-pending row (by id); returns the row count
    (0 when another process already filled it — idempotence: never overwrite done)."""
    result = await session.execute(
        update(EmailAttachment)
        .where(
            EmailAttachment.org_id == org_id,
            EmailAttachment.id == row_id,
            EmailAttachment.extraction_status == "pending",
        )
        .values(
            extracted_text=outcome.text,
            extraction_status=outcome.status,
            extractor_name=outcome.extractor_name,
            extractor_version=outcome.extractor_version,
        )
    )
    return result.rowcount or 0


async def _backfill(root: Path, files: list[Path], org_id: UUID) -> Counter:
    """Collect disk payloads for the pending hashes, then update each pending ROW under its own
    declared content_type, in batched commits."""
    tally: Counter = Counter()
    async with scoped_session(org_id) as session:
        pending_rows = await _load_pending_rows(session, org_id)
        wanted_hashes = {row.content_hash for row in pending_rows}
        print(
            f"pending: {len(pending_rows)} rows over {len(wanted_hashes)} distinct "
            f"content hashes in org {org_id}",
            flush=True,
        )
        if not pending_rows:
            return tally

        payloads, files_failed = await _collect_disk_payloads(root, files, wanted_hashes)
        tally["files_failed"] = files_failed
        outcomes: dict[tuple[str, str], ExtractionResult] = {}
        batch_updates = 0
        for row in pending_rows:
            payload = payloads.get(row.content_hash)
            if payload is None:
                continue  # hash not found on disk — counted once per hash below
            outcome = await _row_extraction(outcomes, row.content_hash, row.content_type, payload)
            rows = await _update_pending_row(session, org_id, row.id, outcome)
            tally[outcome.status] += rows
            batch_updates += rows
            if batch_updates >= _COMMIT_BATCH_ROWS:
                await session.commit()
                batch_updates = 0
        await session.commit()
        tally["hashes_not_on_disk"] = len(wanted_hashes - set(payloads))
    return tally


def _mailbox_of(root: Path, path: Path) -> str:
    """The <mailbox> path segment directly under the dump root (parse_email's direction input)."""
    try:
        return path.relative_to(root).parts[0]
    except (ValueError, IndexError):
        return "unknown@local"


async def _run(root: Path, org_id: UUID) -> int:
    """Drive the whole backfill: env guard, migration check, corpus walk, summary."""
    _refuse_in_secure_env()
    await _assert_migrated()

    def _collect_eml_files() -> list[Path] | None:
        """Disk walk off the event loop: None ⇒ root missing/not a dir; [] ⇒ zero .eml files."""
        return sorted(root.rglob("*.eml")) if root.is_dir() else None

    files = await asyncio.to_thread(_collect_eml_files)
    if files is None:
        raise SystemExit(f"root {root} does not exist or is not a directory — nothing to do.")
    if not files:
        # A typo'd path must exit non-zero, never report a successful DONE 0-updates run.
        raise SystemExit(f"no .eml files found under {root} — is the dump root path right?")
    print(f"backfilling from {len(files)} .eml files into org {org_id}")

    tally = await _backfill(root, files, org_id)

    print("=" * 60)
    residue = tally.pop("hashes_not_on_disk", 0)
    failed_files = tally.pop("files_failed", 0)
    updated = sum(tally.values())
    print(f"DONE — updated {updated} attachment rows; per-status: {dict(sorted(tally.items()))}")
    print(f"       pending hashes not found on disk: {residue}; unreadable files: {failed_files}")
    print("=" * 60)
    return 1 if failed_files else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-run attachment extraction over pending email_attachment rows (dev)."
    )
    parser.add_argument("root", help="dump root (e.g. /spikes/imap_dump)")
    parser.add_argument("--org", default=str(_DEV_INGEST_ORG), help="target org_id (uuid)")
    args = parser.parse_args(argv)
    return asyncio.run(_run(Path(args.root), UUID(args.org)))


if __name__ == "__main__":
    raise SystemExit(main())
