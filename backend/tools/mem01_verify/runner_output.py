"""
Role: What the runner's machine block IS — the mutable `RunState` the §3.2 steps fill in, the
      three block builders of §3.3/§16.14 (completed, aborted, and the §16.16(t) minimal
      fallback). It re-exports the two §16.16(i)/(k) names the contract addresses here —
      `capture_app_logging` and `protected_result_relpath` — from `runner_logging`, and the four
      block-vocabulary names (`SCHEMA_NAME`, `PHASE_NAME`, `SPLIT_FOR_RUN_KIND`,
      `HIDDEN_RUN_KINDS`) from `result_block_schema`, which OWNS them — so the builders here and
      the checker there read ONE literal each.
Used by: `tools.mem01_verify.verify_step1`, `.runner_steps`, `.runner_probe`, `.runner_cleanup`
      and `.runner_render`; sealed through the CLI by
      `tests/tools/mem01_verify/test_verify_step1_*.py` and at module level by
      `test_runner_output_relpath.py` and `test_app_logging_capture.py`, both of which reach the
      re-exported names through THIS module.
Depends on: `tools.mem01_verify.statuses`, `.gates.context`, `.verdict`, `.exceptions`,
      `.result_block_schema` (the block vocabulary; it imports only `.result_block_checks` and
      `.statuses`, so nothing here can cycle), `.runner_logging`, and the wave-2 identity types
      under `TYPE_CHECKING` only.
Key invariants:
  - The block is assembled from `RunState` alone: an identity field never computed is `null` in
    the aborted shape and never invented (§16.14).
  - `hidden_budget*` exists exactly on a completed `checkpoint` run and `validation` exactly on a
    completed `validation` run; both are ABSENT (never null) on an aborted run (§16.14).
  - `reason` is present exactly when the block status is `ERROR` (§3.3), on both shapes.
  - §16.16(p)/(t): EVERY block carries `split_evaluated` (it follows from the run kind alone),
    and `build_minimal_aborted_block` — the fallback a refused stdout projection prints —
    carries no measurement of the run at all, only the envelope and why it aborted.
  - Nothing here formats a value that could carry personal data: gate reasons, diagnostics and
    exclusions come from the evaluators, which owe R5, and identity fields are hashes and ids.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from tools.mem01_verify.exceptions import RunRefusedError
from tools.mem01_verify.gates.context import GateResult

# The block VOCABULARY is owned by `result_block_schema` — the module that checks the shape.
# These four names are imported (never restated) so one literal defines each, and re-exported
# because the runner modules reach them through here.
from tools.mem01_verify.result_block_schema import HIDDEN_RUN_KINDS as HIDDEN_RUN_KINDS
from tools.mem01_verify.result_block_schema import PHASE_NAME as PHASE_NAME
from tools.mem01_verify.result_block_schema import SCHEMA_NAME as SCHEMA_NAME
from tools.mem01_verify.result_block_schema import SPLIT_FOR_RUN_KIND as SPLIT_FOR_RUN_KIND
from tools.mem01_verify.runner_logging import capture_app_logging as capture_app_logging
from tools.mem01_verify.runner_logging import protected_result_relpath as protected_result_relpath
from tools.mem01_verify.statuses import (
    ERROR,
    GATE_NAMES,
    HOLDOUT_GATES,
    PASS,
    SKIPPED,
    derive_block_status,
)
from tools.mem01_verify.verdict import HiddenCounters

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tools.mem01_verify.corpus_identity import CorpusIdentity
    from tools.mem01_verify.criteria import CriteriaFile
    from tools.mem01_verify.lock import ReleaseInfo

PARTIAL_REASON = "partial run (--gates)"
ABORTED_GATE_REASON = "aborted"
#: Gold-standard §8: Stage A runs each candidate once and caches nothing.
REPEATS_REQUIRED = 1
CACHE_POLICY = "forbidden"


def _require(value: object, missing: str) -> object:
    """Return `value`, refusing a step order that would have used a field before it existed."""
    if value is None:
        raise RunRefusedError(missing)
    return value


@dataclass
class RunState:
    """The run's accumulated identity and results — the sole input of the block builders."""

    run_kind: str
    run_id: str
    started_at: datetime
    partial: bool
    baseline_label: str | None
    step: int = 1
    org_id: UUID | None = None
    release: ReleaseInfo | None = None
    criteria: CriteriaFile | None = None
    corpus: CorpusIdentity | None = None
    migrations_digest: str | None = None
    fixtures_digest: str | None = None
    code_hash: str | None = None
    config_hash: str | None = None
    runner_digest: str | None = None
    versions: dict[str, str] = field(default_factory=dict)
    gate_results: dict[str, GateResult] = field(default_factory=dict)
    report_dir: Path | None = None
    report_root: Path | None = None
    probe_name: str | None = None
    probe_dropped: bool = False
    probe_kept: bool = False
    stale_probes: tuple[str, ...] = ()
    #: §16.16(t): the CLASS name of an artifact write that failed at step 10/11, or None.
    artifact_write_failure: str | None = None
    #: §16.17(d): the CLASS names of the `app` capture's emit failures, read at step 11.
    app_log_emit_failures: tuple[str, ...] = ()
    opened_outside_closure: list[str] = field(default_factory=list)
    hidden: HiddenCounters | None = None
    validation_complete: bool = False
    # Lifecycle handles the caller closes at step 10/11: `probe` is a `probe_db.ProbeDatabase`
    # and `lease` its open context manager (kept here so an exception after step 5 still closes
    # it), `log_capture` the open `ExitStack` holding `capture_app_logging`, `budget` a
    # `hidden_budget.HiddenBudget` and `reservation` its `Reservation`. They are typed `object`
    # so this module never imports the database layer.
    probe: object | None = None
    lease: object | None = None
    log_capture: object | None = None
    budget: object | None = None
    reservation: object | None = None
    attempt_id: str | None = None

    @property
    def split_evaluated(self) -> str:
        """The split this run scores, derived from its kind (§3.3)."""
        return SPLIT_FOR_RUN_KIND[self.run_kind]

    def require_release(self) -> ReleaseInfo:
        """The verified release (step 3); an integrity error when a caller ran out of order."""
        return _require(self.release, "the release has not been verified yet")

    def require_criteria(self) -> CriteriaFile:
        """The loaded annex (step 3)."""
        return _require(self.criteria, "the criteria annex has not been loaded yet")

    def require_corpus(self) -> CorpusIdentity:
        """The corpus identity (step 4)."""
        return _require(self.corpus, "the corpus identity has not been computed yet")

    def require_report_dir(self) -> Path:
        """The run's report directory (step 3)."""
        return _require(self.report_dir, "the report directory has not been resolved yet")

    def require_report_root(self) -> Path:
        """The root `protected_result_path` is recorded against (step 3, §16.16(i))."""
        return _require(self.report_root, "the report root has not been resolved yet")


