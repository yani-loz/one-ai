"""
Role: SNAPSHOT_V1 (contract §5.1) — the text-snapshot instrument: the pure per-artifact record
      (`snapshot_record`), the manifest text digest (`text_digest_of` / `text_digest_for_records`),
      the count-only manifest comparison (`compare_manifests`) and the database-backed emitter
      (`emit_snapshot`) that writes one org's artifacts under `<release>/snapshots/<text_digest>/`.
Used by: tools.mem01_verify.release (cut writes the snapshot), .corpus_identity (reports
      `text_digest` beside `corpus_digest`), gates.gate_snap, and the sealed oracle
      tests/tools/mem01_verify/test_snapshot.py.
Depends on: tools.mem01_verify.hashing (`canonical_lines_digest`, `sha256_bytes`) and
      tools.mem01_verify.exceptions (`SnapshotError`); SQLAlchemy for the emitter's two SELECTs.
Key invariants:
  - `emit_snapshot` READS ONLY, through the caller's R6 snapshot session (never the person-bound
    reader plane): it never commits, rolls back or writes, and every statement carries an
    explicit `org_id` filter on top of the session's tenant scope.
  - The record is the STORED text verbatim: no normalization, no newline translation, no
    trimming. A NULL column becomes the empty string with `stored_null = True`; its sha256 is
    the sha256 of zero bytes.
  - `sha256`/`byte_len` are over the UTF-8 encoding; `scalar_len` counts Unicode scalars;
    `line_count` counts `\\n` only (§16.10) plus one for a non-empty text with no trailing `\\n`.
  - `beyond_redact_scan_cap` is scalar-based and strict: `scalar_len > 2_000_000`.
  - `artifact_id` is `<kind>:<source_id>` and is unique inside one snapshot; every manifest
    consumer here treats a repeated `artifact_id` as corruption.
  - `text_digest` depends on exactly `artifact_id`, `sha256` and `stored_null` — nothing else in
    the manifest row can move it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from tools.mem01_verify.exceptions import SnapshotError
from tools.mem01_verify.hashing import canonical_lines_digest, sha256_bytes

SNAPSHOT_VERSION = "SNAPSHOT_V1"

SnapshotKind = Literal["email_body", "email_subject", "attachment_text"]

SNAPSHOT_KINDS: tuple[SnapshotKind, ...] = ("email_body", "email_subject", "attachment_text")
"""The three artifact kinds of §5.1, in the order the emitter reports them."""

REDACT_SCAN_CAP_SCALARS = 2_000_000
"""Secret-redaction scan cap: a text longer than this many scalars was not fully scanned."""

MANIFEST_FILENAME = "snapshot.manifest.jsonl"
RECORDS_FILENAME = "snapshot.records.jsonl"


@dataclass(frozen=True)
class SnapshotRecord:
    """One snapshot artifact: its identity, its measured lengths and its verbatim text.

    Field order follows contract §1.4. `text` is the stored string (empty when `stored_null`);
    the manifest line is this record MINUS `text`.
    """

    artifact_id: str
    kind: SnapshotKind
    source_table: str
    source_id: UUID
    org_id: UUID
    sha256: str
    byte_len: int
    scalar_len: int
    line_count: int
    stored_null: bool
    beyond_redact_scan_cap: bool
    stored_versions: Mapping[str, str | None]
    text: str


@dataclass(frozen=True)
class SnapshotSummary:
    """What one `emit_snapshot` produced: the digest, the per-kind counts and the two files."""

    version: str
    text_digest: str
    counts_by_kind: Mapping[str, int]
    manifest_path: Path
    records_path: Path


@dataclass(frozen=True)
class DiffCounts:
    """Counts only (§5.1): a manifest comparison never reveals artifact ids or content."""

    added: int
    removed: int
    changed: int
    unchanged: int


def count_lines(text: str) -> int:
    """Count logical lines of `text` under the §5.1 rule.

    Contract: the number of `\\n` scalars, plus one when the text is non-empty and does not end
    with `\\n`; 0 for the empty string. Only LF counts (§16.10) — a lone CR is not a line break.

    Args:
        text: the stored text (never None; a NULL column arrives here as "").

    Returns:
        The line count as defined above.
    """
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def snapshot_record(
    kind: str,
    source_table: str,
    source_id: UUID,
    org_id: UUID,
    text: str | None,
    stored_versions: Mapping[str, str | None],
) -> SnapshotRecord:
    """Build the SNAPSHOT_V1 record for one stored text artifact (pure, §5.1).

    The text is measured, never transformed: the sha256 and `byte_len` are over its UTF-8
    encoding, `scalar_len` is its Unicode scalar count, and a NULL column is recorded as the
    empty string with `stored_null = True`.

    Args:
        kind: one of `email_body`, `email_subject`, `attachment_text`.
        source_table: the table the text was read from (`email_message`, `email_attachment`).
        source_id: the primary key of that row — the second half of `artifact_id`.
        org_id: the tenant the row belongs to.
        text: the stored string, or None for a NULL column.
        stored_versions: the row's extraction/parse status columns, copied verbatim.

    Returns:
        The frozen `SnapshotRecord`.

    Raises:
        SnapshotError: if `kind` is not one of the three §5.1 artifact kinds.
    """
    if kind not in SNAPSHOT_KINDS:
        raise SnapshotError(
            f"unknown snapshot kind {kind!r}; expected one of {', '.join(SNAPSHOT_KINDS)}"
        )
    stored_null = text is None
    stored_text = "" if text is None else text
    encoded = stored_text.encode("utf-8")
    scalar_len = len(stored_text)
    return SnapshotRecord(
        artifact_id=f"{kind}:{source_id}",
        kind=cast(SnapshotKind, kind),
        source_table=source_table,
        source_id=source_id,
        org_id=org_id,
        sha256=sha256_bytes(encoded),
        byte_len=len(encoded),
        scalar_len=scalar_len,
        line_count=count_lines(stored_text),
        stored_null=stored_null,
        beyond_redact_scan_cap=scalar_len > REDACT_SCAN_CAP_SCALARS,
        stored_versions=dict(stored_versions),
        text=stored_text,
    )


def manifest_row(record: SnapshotRecord) -> dict[str, object]:
    """Project a record onto its manifest row — the record MINUS `text` (§5.1).

    Args:
        record: the snapshot record to project.

    Returns:
        A JSON-ready dict; UUIDs render as their canonical lowercase hyphenated strings.
    """
    return {
        "artifact_id": record.artifact_id,
        "kind": record.kind,
        "source_table": record.source_table,
        "source_id": str(record.source_id),
        "org_id": str(record.org_id),
        "sha256": record.sha256,
        "byte_len": record.byte_len,
        "scalar_len": record.scalar_len,
        "line_count": record.line_count,
        "stored_null": record.stored_null,
        "beyond_redact_scan_cap": record.beyond_redact_scan_cap,
        "stored_versions": dict(record.stored_versions),
    }


def text_digest_line(artifact_id: str, sha256: str, stored_null: bool) -> str:
    """Render one text-digest line of §5.1: `<artifact_id>\\t<sha256>\\t<true|false>\\n`.

    `stored_null` is rendered as JSON `true`/`false` (§16.3).
    """
    return f"{artifact_id}\t{sha256}\t{'true' if stored_null else 'false'}\n"


def _iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, object]]]:
    """Yield `(file line number, JSON object)` for each non-empty line of a UTF-8 JSONL file.

    Splits on `\\n` only: `str.splitlines()` would also break on U+2028/U+2029, which are legal
    inside a JSON string written with `ensure_ascii=False`.

    Raises:
        SnapshotError: the file is unreadable, a line is not JSON, or a line is not an object.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SnapshotError(f"cannot read snapshot manifest {path}: {error}") from error
    for number, line in enumerate(content.split("\n"), start=1):
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise SnapshotError(f"{path}:{number} is not valid JSON: {error}") from error
        if not isinstance(row, dict):
            raise SnapshotError(f"{path}:{number} is not a JSON object")
        yield number, row


