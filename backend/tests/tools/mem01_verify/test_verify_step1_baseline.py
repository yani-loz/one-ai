"""
Role: Seals the completed-run invariants of contract §3.2/§3.3/§3.8/§13 on the session's two
      full tuning runs (before-census / after-census over the probe corpus) — exit 2, exactly
      one verdict line printed last and consistent with the block and the runner hash, a
      schema-valid block with run identity, the §13 comparison rule, no personal data on stdout
      or stderr, the protected result in the report dir, and the untouched corpus.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.baseline_pair (session-cached child runs), the instrument's result_block
      and verdict modules (imported inside tests), tests.tools.mem01_verify.reference.
Key invariants:
  - The expected verdict line is assembled by hand from the block and the oracle's runner
    merkle, never by the instrument's formatter.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import text

from tests.tools.mem01_verify import reference, seeding
from tests.tools.mem01_verify.conftest import (
    GATE_NAMES,
    SESSION_LOOP,
    BaselinePairFactory,
    InstrumentLoader,
    ProbeCorpusFactory,
)
from tests.tools.mem01_verify.reference import CliRun

GATE_FILE_KEYS = {"gate", "status", "reason", "criteria", "cases", "diagnostics"}
VERSION_KEYS = (
    "python",
    "sqlalchemy",
    "asyncpg",
    "postgres",
    "pgvector",
    "charset_normalizer",
    "html2text",
    "striprtf",
    "pdfplumber",
    "pypdf",
    "python_docx",
    "openpyxl",
    "tnefparse",
)


def _completed(run: CliRun) -> tuple[dict, str]:
    assert run.exit_code == 2, run.stderr[-2000:]
    block = reference.extract_machine_block(run.stdout)
    verdict = reference.last_nonempty_line(run.stdout)
    assert verdict.startswith("STEP1 ")
    assert sum(line.startswith("STEP1 ") for line in run.stdout.splitlines()) == 1
    assert block["aborted"] is False and block["partial"] is False
    assert block["status"] == "ERROR" and block["reason"]
    return block, verdict


@SESSION_LOOP
async def test_both_baseline_runs_complete_with_exit_2_and_a_single_final_verdict(
    baseline_pair: BaselinePairFactory,
) -> None:
    pair = await baseline_pair()

    for run in (pair.before, pair.after):
        block, _ = _completed(run)
        assert block["run_kind"] == "tuning" and block["split_evaluated"] == "optimization"
        assert run.stdout.splitlines()[0].startswith("MEM01 UTF-8 self-test: Здравей")
        assert run.stdout.index("MEM01_RESULT_V1_END") < run.stdout.rindex("STEP1 ")


@SESSION_LOOP
async def test_verdict_line_matches_the_block_the_manifest_lock_and_the_runner_merkle(
    instrument: InstrumentLoader,
    baseline_pair: BaselinePairFactory,
    runner_folder: Path,
) -> None:
    pair = await baseline_pair()
    verdict_module = instrument("verdict")
    block, verdict = _completed(pair.before)
    passed = sum(gate["status"] == "PASS" for gate in block["gates"].values())
    lock = hashlib.sha256(pair.manifest_before).hexdigest()
    runner = reference.merkle_sha256_reference(runner_folder)

    expected = (
        f"STEP1 TUNING: {passed}/17 PASS | provisional=4:FID,THR,IDENT,ATTR | "
        f"directional=- | run_id={block['run_id']} | lock=sha256:{lock} | "
        f"runner=sha256:{runner}"
    )

    assert verdict == expected
    assert block["release_lock_sha256"] == lock and block["runner_sha256"] == runner
    assert passed in (1, 2)
    fields = verdict_module.parse_verdict_line(verdict)
    assert fields.passed == passed and fields.run_kind == "tuning"


@SESSION_LOOP
async def test_block_is_schema_valid_and_carries_the_run_identity(
    instrument: InstrumentLoader,
    baseline_pair: BaselinePairFactory,
    probe_corpus: ProbeCorpusFactory,
    criteria_path: Path,
) -> None:
    corpus = await probe_corpus()
    pair = await baseline_pair()
    result_block = instrument("result_block")
    fixtures_digest = instrument("fixtures.digest").fixtures_digest()
    block, _ = _completed(pair.before)
    manifest = reference.read_json(pair.release.path / "dataset.manifest.json")

    result_block.validate_result_block(block, projection="protected")

    assert set(block["versions"]) == set(VERSION_KEYS)
    assert all(isinstance(v, str) and v for v in block["versions"].values())
    assert block["corpus"]["org_id"] == str(corpus.big.org_id)
    assert block["corpus"]["emails"] == corpus.big.email_count
    assert block["corpus"]["attachments"] == corpus.big.attachment_count
    assert block["corpus"]["database"] == corpus.database
    assert block["corpus"]["snapshot_transaction_id"]
    assert block["cleanup"]["probe_dropped"] is True and block["cleanup"]["kept"] is False
    assert block["baseline_label"] == "before-census"
    assert set(block["sets"]) == set(GATE_NAMES) and set(block["gates"]) == set(GATE_NAMES)
    assert block["opened_outside_closure"] == []
    assert block["cache_policy"] == "forbidden" and block["cache_hits"] == 0
    assert block["text_digest"] == manifest["corpus"]["text_digest"]
    assert block["corpus_digest"] == manifest["corpus"]["corpus_digest"]
    assert (
        block["criteria_sha256"] == hashlib.sha256(reference.read_bytes(criteria_path)).hexdigest()
    )
    assert block["fixtures_digest"] == fixtures_digest == manifest["fixtures_digest"]
    assert block["release_name"] == "step1-gold-v1" and block["release_state"] == "draft"


@SESSION_LOOP
async def test_baseline_pair_compares_equal_on_the_fields_of_section_13(
    baseline_pair: BaselinePairFactory,
) -> None:
    pair = await baseline_pair()
    before, _ = _completed(pair.before)
    after, _ = _completed(pair.after)

    assert before["corpus_digest"] == after["corpus_digest"]
    assert before["text_digest"] == after["text_digest"]
    assert {g: e["status"] for g, e in before["gates"].items()} == {
        g: e["status"] for g, e in after["gates"].items()
    }
    assert sorted(before["criteria"], key=lambda e: e["id"]) == sorted(
        after["criteria"], key=lambda e: e["id"]
    )
    assert before["code_hash"] == after["code_hash"]
    assert before["runner_sha256"] == after["runner_sha256"]
    assert before["release_lock_sha256"] != after["release_lock_sha256"]
    assert before["config_hash"] != after["config_hash"]
    assert after["baseline_label"] == "after-census"


@SESSION_LOOP
async def test_no_personal_data_on_stdout_or_stderr_of_the_full_runs(
    baseline_pair: BaselinePairFactory,
    probe_corpus: ProbeCorpusFactory,
) -> None:
    corpus = await probe_corpus()
    pair = await baseline_pair()
    markers = set(corpus.big.personal_markers) | set(corpus.small.personal_markers)

    for run in (pair.before, pair.after):
        output = run.stdout + run.stderr
        assert "@" not in output
        assert not any(marker in output for marker in markers)
        assert "Здравей" in run.stdout  # positive control: Cyrillic survives on stdout


@SESSION_LOOP
async def test_report_dir_holds_the_protected_result_gate_files_and_stdout(
    baseline_pair: BaselinePairFactory,
) -> None:
    pair = await baseline_pair()
    block, _ = _completed(pair.before)
    report_dir = pair.release.path / "reports" / block["run_id"]

    protected = reference.read_json(report_dir / "protected_result.json")

    assert protected == block  # tuning runs: the protected result IS the printed block
    gate_files = {p.name for p in reference.rglob_files(report_dir / "gates", "*.json")}
    assert gate_files == {f"{gate}.json" for gate in block["gates"]}
    printed = reference.read_text(report_dir / "stdout.txt").replace("\r\n", "\n")
    assert printed == pair.before.stdout.replace("\r\n", "\n")
    for gate, summary in block["gates"].items():  # §16.13 gates/<GATE>.json keys
        detail = reference.read_json(report_dir / "gates" / f"{gate}.json")
        assert set(detail) == GATE_FILE_KEYS, gate
        assert detail["gate"] == gate and detail["status"] == summary["status"]
        assert isinstance(detail["cases"], list) and isinstance(detail["criteria"], list)
        assert sorted(entry["id"] for entry in detail["criteria"]) == sorted(summary["criteria"])


@SESSION_LOOP
async def test_corpus_row_counts_are_unchanged_by_the_runs(
    instrument: InstrumentLoader,
    baseline_pair: BaselinePairFactory,
    probe_corpus: ProbeCorpusFactory,
) -> None:
    corpus = await probe_corpus()
    await baseline_pair()
    sessions = instrument("db").probe_session_factories(corpus.database)

    async with sessions.global_() as session:
        await seeding.assert_probe_connection(session)
        counts = {}
        for org in (corpus.big, corpus.small):
            counts[org.org_id] = (
                (
                    await session.execute(
                        text("SELECT count(*) FROM email_message WHERE org_id = :o"),
                        {"o": str(org.org_id)},
                    )
                ).scalar_one(),
                (
                    await session.execute(
                        text("SELECT count(*) FROM email_attachment WHERE org_id = :o"),
                        {"o": str(org.org_id)},
                    )
                ).scalar_one(),
            )

    assert counts[corpus.big.org_id] == (corpus.big.email_count, corpus.big.attachment_count)
    assert counts[corpus.small.org_id] == (corpus.small.email_count, corpus.small.attachment_count)