def _identity(state: RunState) -> dict[str, object]:
    """The nine §3.3 hash fields, `null` wherever this run had not computed one yet."""
    manifest: Mapping[str, object] = state.release.manifest if state.release else {}
    return {
        "release_lock_sha256": state.release.lock_sha256 if state.release else None,
        "criteria_sha256": manifest.get("criteria_sha256"),
        "runner_sha256": state.runner_digest,
        "code_hash": state.code_hash,
        "config_hash": state.config_hash,
        "corpus_digest": state.corpus.corpus_digest if state.corpus else None,
        "text_digest": state.corpus.text_digest if state.corpus else None,
        "migrations_digest": state.migrations_digest,
        "fixtures_digest": state.fixtures_digest,
    }


def _cleanup(state: RunState) -> dict[str, object]:
    """The §3.3 `cleanup` object — what step 11 did with this run's probe."""
    return {
        "probe_dropped": state.probe_dropped,
        "probe_name": state.probe_name,
        "kept": state.probe_kept,
    }


def _envelope(state: RunState, *, status: str, duration_ms: int) -> dict[str, object]:
    """The fields every block shape carries (§3.3)."""
    return {
        "schema": SCHEMA_NAME,
        "phase": PHASE_NAME,
        "status": status,
        "run_kind": state.run_kind,
        "run_id": state.run_id,
        "started_at": state.started_at.isoformat(timespec="seconds"),
        "duration_ms": duration_ms,
    }


def _gate_entries(state: RunState) -> list[Mapping[str, object]]:
    """Every criteria entry of every gate that ran, in `GATE_NAMES` order (§3.4)."""
    return [
        entry
        for name in GATE_NAMES
        if name in state.gate_results
        for entry in state.gate_results[name].criteria
    ]