def _digest_fields(row: Mapping[str, object], path: Path, number: int) -> tuple[str, str, bool]:
    """Extract the three text-digest fields of one manifest row, validating their types.

    Only `artifact_id`, `sha256` and `stored_null` are read: the digest is a strict function of
    those, and callers hand this module manifests that carry no other guaranteed field.

    Raises:
        SnapshotError: a field is missing or carries the wrong JSON type.
    """
    artifact_id = row.get("artifact_id")
    digest = row.get("sha256")
    stored_null = row.get("stored_null")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise SnapshotError(f"{path}:{number} has no usable 'artifact_id'")
    if not isinstance(digest, str) or not digest:
        raise SnapshotError(f"{path}:{number} has no usable 'sha256'")
    if not isinstance(stored_null, bool):
        raise SnapshotError(f"{path}:{number} has a non-boolean 'stored_null'")
    return artifact_id, digest, stored_null


def _read_digest_rows(path: Path) -> dict[str, tuple[str, bool]]:
    """Read a manifest into `artifact_id -> (sha256, stored_null)`.

    Raises:
        SnapshotError: on an unreadable/invalid manifest or a repeated `artifact_id`.
    """
    rows: dict[str, tuple[str, bool]] = {}
    for number, row in _iter_jsonl(path):
        artifact_id, digest, stored_null = _digest_fields(row, path, number)
        if artifact_id in rows:
            raise SnapshotError(f"{path}:{number} repeats artifact_id {artifact_id!r}")
        rows[artifact_id] = (digest, stored_null)
    return rows


