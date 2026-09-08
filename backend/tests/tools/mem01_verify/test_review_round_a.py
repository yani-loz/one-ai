"""
Role: Seals the group-A review findings of contract §16.16 (p)–(r) / registry rows A2-b, A13,
      A14 on pure surfaces — (p) an aborted hidden block without `split_evaluated` is refused as
      a stdout projection and accepted as a protected result, while a key-less aborted TUNING
      block stays accepted under both (the §16.14 aborted shape); (q) on hidden run kinds every
      H-split gate projects to `{status}` even without an entry on the evaluated split, and a
      block that keeps such a gate's reason is refused as stdout; (r) a non-bytecode file under
      a `__pycache__` directory follows the ORDINARY closure test — inside the editable scope
      it enters the closure's `code_files` and is not an offender, outside the closure it is an
      offender — while `.pyc`/`.pyo` bytecode is exempt everywhere and never hashed.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.result_block, .exceptions, .statuses, .run_identity, .criteria,
      .corpus_identity (imported inside each test); tests.tools.mem01_verify.result_block_samples
      (whitelist, hex, the sealed tuning sample), conftest (criteria_path, GATE_NAMES).
Key invariants:
  - Every block is built by hand from §3.3/§3.4/§16.14; the stdout shape of a hidden block is
    authored here (whitelist keys, H gates reduced, hidden-split entries collapsed), never taken
    from the instrument.
  - The synthetic repository lives in tmp_path; the real repository is never observed or hashed.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tests.tools.mem01_verify.conftest import GATE_NAMES, InstrumentLoader
from tests.tools.mem01_verify.result_block_samples import (
    HEX,
    STDOUT_WHITELIST,
    VERSION_KEYS,
    aborted_block,
    completed_block,
)

H_SPLIT_GATES = ("QS", "NF", "LANG", "RET")
HIDDEN_KINDS = [("checkpoint", "test"), ("validation", "validation")]
ABORT_KEYS = {"reason", "aborted_at_step"}
LANG_REASON = "no hidden scorability in stage A"


def _pending_validation_entry(gate: str) -> dict:
    return {
        "id": f"{gate.lower()}.validation",
        "gate": gate,
        "set": gate,
        "split": "validation",
        "evidence_basis": "H-validation",
        "acceptance_state": "provisional",
        "kind": "ratio",
        "numerator": None,
        "denominator": None,
        "denominator_def": "oracle denominator",
        "operator": ">=",
        "threshold": 0.9,
        "minimum": 100,
        "status": "pending",
        "reason": "validation not run",
        "expected": 0,
        "evaluated": 0,
        "skipped": 0,
        "errors": 0,
        "diagnostic_only": False,
        "directional": False,
        "versions": {},
    }


def _with_roster_only_lang(block: dict) -> dict:
    """LANG `incomplete` with a reason and ONLY a pending `.validation` entry (no evaluated-split
    entry), CH untouched as the non-H control."""
    block["gates"]["LANG"] = {
        "status": "incomplete",
        "reason": LANG_REASON,
        "criteria": ["lang.validation"],
    }
    block["criteria"] = [e for e in block["criteria"] if e["gate"] != "LANG"]
    block["criteria"].append(_pending_validation_entry("LANG"))
    return block


def _aborted_hidden(run_kind: str, split: str) -> dict:
    """A raw aborted hidden block (step 9) that had evaluated QS on the hidden split."""
    block = completed_block(run_kind)
    block.update(
        {
            "status": "ERROR",
            "aborted": True,
            "reason": "opened outside closure",
            "aborted_at_step": 9,
            "split_evaluated": split,
            "opened_outside_closure": ["backend/scripts/foo.py"],
        }
    )
    for key in ("hidden_budget", "hidden_budget_by_split", "hidden_budget_limit"):
        block.pop(key, None)
    block.pop("hidden_invocations_under_lock", None)
    block.pop("validation", None)
    block.pop("baseline_label", None)
    for gate in GATE_NAMES:
        if gate not in ("QS", "SNAP"):
            block["gates"][gate] = {"status": "skipped", "reason": "aborted"}
    block["criteria"] = [e for e in block["criteria"] if e["gate"] in ("QS", "SNAP")]
    for entry in block["criteria"]:
        if entry["gate"] == "QS":
            entry["split"] = split
            entry["evidence_basis"] = f"H-{split}"
    block["gates"]["QS"] = {
        "status": "FAIL",
        "reason": "content loss",
        "criteria": [e["id"] for e in block["criteria"] if e["gate"] == "QS"],
    }
    block["versions"] = {key: "1.0" for key in VERSION_KEYS}
    return block


def _stdout_shape(block: dict) -> dict:
    """The §3.4 stdout shape of a hidden block, written by hand (never the instrument)."""
    split = block["split_evaluated"]
    shaped = {
        key: copy.deepcopy(value)
        for key, value in block.items()
        if key in STDOUT_WHITELIST | ABORT_KEYS
    }
    shaped["gates"] = {
        gate: {"status": entry["status"]} if gate in H_SPLIT_GATES else copy.deepcopy(entry)
        for gate, entry in block["gates"].items()
    }
    collapsed: dict[str, str] = {}
    kept = []
    for entry in block["criteria"]:
        if entry["split"] != split:
            kept.append(copy.deepcopy(entry))
            continue
        worst = "FAIL" if "FAIL" in (collapsed.get(entry["set"]), entry["status"]) else "PASS"
        collapsed[entry["set"]] = worst
    shaped["criteria"] = kept + [
        {"id": set_name, "split": split, "status": status}
        for set_name, status in sorted(collapsed.items())
    ]
    return shaped


def _without(block: dict, key: str) -> dict:
    stripped = copy.deepcopy(block)
    stripped.pop(key)
    return stripped


# ── (p) split_evaluated on aborted blocks ─────────────────────────────────────────────────


@pytest.mark.parametrize(("run_kind", "split"), HIDDEN_KINDS)
def test_aborted_hidden_stdout_projection_requires_split_evaluated(
    instrument: InstrumentLoader, run_kind: str, split: str
) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    shaped = _stdout_shape(_aborted_hidden(run_kind, split))
    # the same shape with no collapsed hidden entries: the missing key is then the ONLY defect
    minimal = copy.deepcopy(shaped)
    minimal["criteria"] = [e for e in shaped["criteria"] if set(e) != {"id", "split", "status"}]

    result_block.validate_result_block(shaped, projection="stdout")  # positive controls
    result_block.validate_result_block(minimal, projection="stdout")
    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(_without(shaped, "split_evaluated"), projection="stdout")
    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(
            _without(minimal, "split_evaluated"), projection="stdout"
        )


@pytest.mark.parametrize(("run_kind", "split"), HIDDEN_KINDS)
def test_raw_aborted_hidden_block_is_accepted_as_protected_with_or_without_split_evaluated(
    instrument: InstrumentLoader, run_kind: str, split: str
) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    raw = _aborted_hidden(run_kind, split)

    result_block.validate_result_block(raw, projection="protected")
    result_block.validate_result_block(_without(raw, "split_evaluated"), projection="protected")
    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(_without(raw, "split_evaluated"), projection="stdout")


def test_key_less_aborted_tuning_block_stays_accepted_under_both_projections(
    instrument: InstrumentLoader,
) -> None:
    result_block = instrument("result_block")
    tuning = aborted_block()  # the sealed §16.14 sample: no split_evaluated key
    assert "split_evaluated" not in tuning and tuning["run_kind"] == "tuning"

    result_block.validate_result_block(tuning, projection="protected")
    result_block.validate_result_block(tuning, projection="stdout")
    with_key = {**copy.deepcopy(tuning), "split_evaluated": "optimization"}
    result_block.validate_result_block(with_key, projection="stdout")


# ── (q) roster-based hidden gates ─────────────────────────────────────────────────────────


def test_h_split_roster_on_statuses_is_the_four_hidden_gates(instrument: InstrumentLoader) -> None:
    assert instrument("statuses").H_SPLIT_GATES == H_SPLIT_GATES


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: completed_block("checkpoint"), id="completed_checkpoint"),
        pytest.param(lambda: completed_block("validation"), id="completed_validation"),
        pytest.param(lambda: _aborted_hidden("checkpoint", "test"), id="aborted_checkpoint"),
        pytest.param(lambda: _aborted_hidden("validation", "validation"), id="aborted_validation"),
    ],
)
def test_every_h_split_gate_projects_to_status_only_even_without_an_evaluated_split_entry(
    instrument: InstrumentLoader, build: object
) -> None:
    result_block = instrument("result_block")
    exceptions = instrument("exceptions")
    block = _with_roster_only_lang(build())  # type: ignore[operator]

    projected = result_block.project_for_stdout(copy.deepcopy(block))

    assert projected["gates"]["LANG"] == {"status": "incomplete"}
    assert all(set(projected["gates"][gate]) == {"status"} for gate in H_SPLIT_GATES)
    assert projected["gates"]["CH"] == block["gates"]["CH"]  # non-H gates pass through unchanged
    assert LANG_REASON not in str(projected)
    pending = [e for e in projected["criteria"] if e["id"] == "lang.validation"]
    assert pending and pending[0]["status"] == "pending"  # a pending entry keeps its form
    leaked = _stdout_shape(block)
    leaked["gates"]["LANG"] = copy.deepcopy(block["gates"]["LANG"])  # LANG keeps its reason
    with pytest.raises(exceptions.ResultBlockError):
        result_block.validate_result_block(leaked, projection="stdout")


# ── (r) the __pycache__ rule for the observer and the closure ─────────────────────────────

EDITABLE_FILE = "backend/app/a.py"
IN_SCOPE_JSON = "backend/app/__pycache__/oracle.json"
BYTECODE_PYC = "backend/app/__pycache__/x.cpython-312.pyc"
BYTECODE_PYO = "backend/app/__pycache__/y.pyo"
SCRIPTS_FILE = "backend/scripts/foo.py"
OUTSIDE_JSON = "backend/scripts/__pycache__/oracle.json"
DOCS_JSON = "docs/__pycache__/hidden_split.json"
OUTSIDE_PYC = "backend/scripts/__pycache__/foo.cpython-312.pyc"
REPO_FILES = {
    EDITABLE_FILE: b"print('a')\n",
    IN_SCOPE_JSON: b'{"kept": "a non-bytecode file inside the editable scope"}\n',
    BYTECODE_PYC: b"\x00bytecode of x",
    BYTECODE_PYO: b"\x00optimised bytecode of y",
    SCRIPTS_FILE: b"print('outside the closure')\n",
    OUTSIDE_JSON: b'{"smuggled": "outside the closure under bytecode"}\n',
    DOCS_JSON: b'{"smuggled": "hidden split ids"}\n',
    OUTSIDE_PYC: b"\x00bytecode of foo",
    "backend/pyproject.toml": b"[project]\nname='x'\n",
    "backend/uv.lock": b"version = 1\n",
    "backend/.python-version": b"3.12\n",
    "backend/alembic.ini": b"[alembic]\n",
    "backend/.env": b"POSTGRES_HOST=localhost\n",
}


def _synthetic_repo(root: Path) -> Path:
    for relative, payload in REPO_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def _closure(instrument: InstrumentLoader, repo: Path, criteria_path: Path) -> object:
    corpus = instrument("corpus_identity").CorpusIdentity(
        version="CORPUS_DIGEST_V1",
        corpus_digest=HEX,
        text_digest="1" * 64,
        roster_counts={"email_message": 6},
        taken_at=datetime.now(UTC),
        snapshot_transaction_id="00000A1B-1",
        database="mem01_probe_oracle",
        host="localhost",
        port=5432,
        org_id=uuid4(),
    )
    return instrument("run_identity").build_closure(
        repo,
        instrument("criteria").load_criteria(criteria_path),
        corpus=corpus,
        migrations_digest=HEX,
        fixtures_digest=HEX,
        cli_options={"gates": None},
    )


def _posix(items: object) -> list[str]:
    return [str(item).replace("\\", "/") for item in items]  # type: ignore[union-attr]


def test_closure_code_files_include_a_non_bytecode_file_under_pycache_and_exclude_bytecode(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    repo = _synthetic_repo(tmp_path / "repo")

    closure = _closure(instrument, repo, criteria_path)

    assert IN_SCOPE_JSON in closure.code_files and EDITABLE_FILE in closure.code_files  # type: ignore[attr-defined]
    assert BYTECODE_PYC not in closure.code_files and BYTECODE_PYO not in closure.code_files  # type: ignore[attr-defined]
    assert OUTSIDE_JSON not in closure.code_files  # type: ignore[attr-defined]


def test_observer_reports_non_bytecode_pycache_files_only_outside_the_closure(
    instrument: InstrumentLoader, criteria_path: Path, tmp_path: Path
) -> None:
    run_identity = instrument("run_identity")
    repo = _synthetic_repo(tmp_path / "repo")
    closure = _closure(instrument, repo, criteria_path)
    read = (
        IN_SCOPE_JSON,
        BYTECODE_PYC,
        BYTECODE_PYO,
        OUTSIDE_JSON,
        DOCS_JSON,
        OUTSIDE_PYC,
        SCRIPTS_FILE,
        EDITABLE_FILE,
    )

    with run_identity.InputObserver(repo) as observer:
        for relative in read:
            (repo / relative).read_bytes()
    offenders = _posix(observer.check_within(closure))

    assert any(item.endswith(OUTSIDE_JSON) for item in offenders)  # outside: an ordinary offender
    assert any(item.endswith(DOCS_JSON) for item in offenders)
    assert any(item.endswith(SCRIPTS_FILE) for item in offenders)  # existing behaviour
    assert not any(item.endswith(IN_SCOPE_JSON) for item in offenders)  # hashed, inside
    for bytecode in (BYTECODE_PYC, BYTECODE_PYO, OUTSIDE_PYC):
        assert not any(item.endswith(bytecode) for item in offenders), bytecode
    assert not any(item.endswith(EDITABLE_FILE) for item in offenders)
