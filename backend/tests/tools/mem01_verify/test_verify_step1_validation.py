"""
Role: Seals the Stage A frozen-release behaviour of `--validation` through the CLI on a
      synthetic frozen release with a `validation/` split (§16.6/§16.10/§16.13) — the §16.13
      refusal `no scorable hidden set` comes BEFORE any admission or unauthorized-attempt
      record, with and without a founder authorization, charges no unit, opens no hidden file
      and prints no verdict.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: conftest.validation_refusals (the recorded runs), tests.tools.mem01_verify.reference,
      .frozen_release (BOGUS_SHA256).
Key invariants:
  - The founder authorization the scenario appends is a hand-authored §16.1-exact event naming
    the lock and the candidate hashes the runner printed; the journal must not grow on either
    run because no attempt is recorded before the scorability check (§16.13).
  - The validation guard's admission, reservation, printed-verdict and consumption mechanics are
    sealed at module level in test_validation_guard.py.
"""

from __future__ import annotations

import json
import re

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import (
    NO_SCORABLE_REASON,
    SESSION_LOOP,
    ValidationRefusalsFactory,
)
from tests.tools.mem01_verify.frozen_release import BOGUS_SHA256
from tests.tools.mem01_verify.reference import CliRun

SET_LINE = re.compile(r"^(QS|NF|LANG|RET): GATES (PASS|FAIL)$")
AUTHORIZATION_KEYS = {"type", "event_id", "at", "lock", "code_hash", "config_hash", "principal"}


def _aborted(run: CliRun) -> dict:
    assert run.exit_code == 2, run.stderr[-2000:]
    block = reference.extract_machine_block(run.stdout)
    assert block["aborted"] is True and block["status"] == "ERROR" and block["reason"]
    assert not any(line.startswith("STEP1 ") for line in run.stdout.splitlines())
    assert not any(SET_LINE.match(line) for line in run.stdout.splitlines())
    return block


@SESSION_LOOP
async def test_validation_without_authorization_is_refused_for_no_scorable_set_before_any_record(
    validation_refusals: ValidationRefusalsFactory,
) -> None:
    scenario = await validation_refusals()

    block = _aborted(scenario.unauthorized)

    assert block["reason"] == NO_SCORABLE_REASON and block["run_kind"] == "validation"
    assert scenario.audit_after_unauthorized == b""  # no unauthorized_attempt either (§16.13)
    assert block.get("validation") is None  # "complete" only on a completed validation run


@SESSION_LOOP
async def test_authorized_validation_is_refused_for_no_scorable_set_before_admission(
    validation_refusals: ValidationRefusalsFactory,
) -> None:
    scenario = await validation_refusals()
    authorization_line = json.dumps(scenario.authorization, ensure_ascii=False).encode("utf-8")

    block = _aborted(scenario.authorized)

    assert block["reason"] == NO_SCORABLE_REASON and block["run_kind"] == "validation"
    assert set(scenario.authorization) == AUTHORIZATION_KEYS
    assert scenario.authorization["lock"] == scenario.frozen.lock_sha256()
    # the journal holds exactly the authorization the oracle wrote: no admission, no attempt
    assert scenario.audit_after_authorized == authorization_line + b"\n"


@SESSION_LOOP
async def test_validation_refusals_charge_no_unit_and_open_no_hidden_file(
    validation_refusals: ValidationRefusalsFactory,
) -> None:
    scenario = await validation_refusals()
    files = scenario.frozen.manifest()["files"]
    hidden = {
        relative: entry for relative, entry in files.items() if entry["visibility"] == "hidden"
    }

    assert scenario.ledger_after == b""  # §3.7: the validation run charges no hidden unit
    assert hidden and all(
        entry["sha256"]
        != reference.sha256_hex(scenario.frozen.hidden_file(relative.split("/")[2]).read_bytes())
        for relative, entry in hidden.items()
        if relative.startswith("hidden/validation/")
    )
    assert all(
        entry["sha256"] == BOGUS_SHA256
        for relative, entry in hidden.items()
        if relative.startswith("hidden/test/")
    )
    assert not (scenario.frozen.hidden_root / "releases" / scenario.frozen.name / "test").exists()
