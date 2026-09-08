"""
Role: Seals fix-registry row A4 at module level — the counters a checkpoint/validation run
      displays are the cumulative ledger counters of ALL FOUR H splits, each keyed by its own
      split digest, including splits this run never reserved: prior spend on LANG's digest
      shows as `LANG: 3` after a QS-only reservation, `total` is the max over the four, `limit`
      is the effective limit of the split attaining the max, the verdict bracket renders
      every split's counter, and `runner_steps.hidden_display_digests(manifest)` yields the
      split digest of ALL FOUR H splits (absent splits included) for the runner's display.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.hidden_budget, .audit_file, .verdict, .runner_steps (imported
      inside each test);
      tests.tools.mem01_verify.reference (run-id and stamp forms).
Key invariants:
  - Every digest and hash is minted by hand; the ledger starts empty in tmp_path.
  - The reservation itself still names only the scorable split (the event's `split_digests`).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle display lock").hexdigest()
QS = hashlib.sha256(b"display split QS").hexdigest()
NF = hashlib.sha256(b"display split NF").hexdigest()
LANG = hashlib.sha256(b"display split LANG").hexdigest()
RET = hashlib.sha256(b"display split RET").hexdigest()
ALL_FOUR = {"QS": QS, "NF": NF, "LANG": LANG, "RET": RET}
RUNNER = "0" * 64


def _pair(index: int) -> tuple[str, str]:
    return (
        hashlib.sha256(f"display code {index}".encode()).hexdigest(),
        hashlib.sha256(f"display config {index}".encode()).hexdigest(),
    )


def _reserve(budget: object, index: int, splits: dict[str, str]) -> object:
    code, config = _pair(index)
    return budget.reserve(  # type: ignore[attr-defined]
        lock_sha256=LOCK,
        split_digests=splits,
        code_hash=code,
        config_hash=config,
        run_id=reference.oracle_run_id(index),
    )


def _budget_with_lang_spend(instrument: InstrumentLoader, tmp_path: Path) -> tuple[object, Path]:
    """An empty ledger with three distinct pairs already charged to LANG's digest."""
    ledger = tmp_path / "hidden_budget.jsonl"
    ledger.write_bytes(b"")
    budget = instrument("hidden_budget").HiddenBudget(ledger)
    for index in (1, 2, 3):
        _reserve(budget, index, {"LANG": LANG})
    return budget, ledger


def _raise_limit(instrument: InstrumentLoader, ledger: Path, split_digest: str, limit: int) -> None:
    instrument("audit_file").append_event(
        ledger,
        {
            "type": "budget_raise",
            "split_digest": split_digest,
            "new_limit": limit,
            "principal": "founder",
            "reason": "oracle raise",
            "at": reference.EVENT_AT,
        },
    )


def test_counters_over_all_four_digests_show_prior_spend_of_a_split_this_run_never_reserved(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    budget, ledger = _budget_with_lang_spend(instrument, tmp_path)

    reservation = _reserve(budget, 4, {"QS": QS})
    counters = budget.counters(ALL_FOUR)  # type: ignore[attr-defined]

    assert counters.by_split == {"QS": 1, "NF": 0, "LANG": 3, "RET": 0}
    assert counters.total == 3 and counters.limit == 20
    assert reservation.counters_before.by_split["QS"] == 0
    events = [e for e in reference.read_jsonl(ledger) if e["type"] == "hidden_reservation"]
    assert events[-1]["split_digests"] == {"QS": QS}  # the reservation covers QS only
    assert budget.counters({"QS": QS}).by_split["QS"] == 1  # type: ignore[attr-defined]


def test_limit_is_the_effective_limit_of_the_split_attaining_the_max(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    budget, ledger = _budget_with_lang_spend(instrument, tmp_path)
    _reserve(budget, 4, {"QS": QS})

    _raise_limit(instrument, ledger, LANG, 25)
    lang_raised = budget.counters(ALL_FOUR)  # type: ignore[attr-defined]
    _raise_limit(instrument, ledger, QS, 30)
    qs_raised_too = budget.counters(ALL_FOUR)  # type: ignore[attr-defined]
    for index in (5, 6, 7):
        _reserve(budget, index, {"QS": QS})
    qs_leads = budget.counters(ALL_FOUR)  # type: ignore[attr-defined]

    assert lang_raised.total == 3 and lang_raised.limit == 25
    assert qs_raised_too.total == 3 and qs_raised_too.limit == 25  # QS at 1 does not attain 3
    assert qs_leads.by_split == {"QS": 4, "NF": 0, "LANG": 3, "RET": 0}
    assert qs_leads.total == 4 and qs_leads.limit == 30


def test_verdict_bracket_renders_every_splits_cumulative_counter_including_zero(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")
    fields = verdict.VerdictFields(
        run_kind="checkpoint",
        passed=1,
        provisional=("FID", "THR", "IDENT", "ATTR"),
        directional=(),
        run_id=reference.oracle_run_id(4),
        lock_sha256=LOCK,
        runner_sha256=RUNNER,
        hidden=verdict.HiddenCounters(
            total=3,
            limit=20,
            by_split={"QS": 1, "NF": 0, "LANG": 3, "RET": 0},
            invocations_under_lock=1,
        ),
    )

    line = verdict.format_verdict_line(fields)

    assert " | hidden 3/20 (QS 1 · NF 0 · LANG 3 · RET 0) | " in line
    assert verdict.parse_verdict_line(line).hidden.by_split == {
        "QS": 1,
        "NF": 0,
        "LANG": 3,
        "RET": 0,
    }


def _hidden_entry(digest_seed: str, records: int) -> dict:
    return {
        "sha256": hashlib.sha256(digest_seed.encode()).hexdigest(),
        "bytes": 10 * records,
        "records": records,
        "visibility": "hidden",
    }


def test_display_digests_cover_all_four_h_splits_even_when_absent_from_the_manifest(
    instrument: InstrumentLoader,
) -> None:
    hidden_budget = instrument("hidden_budget")
    runner_steps = instrument("runner_steps")
    manifest = {
        "release_name": "step1-gold-v1",
        "files": {
            "hidden/test/QS/part0.jsonl": _hidden_entry("qs part0", 2),
            "hidden/test/NF/part0.jsonl": _hidden_entry("nf part0", 3),
            "data/optimization/QS/part0.jsonl": {
                "sha256": "d" * 64,
                "bytes": 40,
                "records": 4,
                "visibility": "visible",
            },
        },
    }

    digests = runner_steps.hidden_display_digests(manifest)

    assert set(digests) == {"QS", "NF", "LANG", "RET"}  # LANG and RET have no hidden files
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests.values())
    assert digests == {
        name: hidden_budget.split_digest(manifest, name) for name in ("QS", "NF", "LANG", "RET")
    }
    assert digests["QS"] != digests["NF"]
