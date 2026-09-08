"""
Role: Seals the staged lock of contract §4.3 / §1.3 `lock` on a synthetic release — the lock is
      the manifest's sha256; stage 1 verifies every VISIBLE file the manifest names (and the
      runner hash on a frozen release) without opening a hidden path; a draft tolerates extra
      files and a frozen release refuses them; stage 2 verifies exactly the selected hidden
      split and never the other one.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.lock and .exceptions (imported inside each test);
      tests.tools.mem01_verify.synthetic_release (proven by test_oracle_helpers.py);
      tests.tools.mem01_verify.reference (the runner merkle).
Key invariants:
  - `ReleaseInfo.lock_sha256` is bare lowercase hex and `expect_lock` accepts the `sha256:`
    prefixed or bare form in either case (contract 16.2).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.tools.mem01_verify import reference, synthetic_release
from tests.tools.mem01_verify.conftest import InstrumentLoader

HIDDEN_QS = ["qs-0001", "qs-0002"]
HIDDEN_NF = ["nf-0001"]


def _runner_hash(runner_folder: Path) -> str:
    return reference.merkle_sha256_reference(runner_folder)


def _lock_errors(instrument: InstrumentLoader) -> tuple[type[BaseException], ...]:
    exceptions = instrument("exceptions")
    return (exceptions.ReleaseLockError, exceptions.RunnerHashMismatchError)


def test_compute_lock_is_the_sha256_of_the_manifest_bytes(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    lock = instrument("lock")
    built = synthetic_release.build_release(tmp_path, runner_sha256=_runner_hash(runner_folder))

    assert lock.compute_lock(built.manifest_path) == reference.sha256_hex(
        built.manifest_path.read_bytes()
    )


def test_verify_release_visible_returns_release_info_for_a_consistent_draft(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    lock = instrument("lock")
    built = synthetic_release.build_release(tmp_path, runner_sha256=_runner_hash(runner_folder))

    info = lock.verify_release_visible(built.path, expect_lock=None)

    assert info.state == "draft" and info.name == built.name
    assert Path(info.path) == built.path
    assert info.lock_sha256 == lock.compute_lock(built.manifest_path)
    assert dict(info.manifest) == built.manifest()
    assert Path(info.criteria_path) == built.path / "criteria.step1.v1.yaml"
    assert info.visible_files_verified >= 3 and info.hidden_files_verified == 0


def test_expect_lock_round_trips_and_a_mismatch_is_refused(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    lock = instrument("lock")
    built = synthetic_release.build_release(tmp_path, runner_sha256=_runner_hash(runner_folder))
    reported = lock.verify_release_visible(built.path, expect_lock=None).lock_sha256

    again = lock.verify_release_visible(built.path, expect_lock=reported)
    with pytest.raises(_lock_errors(instrument)):
        lock.verify_release_visible(
            built.path, expect_lock=synthetic_release.flip_one_hex_digit(reported)
        )

    assert again.lock_sha256 == reported


@pytest.mark.parametrize(
    "form", [lambda h: f"sha256:{h}", lambda h: h.upper(), lambda h: f"SHA256:{h.upper()}"]
)
def test_expect_lock_accepts_prefixed_and_uppercase_forms_and_reports_bare_lowercase_hex(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path, form: object
) -> None:
    lock = instrument("lock")
    built = synthetic_release.build_release(tmp_path, runner_sha256=_runner_hash(runner_folder))
    reported = lock.verify_release_visible(built.path, expect_lock=None).lock_sha256

    info = lock.verify_release_visible(built.path, expect_lock=form(reported))  # type: ignore[operator]

    assert re.fullmatch(r"[0-9a-f]{64}", reported) and info.lock_sha256 == reported
    with pytest.raises(_lock_errors(instrument)):
        lock.verify_release_visible(
            built.path,
            expect_lock=form(synthetic_release.flip_one_hex_digit(reported)),  # type: ignore[operator]
        )


@pytest.mark.parametrize(
    "relative", ["schemas/record_core.schema.json", "PROTOCOL.v1.md", "criteria.step1.v1.yaml"]
)
def test_a_tampered_named_file_is_refused(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path, relative: str
) -> None:
    lock = instrument("lock")
    built = synthetic_release.build_release(tmp_path, runner_sha256=_runner_hash(runner_folder))
    lock.verify_release_visible(built.path, expect_lock=None)  # positive control
    target = built.path / relative
    target.write_bytes(target.read_bytes() + b"\n# tampered\n")

    with pytest.raises(_lock_errors(instrument)):
        lock.verify_release_visible(built.path, expect_lock=None)


def test_draft_tolerates_extra_files_but_frozen_refuses_them(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    lock = instrument("lock")
    extra = {"census/extra.json": b"{}"}
    draft = synthetic_release.build_release(
        tmp_path / "draft", runner_sha256=_runner_hash(runner_folder), extra_unmanifested=extra
    )
    frozen_extra = synthetic_release.build_release(
        tmp_path / "frozen_extra",
        state="frozen",
        runner_sha256=_runner_hash(runner_folder),
        extra_unmanifested=extra,
    )
    frozen_clean = synthetic_release.build_release(
        tmp_path / "frozen_clean", state="frozen", runner_sha256=_runner_hash(runner_folder)
    )

    assert lock.verify_release_visible(draft.path, expect_lock=None).state == "draft"
    assert lock.verify_release_visible(frozen_clean.path, expect_lock=None).state == "frozen"
    with pytest.raises(_lock_errors(instrument)):
        lock.verify_release_visible(frozen_extra.path, expect_lock=None)


def test_frozen_release_compares_runner_sha256_and_draft_only_records_it(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    lock = instrument("lock")
    wrong = synthetic_release.flip_one_hex_digit(_runner_hash(runner_folder))
    frozen_wrong = synthetic_release.build_release(
        tmp_path / "frozen", state="frozen", runner_sha256=wrong
    )
    draft_wrong = synthetic_release.build_release(tmp_path / "draft", runner_sha256=wrong)

    assert lock.verify_release_visible(draft_wrong.path, expect_lock=None).state == "draft"
    with pytest.raises(_lock_errors(instrument)):
        lock.verify_release_visible(frozen_wrong.path, expect_lock=None)


def test_stage_one_never_opens_hidden_paths(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    lock = instrument("lock")
    built = synthetic_release.build_release(
        tmp_path,
        state="frozen",
        runner_sha256=_runner_hash(runner_folder),
        hidden_records={"QS": HIDDEN_QS},
    )
    manifest = built.manifest()
    manifest["files"]["hidden/test/QS/part0.jsonl"]["sha256"] = "f" * 64  # bogus, never read
    built.write_manifest(manifest)

    info = lock.verify_release_visible(built.path, expect_lock=None)

    assert info.hidden_files_verified == 0 and info.state == "frozen"


def test_stage_two_verifies_exactly_the_selected_split(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    lock = instrument("lock")
    built = synthetic_release.build_release(
        tmp_path,
        state="frozen",
        runner_sha256=_runner_hash(runner_folder),
        hidden_records={"QS": HIDDEN_QS, "NF": HIDDEN_NF},
    )
    info = lock.verify_release_visible(built.path, expect_lock=None)

    lock.verify_hidden_split(info, built.hidden_root, "test", ["QS", "NF"])  # positive control
    manifest = built.manifest()
    manifest["files"]["hidden/test/NF/part0.jsonl"]["sha256"] = "e" * 64
    built.write_manifest(manifest)
    tampered = lock.verify_release_visible(built.path, expect_lock=None)

    lock.verify_hidden_split(tampered, built.hidden_root, "test", ["QS"])  # NF never opened
    with pytest.raises(_lock_errors(instrument)):
        lock.verify_hidden_split(tampered, built.hidden_root, "test", ["QS", "NF"])


def test_missing_manifest_or_criteria_is_refused(
    instrument: InstrumentLoader, tmp_path: Path, runner_folder: Path
) -> None:
    lock = instrument("lock")
    no_manifest = synthetic_release.build_release(
        tmp_path / "a", runner_sha256=_runner_hash(runner_folder)
    )
    no_criteria = synthetic_release.build_release(
        tmp_path / "b", runner_sha256=_runner_hash(runner_folder)
    )
    no_manifest.manifest_path.unlink()
    (no_criteria.path / "criteria.step1.v1.yaml").unlink()

    with pytest.raises(_lock_errors(instrument)):
        lock.verify_release_visible(no_manifest.path, expect_lock=None)
    with pytest.raises(_lock_errors(instrument)):
        lock.verify_release_visible(no_criteria.path, expect_lock=None)


def test_release_lock_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.ReleaseLockError, exceptions.Mem01Error)
    assert issubclass(exceptions.RunnerHashMismatchError, exceptions.Mem01Error)