def text_digest_of(manifest_path: Path) -> str:
    """Compute the SNAPSHOT_V1 `text_digest` of a written manifest (§5.1).

    The digest is `canonical_lines_digest` over `<artifact_id>\\t<sha256>\\t<true|false>\\n`, so
    it is insensitive to the manifest's line order and to every field outside those three.

    Args:
        manifest_path: path to a `snapshot.manifest.jsonl`.

    Returns:
        The lowercase hex sha256 digest.

    Raises:
        SnapshotError: the manifest is unreadable, malformed, or repeats an `artifact_id`.
    """
    rows = _read_digest_rows(manifest_path)
    return canonical_lines_digest(
        text_digest_line(artifact_id, digest, stored_null)
        for artifact_id, (digest, stored_null) in rows.items()
    )


def compare_manifests(a: Path, b: Path) -> DiffCounts:
    """Compare two snapshot manifests and report COUNTS ONLY (§5.1) — never ids or content.

    An artifact present only in `b` is `added`, present only in `a` is `removed`, present in both
    with a different `(sha256, stored_null)` pair is `changed`, and otherwise `unchanged`. The
    comparison keys on exactly the fields the `text_digest` depends on, so two manifests with the
    same digest always compare as fully unchanged.

    Args:
        a: the earlier manifest (the baseline).
        b: the later manifest (the replay).

    Returns:
        The `DiffCounts` for the two manifests.

    Raises:
        SnapshotError: either manifest is unreadable, malformed, or repeats an `artifact_id`.
    """
    left = _read_digest_rows(a)
    right = _read_digest_rows(b)
    shared = left.keys() & right.keys()
    changed = sum(1 for artifact_id in shared if left[artifact_id] != right[artifact_id])
    return DiffCounts(
        added=len(right.keys() - left.keys()),
        removed=len(left.keys() - right.keys()),
        changed=changed,
        unchanged=len(shared) - changed,
    )


def text_digest_for_records(records: Iterable[SnapshotRecord]) -> str:
    """Compute the §5.1 `text_digest` straight from records, without writing a manifest.

    The single in-memory definition of the rule: `emit_snapshot` uses it, and `corpus_identity`
    reports the same digest beside `corpus_digest` without duplicating it. The result is
    identical to `text_digest_of` over the manifest those records produce.
    """
    return canonical_lines_digest(
        text_digest_line(record.artifact_id, record.sha256, record.stored_null)
        for record in records
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    """Write JSONL bytes: sorted keys, non-ASCII raw, the non-LF line separators escaped.

    Bytes, never text mode: Windows would translate `\\n` to `\\r\\n` and break determinism.
    U+2028, U+2029 and U+0085 are legal raw inside a JSON string, but every one of them ends
    a line for `str.splitlines()`, so a reader would tear one record into two. They are
    emitted as their `\\u` escapes — the parsed value is unchanged.
    """
    payload = "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
        .replace("\u0085", "\\u0085")
        + "\n"
        for row in rows
    )
    path.write_bytes(payload.encode("utf-8"))