def _gate_summaries(state: RunState, *, aborted: bool) -> dict[str, dict[str, object]]:
    """Gate name → `{status, reason, criteria ids}`; gates that never ran are `skipped`."""
    absent = ABORTED_GATE_REASON if aborted else "gate did not run"
    summaries: dict[str, dict[str, object]] = {}
    for name in GATE_NAMES:
        result = state.gate_results.get(name)
        summaries[name] = (
            {"status": SKIPPED, "reason": absent, "criteria": []}
            if result is None
            else {
                "status": result.status,
                "reason": result.reason,
                "criteria": [str(entry["id"]) for entry in result.criteria],
            }
        )
    return summaries


def _set_counters(state: RunState) -> dict[str, dict[str, int]]:
    """Per SET the summed `expected / evaluated / skipped / errors` of its entries (§3.3)."""
    counters: dict[str, dict[str, int]] = {}
    for name in GATE_NAMES:
        block = dict.fromkeys(("expected", "evaluated", "skipped", "errors"), 0)
        result = state.gate_results.get(name)
        for entry in result.criteria if result else ():
            for counter in block:
                block[counter] += int(entry.get(counter, 0) or 0)
        counters[name] = block
    return counters


def _provisional_gates(
    entries: Sequence[Mapping[str, object]], directional: Sequence[str]
) -> list[str]:
    """The DERIVED `provisional_gates` of §3.3, in the frozen holdout order."""
    validated = {
        str(entry.get("gate"))
        for entry in entries
        if entry.get("split") == "validation" and entry.get("acceptance_state") == "validated"
    }
    return [name for name in HOLDOUT_GATES if name not in validated or name in directional]


def _status_reason(summaries: Mapping[str, Mapping[str, object]], *, partial: bool) -> str:
    """Why a completed run's block status is `ERROR` — counts of the non-PASS gates only."""
    if partial:
        return PARTIAL_REASON
    counts = Counter(
        str(entry["status"]) for entry in summaries.values() if entry["status"] != PASS
    )
    details = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    return f"gates not PASS: {details}"


def _hidden_fields(state: RunState) -> dict[str, object]:
    """`hidden_budget*` on a checkpoint run, `validation` on a validation run (§3.3)."""
    if state.run_kind == "checkpoint":
        counters = state.hidden or HiddenCounters(total=0, limit=0, by_split={})
        return {
            "hidden_budget": f"{counters.total}/{counters.limit}",
            "hidden_budget_by_split": dict(counters.by_split),
            "hidden_budget_limit": counters.limit,
            "hidden_invocations_under_lock": counters.invocations_under_lock,
        }
    return {"validation": "complete"} if state.run_kind == "validation" else {}


def build_completed_block(state: RunState, *, duration_ms: int) -> dict[str, object]:
    """Assemble the completed-run protected result of §3.3 from `RunState`.

    `state` is the finished run (every gate carries a terminal result) and `duration_ms` its
    wall-clock length. `reason` is present exactly when the status is `ERROR`; a partial run
    keeps its run kind and reports the literal §3.1 partial reason.
    """
    summaries = _gate_summaries(state, aborted=False)
    entries = _gate_entries(state)
    status = derive_block_status(
        gates=summaries, partial=state.partial, aborted=False, integrity_ok=True
    )
    criteria = state.criteria
    directional = list(criteria.directional_gates) if criteria else []
    corpus = state.corpus
    block: dict[str, object] = {
        **_envelope(state, status=status, duration_ms=duration_ms),
        "aborted": False,
        "partial": state.partial,
        "split_evaluated": state.split_evaluated,
        "release_name": state.release.name if state.release else "",
        "release_state": state.release.state if state.release else "draft",
        **_identity(state),
        "corpus": {
            "org_id": str(corpus.org_id) if corpus else None,
            "host": corpus.host if corpus else None,
            "port": corpus.port if corpus else None,
            "database": corpus.database if corpus else None,
            "emails": corpus.roster_counts.get("email_message", 0) if corpus else 0,
            "attachments": corpus.roster_counts.get("email_attachment", 0) if corpus else 0,
            "snapshot_transaction_id": corpus.snapshot_transaction_id if corpus else None,
        },
        "sets": _set_counters(state),
        "gates": summaries,
        "criteria": list(entries),
        "provisional_gates": _provisional_gates(entries, directional),
        "directional_gates": directional,
        **_hidden_fields(state),
        "repeats_required": REPEATS_REQUIRED,
        "repeats_completed": REPEATS_REQUIRED,
        "cache_policy": CACHE_POLICY,
        "cache_hits": 0,
        "diagnostics": _diagnostics(state),
        "exclusions": [
            dict(item)
            for name in GATE_NAMES
            if name in state.gate_results
            for item in state.gate_results[name].exclusions
        ],
        "opened_outside_closure": list(state.opened_outside_closure),
        "versions": dict(state.versions),
        "cleanup": _cleanup(state),
    }
    if status == ERROR:
        block["reason"] = _status_reason(summaries, partial=state.partial)
    if state.baseline_label is not None:
        block["baseline_label"] = state.baseline_label
    return block


