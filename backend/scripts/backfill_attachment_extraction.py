"""
Role: DEV driver (NOT the production sync path) — one-shot extraction backfill (design §4.2): walks
      the on-disk spike .eml corpus, recomputes each attachment's content_hash through the SAME
      parser the ingest used, and runs the NEW extraction seam over every email_attachment row
      still marked extraction_status='pending', updating extracted_text + status + detail
      (0016, EQ-7) + extractor provenance + the typed structured grid (0017, extracted_data — NULL
      for non-xlsx) in place. No IMAP re-fetch: the corpus on disk is the byte source.
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
  - ENGINE-UPGRADE REQUEUE (--requeue-extractor NAME --requeue-below-version N, both required
    together): BEFORE the normal pending pass, rows last written by extractor_name=NAME with a
    NUMERIC extractor_version < N are flipped back to 'pending' (org-scoped, same session
    conventions; the count is logged). This is how engine-upgrade backfills target stale rows
    the pending pass cannot otherwise reach — e.g. the EQ-3/EQ-4 repair re-runs every row the
    old decode rules damaged: --requeue-extractor text-decode --requeue-below-version 3. Our
    OWN extractors version with plain integer strings ('1', '2', '3'); vendor-engine versions
    ('0.11.9', 'unknown') are non-numeric and guarded out of the cast, never crashed on.
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
  - PER-ROW SAVEPOINT (mirrors the live sync path's connector_sync_runner._ingest_one): each row's
    UPDATE runs inside its own begin_nested() so a single un-storable row (e.g. a structured grid
    that survived extraction but trips a JSONB DataError) rolls back ONLY its savepoint and is
    skipped (counted under rows_db_failed) — it never poisons the surrounding batch transaction
    (up to ~200 rows of work) nor escapes to kill a multi-hour corpus run mid-flight. A run that
    skipped any row exits non-zero so the operator sees it.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from pathlib import Path
from uuid import UUID

from sqlalchemy import Integer, Row, case, cast, false, select, text, update
from sqlalchemy.exc import SQLAlchemyError
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
    """Fail fast unless the 0015 (extraction_status), 0016 (extraction_detail) AND 0017
    (extracted_data) columns exist on the target DB — the backfill writes all three."""
    async with GlobalSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'email_attachment' "
                "AND column_name IN ('extraction_status', 'extraction_detail', 'extracted_data')"
            )
        )
        if result.scalar() != 3:
            raise SystemExit(
                "email_attachment.extraction_status/extraction_detail/extracted_data missing — run "
                "'alembic upgrade head' (migrations 0015 + 0016 + 0017) first."
            )


async def _requeue_stale_rows(
    session: AsyncSession, org_id: UUID, extractor_name: str, below_version: int
) -> int:
    """Flip rows produced by an OLDER version of one of OUR extractors back to 'pending'.

    Engine-upgrade targeting: rows damaged by a bug fixed in extractor version N sit at a
    TERMINAL status ('extracted' with mojibake / raw markup — audits EQ-3/EQ-4), so the normal
    pending pass can never reach them. Matching is org-scoped on extractor_name plus a NUMERIC
    extractor_version below `below_version`. Our own extractors version with plain integer
    strings ('1', '2', '3' — e.g. text-decode); the CASE guard skips non-numeric versions
    (vendor engines like '0.11.9' / 'unknown') instead of crashing the int cast — Postgres
    guarantees a CASE condition evaluates before its THEN branch (plain WHERE conjunctions
    carry no such ordering guarantee). Returns the number of rows flipped.
    """
    version_is_numeric = EmailAttachment.extractor_version.op("~")(r"^[0-9]{1,9}$")
    result = await session.execute(
        update(EmailAttachment)
        .where(
            EmailAttachment.org_id == org_id,
            EmailAttachment.extractor_name == extractor_name,
            case(
                (
                    version_is_numeric,
                    cast(EmailAttachment.extractor_version, Integer) < below_version,
                ),
                else_=false(),
            ),
        )
        .values(extraction_status="pending")
    )
    return result.rowcount or 0


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
) -> int | None:
    """Write one extraction outcome onto ONE still-pending row (by id), SAVEPOINT-isolated.

    Returns the affected row count (0 when another process already filled it — idempotence: never
    overwrite done), or None when the UPDATE itself raised a DB error (e.g. a structured grid that
    survived extraction but is not JSONB-storable). Each row runs inside its OWN begin_nested()
    savepoint — the same poison-isolation posture as the live sync path (connector_sync_runner.
    _ingest_one): a single bad row rolls back only its savepoint, NEVER poisoning the surrounding
    batch transaction (up to _COMMIT_BATCH_ROWS rows of work) nor escaping to kill the whole run.
    A None return tells the caller to skip-and-continue rather than commit a half-aborted batch.
    """
    try:
        async with session.begin_nested():
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
                    extraction_detail=outcome.detail,
                    extractor_name=outcome.extractor_name,
                    extractor_version=outcome.extractor_version,
                    extracted_data=outcome.structured,
                )
            )
        return result.rowcount or 0
    except SQLAlchemyError as db_error:
        # The savepoint already rolled back this row; the outer batch transaction is still usable.
        # Print the exception TYPE only — str(exc) can echo bound parameter values (cell content).
        print(f"  [db-fail] {type(db_error).__name__} @ row {row_id}", flush=True)
        return None


async def _backfill(
    root: Path, files: list[Path], org_id: UUID, requeue: tuple[str, int] | None
) -> Counter:
    """Optionally requeue version-stale rows, collect disk payloads for the pending hashes,
    then update each pending ROW under its own declared content_type, in batched commits."""
    tally: Counter = Counter()
    async with scoped_session(org_id) as session:
        if requeue is not None:
            extractor_name, below_version = requeue
            requeued = await _requeue_stale_rows(session, org_id, extractor_name, below_version)
            print(
                f"requeued {requeued} rows to pending: extractor_name={extractor_name!r} "
                f"with numeric extractor_version < {below_version}",
                flush=True,
            )
            await session.commit()
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
            if rows is None:
                tally["rows_db_failed"] += 1  # savepoint rolled this row back — keep going
                continue
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


async def _run(root: Path, org_id: UUID, requeue: tuple[str, int] | None) -> int:
    """Drive the whole backfill: env guard, migration check, optional requeue + corpus walk,
    summary."""
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

    tally = await _backfill(root, files, org_id, requeue)

    print("=" * 60)
    residue = tally.pop("hashes_not_on_disk", 0)
    failed_files = tally.pop("files_failed", 0)
    rows_db_failed = tally.pop("rows_db_failed", 0)
    updated = sum(tally.values())
    print(f"DONE — updated {updated} attachment rows; per-status: {dict(sorted(tally.items()))}")
    print(f"       pending hashes not found on disk: {residue}; unreadable files: {failed_files}")
    print(f"       rows skipped on a DB error (savepoint rolled back): {rows_db_failed}")
    print("=" * 60)
    return 1 if (failed_files or rows_db_failed) else 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the backfill; the requeue flags must be given together."""
    parser = argparse.ArgumentParser(
        description="Re-run attachment extraction over pending email_attachment rows (dev)."
    )
    parser.add_argument("root", help="dump root (e.g. /spikes/imap_dump)")
    parser.add_argument("--org", default=str(_DEV_INGEST_ORG), help="target org_id (uuid)")
    parser.add_argument(
        "--requeue-extractor",
        metavar="NAME",
        default=None,
        help="BEFORE the pending pass, requeue rows last written by this extractor_name "
        "(requires --requeue-below-version; e.g. text-decode)",
    )
    parser.add_argument(
        "--requeue-below-version",
        metavar="N",
        type=int,
        default=None,
        help="requeue only rows whose NUMERIC extractor_version is below N "
        "(requires --requeue-extractor; non-numeric versions never match)",
    )
    args = parser.parse_args(argv)
    if (args.requeue_extractor is None) != (args.requeue_below_version is None):
        parser.error("--requeue-extractor and --requeue-below-version must be given together")
    requeue = (
        (args.requeue_extractor, args.requeue_below_version)
        if args.requeue_extractor is not None
        else None
    )
    return asyncio.run(_run(Path(args.root), UUID(args.org), requeue))


if __name__ == "__main__":
    raise SystemExit(main())
