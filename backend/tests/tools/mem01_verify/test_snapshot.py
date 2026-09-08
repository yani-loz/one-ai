"""
Role: Seals SNAPSHOT_V1 (contract §5.1, §1.4) — the pure record (sha256 of UTF-8, byte vs scalar
      length, line_count rule, stored_null, the 2,000,000-scalar redact-cap boundary, artifact id
      form), `text_digest_of`, `compare_manifests`, and the emitter on the six-email probe org
      (every artifact of the org through the R6 snapshot, sorted manifest, determinism).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.snapshot and .db (imported inside each test);
      tests.tools.mem01_verify.reference (canonical lines digest, readers).
Key invariants:
  - `<stored_null>` in the text-digest line is rendered as JSON `true`/`false` (an oracle
    assumption flagged in the report; contract §5.1 does not spell the rendering).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import SESSION_LOOP, InstrumentLoader, ProbeCorpusFactory

ORG = UUID("00000000-0000-4000-8000-00000000000a")
VERSIONS = {"parse_status": "parsed"}


def _record(
    snapshot: object, text: str | None, kind: str = "email_body", source_id: UUID | None = None
) -> object:
    return snapshot.snapshot_record(  # type: ignore[attr-defined]
        kind=kind,
        source_table="email_message",
        source_id=source_id or uuid4(),
        org_id=ORG,
        text=text,
        stored_versions=VERSIONS,
    )


def test_version_constant(instrument: InstrumentLoader) -> None:
    assert instrument("snapshot").SNAPSHOT_VERSION == "SNAPSHOT_V1"


def test_snapshot_record_hashes_utf8_and_records_both_lengths(instrument: InstrumentLoader) -> None:
    snapshot = instrument("snapshot")
    source_id = uuid4()
    text = "Здравей\nсвят"

    record = _record(snapshot, text, source_id=source_id)

    assert record.sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert (record.byte_len, record.scalar_len, record.line_count) == (23, 12, 2)
    assert record.artifact_id == f"email_body:{source_id}"
    assert (record.kind, record.source_table, record.source_id, record.org_id) == (
        "email_body",
        "email_message",
        source_id,
        ORG,
    )
    assert record.stored_null is False and record.beyond_redact_scan_cap is False
    assert record.text == text and dict(record.stored_versions) == VERSIONS


def test_snapshot_record_null_becomes_empty_string_with_stored_null(
    instrument: InstrumentLoader,
) -> None:
    snapshot = instrument("snapshot")

    null_record = _record(snapshot, None)
    empty_record = _record(snapshot, "")

    assert null_record.text == "" and null_record.stored_null is True
    assert null_record.sha256 == hashlib.sha256(b"").hexdigest()
    assert (null_record.byte_len, null_record.scalar_len, null_record.line_count) == (0, 0, 0)
    assert empty_record.stored_null is False and empty_record.line_count == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a", 1),
        ("a\n", 1),
        ("a\nb", 2),
        ("a\nb\n", 2),
        ("\n", 1),
        ("\n\n", 2),
        ("a\r\nb", 2),
        ("a\rb", 1),  # §16.10: only LF counts
        ("a\r\n", 1),
    ],
)
def test_snapshot_record_line_count_rule(
    instrument: InstrumentLoader, text: str, expected: int
) -> None:
    assert _record(instrument("snapshot"), text).line_count == expected


def test_snapshot_record_redact_cap_is_scalar_based_at_two_million(
    instrument: InstrumentLoader,
) -> None:
    snapshot = instrument("snapshot")

    at_cap = _record(snapshot, "x" * 2_000_000)
    over_cap = _record(snapshot, "x" * 2_000_001)
    many_bytes_few_scalars = _record(snapshot, "Ж" * 1_500_000)  # 3,000,000 bytes
    cyrillic_over_cap = _record(snapshot, "Ж" * 2_000_001)

    assert at_cap.beyond_redact_scan_cap is False
    assert over_cap.beyond_redact_scan_cap is True
    assert many_bytes_few_scalars.beyond_redact_scan_cap is False
    assert many_bytes_few_scalars.byte_len == 3_000_000
    assert cyrillic_over_cap.beyond_redact_scan_cap is True


@pytest.mark.parametrize("kind", ["email_subject", "attachment_text"])
def test_snapshot_record_artifact_id_uses_kind_and_source_id(
    instrument: InstrumentLoader, kind: str
) -> None:
    source_id = uuid4()

    record = _record(instrument("snapshot"), "t", kind=kind, source_id=source_id)

    assert record.artifact_id == f"{kind}:{source_id}"


def test_snapshot_record_is_frozen(instrument: InstrumentLoader) -> None:
    record = _record(instrument("snapshot"), "t")

    with pytest.raises(AttributeError):
        record.text = "u"  # type: ignore[misc]


def _manifest_line(artifact_id: str, digest: str, stored_null: bool) -> str:
    return json.dumps(
        {
            "artifact_id": artifact_id,
            "kind": artifact_id.split(":")[0],
            "sha256": digest,
            "stored_null": stored_null,
            "byte_len": 1,
            "scalar_len": 1,
            "line_count": 1,
            "beyond_redact_scan_cap": False,
            "stored_versions": {},
        },
        sort_keys=True,
    )


def _write_manifest(path: Path, rows: list[tuple[str, str, bool]]) -> Path:
    rows = sorted(rows, key=lambda row: row[0].encode("utf-8"))
    path.write_text("".join(_manifest_line(*row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_text_digest_of_matches_canonical_lines_and_is_sensitive(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    snapshot = instrument("snapshot")
    h1, h2 = hashlib.sha256(b"1").hexdigest(), hashlib.sha256(b"2").hexdigest()
    rows = [("email_body:b", h1, False), ("email_body:a", h2, True), ("email_subject:a", h1, False)]
    manifest = _write_manifest(tmp_path / "snapshot.manifest.jsonl", rows)
    expected = reference.canonical_lines_digest_reference(
        f"{artifact}\t{digest}\t{'true' if null else 'false'}\n" for artifact, digest, null in rows
    )

    digest = snapshot.text_digest_of(manifest)

    assert digest == expected
    changed = _write_manifest(
        tmp_path / "changed.jsonl", [rows[0], (rows[1][0], h1, True), rows[2]]
    )
    added = _write_manifest(tmp_path / "added.jsonl", [*rows, ("attachment_text:x", h2, False)])
    assert snapshot.text_digest_of(changed) != digest
    assert snapshot.text_digest_of(added) != digest


def test_compare_manifests_reports_counts_only(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    snapshot = instrument("snapshot")
    h1, h2 = hashlib.sha256(b"1").hexdigest(), hashlib.sha256(b"2").hexdigest()
    common = [(f"email_body:c{i}", h1, False) for i in range(3)]
    left = _write_manifest(
        tmp_path / "a.jsonl", [*common, ("email_body:x", h1, False), ("email_body:gone", h1, False)]
    )
    right = _write_manifest(
        tmp_path / "b.jsonl",
        [
            *common,
            ("email_body:x", h2, False),
            ("email_body:new1", h1, False),
            ("email_body:new2", h2, False),
        ],
    )

    diff = snapshot.compare_manifests(left, right)

    assert (diff.added, diff.removed, diff.changed, diff.unchanged) == (2, 1, 1, 3)


@SESSION_LOOP
async def test_emit_snapshot_covers_every_artifact_of_the_org_through_the_snapshot_plane(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, tmp_path: Path
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    snapshot = instrument("snapshot")
    small = corpus.small

    async with db.readonly_corpus_snapshot(small.org_id, database=corpus.database) as conn:
        summary = await snapshot.emit_snapshot(conn, small.org_id, tmp_path)

    assert summary.version == "SNAPSHOT_V1"
    assert dict(summary.counts_by_kind) == {
        "email_body": 6,
        "email_subject": 6,
        "attachment_text": 1,
    }
    assert summary.manifest_path.name == "snapshot.manifest.jsonl"
    manifest = reference.read_jsonl(summary.manifest_path)
    assert len(manifest) == small.text_artifact_count and all("text" not in row for row in manifest)
    ids = [row["artifact_id"] for row in manifest]
    assert ids == sorted(ids, key=lambda value: value.encode("utf-8"))
    null_body = next(
        row
        for row in manifest
        if row["artifact_id"] == f"email_body:{small.null_body_email_ids[0]}"
    )
    assert null_body["stored_null"] is True
    assert null_body["sha256"] == hashlib.sha256(b"").hexdigest()
    records = reference.read_jsonl(summary.records_path)
    assert len(records) == small.text_artifact_count and all("text" in row for row in records)
    first_body = next(
        row for row in records if row["artifact_id"] == f"email_body:{small.email_ids[0]}"
    )
    assert first_body["text"].startswith("OracleBodyText one")
    assert summary.text_digest == snapshot.text_digest_of(summary.manifest_path)
    assert all(
        UUID(row["artifact_id"].split(":")[1]) in set(small.email_ids) | set(small.attachment_ids)
        for row in manifest
    )


@SESSION_LOOP
async def test_emit_snapshot_is_deterministic_across_two_replays(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, tmp_path: Path
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    snapshot = instrument("snapshot")
    small = corpus.small

    async with db.readonly_corpus_snapshot(small.org_id, database=corpus.database) as conn:
        first = await snapshot.emit_snapshot(conn, small.org_id, tmp_path / "one")
    async with db.readonly_corpus_snapshot(small.org_id, database=corpus.database) as conn:
        second = await snapshot.emit_snapshot(conn, small.org_id, tmp_path / "two")

    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.text_digest == second.text_digest
    diff = snapshot.compare_manifests(first.manifest_path, second.manifest_path)
    assert (diff.added, diff.removed, diff.changed, diff.unchanged) == (
        0,
        0,
        0,
        small.text_artifact_count,
    )


@SESSION_LOOP
async def test_emit_snapshot_records_every_seeded_text_verbatim_with_exact_lengths_and_hashes(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, tmp_path: Path
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    snapshot = instrument("snapshot")
    small = corpus.small
    expected = dict(small.texts_by_artifact)
    texts = [text for text in expected.values() if text is not None]
    assert any("\r\n" in text and "\u00a0" in text for text in texts)  # a normalizer would fail
    assert any("\u2026" in text and "\u201e" in text for text in texts)

    async with db.readonly_corpus_snapshot(small.org_id, database=corpus.database) as conn:
        summary = await snapshot.emit_snapshot(conn, small.org_id, tmp_path)
    records = {row["artifact_id"]: row for row in reference.read_jsonl(summary.records_path)}

    assert set(records) == set(expected)
    for artifact_id, text in expected.items():
        record = records[artifact_id]
        stored = "" if text is None else text
        assert record["text"] == stored, artifact_id
        assert record["stored_null"] is (text is None), artifact_id
        assert record["sha256"] == hashlib.sha256(stored.encode("utf-8")).hexdigest()
        assert record["byte_len"] == len(stored.encode("utf-8"))
        assert record["scalar_len"] == len(stored)
        tail = 1 if stored and not stored.endswith("\n") else 0
        assert record["line_count"] == stored.count("\n") + tail
