"""
Role: Hand-built §3.3 machine blocks (completed tuning / checkpoint / validation, all-PASS,
      aborted) and the stdout whitelist, shared by the result-block seals.
Used by: test_result_block.py, test_result_block_schema.py.
Depends on: tests.tools.mem01_verify.conftest (GATE_NAMES); stdlib.
Key invariants:
  - Every block here is schema-complete per the §3.3 field table; a test that needs a violation
    mutates a deep copy.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from tests.tools.mem01_verify.conftest import GATE_NAMES

HEX = hashlib.sha256(b"oracle").hexdigest()
HIDDEN_SETS = ("QS", "NF", "LANG", "RET")
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
STDOUT_WHITELIST = {
    "schema",
    "phase",
    "status",
    "aborted",
    "partial",
    "run_kind",
    "split_evaluated",
    "release_name",
    "release_state",
    "release_lock_sha256",
    "criteria_sha256",
    "runner_sha256",
    "code_hash",
    "config_hash",
    "corpus_digest",
    "text_digest",
    "migrations_digest",
    "fixtures_digest",
    "sets",
    "gates",
    "criteria",
    "provisional_gates",
    "directional_gates",
    "hidden_budget",
    "hidden_budget_by_split",
    "hidden_budget_limit",
    "hidden_invocations_under_lock",
    "validation",
    "repeats_required",
    "repeats_completed",
    "cache_policy",
    "cache_hits",
    "versions",
    "cleanup",
    "run_id",
    "started_at",
    "duration_ms",
}


def _entry(criterion_id: str, gate: str, *, split: str, status: str, basis: str = "F") -> dict:
    return {
        "id": criterion_id,
        "gate": gate,
        "set": gate,
        "split": split,
        "evidence_basis": basis,
        "acceptance_state": "provisional",
        "kind": "ratio",
        "numerator": 0,
        "denominator": 30,
        "denominator_def": "oracle denominator",
        "operator": "==",
        "threshold": 0,
        "minimum": 30,
        "status": status,
        "reason": "" if status == "PASS" else "oracle reason",
        "expected": 30,
        "evaluated": 30,
        "skipped": 0,
        "errors": 0,
        "diagnostic_only": False,
        "directional": False,
        "versions": {},
    }


def completed_block(run_kind: str = "tuning") -> dict:
    """A schema-complete §3.3 block: every gate `incomplete` except SNAP PASS (block ERROR)."""
    gates = {}
    criteria = []
    for gate in GATE_NAMES:
        criterion_id = f"{gate.lower()}.oracle"
        status = "PASS" if gate == "SNAP" else "incomplete"
        gates[gate] = {"status": status, "criteria": [criterion_id]}
        if status != "PASS":
            gates[gate]["reason"] = "component absent"
        criteria.append(_entry(criterion_id, gate, split="fixtures", status=status))
    block = {
        "schema": "MEM01_RESULT_V1",
        "phase": "step1",
        "status": "ERROR",
        "reason": "gates incomplete",
        "aborted": False,
        "partial": False,
        "run_kind": run_kind,
        "split_evaluated": "optimization"
        if run_kind == "tuning"
        else run_kind.replace("checkpoint", "test"),
        "release_name": "step1-gold-v1",
        "release_state": "draft" if run_kind == "tuning" else "frozen",
        "release_lock_sha256": HEX,
        "criteria_sha256": HEX,
        "runner_sha256": HEX,
        "code_hash": HEX,
        "config_hash": HEX,
        "corpus_digest": HEX,
        "text_digest": HEX,
        "migrations_digest": HEX,
        "fixtures_digest": HEX,
        "corpus": {
            "org_id": str(uuid4()),
            "host": "localhost",
            "port": 5432,
            "database": "mem01_probe_oracle",
            "emails": 1000,
            "attachments": 60,
            "snapshot_transaction_id": "00000A1B-0000000000000001-1",
        },
        "sets": {
            gate: {"expected": 0, "evaluated": 0, "skipped": 0, "errors": 0} for gate in GATE_NAMES
        },
        "gates": gates,
        "criteria": criteria,
        "provisional_gates": ["FID", "THR", "IDENT", "ATTR"],
        "directional_gates": [],
        "repeats_required": 1,
        "repeats_completed": 1,
        "cache_policy": "forbidden",
        "cache_hits": 0,
        "diagnostics": {gate: {} for gate in GATE_NAMES},
        "exclusions": [],
        "opened_outside_closure": [],
        "versions": {key: "1.0" for key in VERSION_KEYS},
        "cleanup": {
            "probe_dropped": True,
            "probe_name": "mem01_probe_20260906t120000z_0a1b2c3d",
            "kept": False,
        },
        "run_id": "20260906t120000z_0a1b2c3d",
        "started_at": "2026-09-06T12:00:00+00:00",
        "duration_ms": 10,
        "baseline_label": "before-census",
    }
    if run_kind == "checkpoint":
        block["hidden_budget"] = "1/20"
        block["hidden_budget_by_split"] = {"QS": 1, "NF": 1, "LANG": 1, "RET": 1}
        block["hidden_budget_limit"] = 20
        block["hidden_invocations_under_lock"] = 1
        block["criteria"] = [entry for entry in criteria if entry["gate"] not in ("QS", "NF")] + [
            _entry("qs.no_content_loss", "QS", split="test", status="FAIL", basis="H-test"),
            _entry("qs.echo_incidence", "QS", split="test", status="PASS", basis="H-test"),
            _entry("nf.noise_stopped", "NF", split="test", status="PASS", basis="H-test"),
        ]
        block["gates"]["QS"] = {
            "status": "FAIL",
            "reason": "content loss",
            "criteria": ["qs.no_content_loss", "qs.echo_incidence"],
        }
        block["gates"]["NF"] = {"status": "PASS", "criteria": ["nf.noise_stopped"]}
    if run_kind == "validation":
        block["validation"] = "complete"
    return block


def make_all_pass(block: dict, *, keep_reason: bool) -> dict:
    """Turn a completed block into an all-PASS one (status PASS; no gate or block reason)."""
    for gate in block["gates"].values():
        gate["status"] = "PASS"
        gate.pop("reason", None)
    for entry in block["criteria"]:
        entry["status"] = "PASS"
        entry["reason"] = ""
    block["status"] = "PASS"
    if not keep_reason:
        block.pop("reason", None)
    return block


def aborted_block() -> dict:
    """A §3.3 aborted-run block: identity nulls, every gate skipped with reason 'aborted'."""
    return {
        "schema": "MEM01_RESULT_V1",
        "phase": "step1",
        "status": "ERROR",
        "aborted": True,
        "reason": "lock mismatch",
        "aborted_at_step": 3,
        "run_kind": "tuning",
        "run_id": "20260906t120001z_0a1b2c3e",
        "started_at": "2026-09-06T12:00:00+00:00",
        "duration_ms": 3,
        "release_lock_sha256": None,
        "criteria_sha256": None,
        "runner_sha256": HEX,
        "code_hash": None,
        "config_hash": None,
        "corpus_digest": None,
        "text_digest": None,
        "migrations_digest": None,
        "fixtures_digest": None,
        "gates": {gate: {"status": "skipped", "reason": "aborted"} for gate in GATE_NAMES},
        "criteria": [],
    }