async def _read_email_artifacts(conn: AsyncSession, org_id: UUID) -> list[SnapshotRecord]:
    """Read both email artifact kinds of one org through the caller's R6 snapshot session."""
    result = await conn.execute(
        sql_text(
            "SELECT id, subject, body_text, parse_status FROM email_message "
            "WHERE org_id = :org ORDER BY id"
        ),
        {"org": str(org_id)},
    )
    records: list[SnapshotRecord] = []
    for row in result:
        versions: Mapping[str, str | None] = {"parse_status": row.parse_status}
        for kind, value in (("email_body", row.body_text), ("email_subject", row.subject)):
            records.append(
                snapshot_record(
                    kind=kind,
                    source_table="email_message",
                    source_id=row.id,
                    org_id=org_id,
                    text=value,
                    stored_versions=versions,
                )
            )
    return records


async def _read_attachment_artifacts(conn: AsyncSession, org_id: UUID) -> list[SnapshotRecord]:
    """Read the `attachment_text` artifacts of one org (§5.1: only where the text is non-null)."""
    result = await conn.execute(
        sql_text(
            "SELECT id, extracted_text, extractor_name, extractor_version, extraction_status "
            "FROM email_attachment WHERE org_id = :org AND extracted_text IS NOT NULL ORDER BY id"
        ),
        {"org": str(org_id)},
    )
    return [
        snapshot_record(
            kind="attachment_text",
            source_table="email_attachment",
            source_id=row.id,
            org_id=org_id,
            text=row.extracted_text,
            stored_versions={
                "extractor_name": row.extractor_name,
                "extractor_version": row.extractor_version,
                "extraction_status": row.extraction_status,
            },
        )
        for row in result
    ]


async def emit_snapshot(conn: AsyncSession, org_id: UUID, out_dir: Path) -> SnapshotSummary:
    """Emit the SNAPSHOT_V1 text snapshot of one org under `<out_dir>/snapshots/<text_digest>/`.

    Reads every §5.1 artifact of the org — one `email_body` and one `email_subject` per message,
    one `attachment_text` per attachment with a non-null `extracted_text` — through the caller's
    R6 snapshot session (read-only, repeatable read), and writes two byte-deterministic files:
    `snapshot.manifest.jsonl` (records minus `text`) and `snapshot.records.jsonl` (with `text`),
    both sorted by `artifact_id` bytewise. The emitter neither commits nor rolls back `conn`, and
    never widens the org filter: the session is tenant-scoped AND every statement filters `org_id`.

    Args:
        conn: an open R6 snapshot session (see `db.readonly_corpus_snapshot`).
        org_id: the tenant whose artifacts are snapshotted.
        out_dir: the RELEASE directory — the digest folder is created underneath it.

    Returns:
        The `SnapshotSummary`: version, `text_digest`, per-kind counts and the two paths.

    Raises:
        SnapshotError: the artifact set contains a repeated `artifact_id`, or a file cannot be
            written.
    """
    records = await _read_email_artifacts(conn, org_id)
    records.extend(await _read_attachment_artifacts(conn, org_id))
    records.sort(key=lambda record: record.artifact_id.encode("utf-8"))
    if len({record.artifact_id for record in records}) != len(records):
        raise SnapshotError(f"snapshot of org {org_id} contains a repeated artifact_id")

    digest = text_digest_for_records(records)
    directory = out_dir / "snapshots" / digest
    manifest_path = directory / MANIFEST_FILENAME
    records_path = directory / RECORDS_FILENAME
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _write_jsonl(manifest_path, [manifest_row(record) for record in records])
        _write_jsonl(
            records_path,
            [{**manifest_row(record), "text": record.text} for record in records],
        )
    except OSError as error:
        raise SnapshotError(f"cannot write the snapshot under {directory}: {error}") from error

    counts = {kind: 0 for kind in SNAPSHOT_KINDS}
    for record in records:
        counts[record.kind] += 1
    return SnapshotSummary(
        version=SNAPSHOT_VERSION,
        text_digest=digest,
        counts_by_kind=counts,
        manifest_path=manifest_path,
        records_path=records_path,
    )