def _diagnostics(state: RunState) -> dict[str, object]:
    """Per-gate aggregates plus the run-level probe observations (§3.3, aggregates only)."""
    diagnostics: dict[str, object] = {
        name: dict(state.gate_results[name].diagnostics)
        for name in GATE_NAMES
        if name in state.gate_results
    }
    run: dict[str, object] = {
        "stale_probe_databases": list(state.stale_probes),
        "probe_database": state.probe_name,
        "opened_outside_closure": len(state.opened_outside_closure),
        "artifact_write_failure": state.artifact_write_failure,
    }
    if state.app_log_emit_failures:  # §16.17(d): ABSENT when the capture reported none
        run["app_log_emit_failures"] = list(state.app_log_emit_failures)
    diagnostics["run"] = run
    return diagnostics


def build_aborted_block(
    state: RunState, *, step: int, reason: str, duration_ms: int
) -> dict[str, object]:
    """Assemble the aborted-run block of §3.3/§16.14 — no verdict follows it.

    `state` is the run as far as it got (identity fields it never computed stay `null`), `step`
    the §3.2 step it aborted at and `reason` why (never personal data, R5). `split_evaluated`
    follows from the run kind, so it is emitted on EVERY run and never null on a hidden one
    (§16.16(p)). `hidden_budget*` and `validation` are ABSENT; gates that never ran carry
    `skipped` / `aborted`. An artifact write that failed while the drop failed too — and the
    §16.17(d) app-log emit failures — are reported under `diagnostics` (§16.16(t)), which the
    hidden stdout projection drops.
    """
    block: dict[str, object] = {
        **_envelope(state, status=ERROR, duration_ms=duration_ms),
        "aborted": True,
        "reason": reason,
        "aborted_at_step": step,
        "split_evaluated": state.split_evaluated,
        **_identity(state),
        "gates": _gate_summaries(state, aborted=True),
        "criteria": list(_gate_entries(state)),
    }
    if state.release is not None:
        block["release_name"] = state.release.name
        block["release_state"] = state.release.state
    if state.probe_name is not None:
        block["cleanup"] = _cleanup(state)
    if state.artifact_write_failure is not None or state.app_log_emit_failures:
        block["diagnostics"] = _diagnostics(state)
    return block


def build_minimal_aborted_block(
    state: RunState, *, step: int, reason: str, duration_ms: int
) -> dict[str, object]:
    """The §16.16(t) fallback: the least an aborted run can print when its projection failed.

    A projection the schema refuses means the assembled block cannot be trusted to be
    hidden-safe, so this shape carries no measurement of the run at all: null identity fields,
    a `skipped` placeholder per gate, no criteria. What remains is the envelope, why the run
    aborted (`reason` names a violation CLASS, never a field or a value), where (`step`),
    `split_evaluated` and the probe `cleanup` step 11 performed.
    """
    return {
        **_envelope(state, status=ERROR, duration_ms=duration_ms),
        "aborted": True,
        "reason": reason,
        "aborted_at_step": step,
        "split_evaluated": state.split_evaluated,
        **dict.fromkeys(_identity(state), None),
        "gates": {name: {"status": SKIPPED, "reason": ABORTED_GATE_REASON} for name in GATE_NAMES},
        "criteria": [],
        "cleanup": _cleanup(state),
    }
