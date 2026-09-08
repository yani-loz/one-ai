"""
Role: The status algebra of contract §3.5 — the status vocabulary, the gate-name roster the
      block and the verdict line are written against, the derivation of a gate status from its
      criteria entries, the derivation of the block status from its gates, and the exit code.
Used by: `tools.mem01_verify.result_block`, `.verdict`, the gate evaluators (`gates.*`) and the
      runner `verify_step1`; sealed by `tests/tools/mem01_verify/test_statuses.py`.
Depends on: `tools.mem01_verify.exceptions` only.
Key invariants:
  - Gate precedence is ERROR > incomplete > FAIL > PASS over the DECIDING criteria entries;
    `diagnostic_only`, `directional` and `pending` entries never decide, and a gate with no
    deciding entry is `incomplete` — never a vacuous PASS (§16.10).
  - Block precedence is integrity/aborted > partial > ERROR|incomplete|skipped > FAIL > PASS.
  - `exit_code_for` accepts only the three BLOCK statuses; anything else is a `ResultBlockError`.
  - `GATE_NAMES` / `H_SPLIT_GATES` / `HOLDOUT_GATES` are the contract §1.3 rosters; the wave-3
    module `gates.registry` declares the same tuples and both derive from the contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from tools.mem01_verify.exceptions import ResultBlockError

PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"
INCOMPLETE = "incomplete"
PENDING = "pending"
SKIPPED = "skipped"
NOT_APPLICABLE = "N/A"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

#: The 17 gates, in the frozen contract order (§1.3 `gates.registry`).
GATE_NAMES: tuple[str, ...] = (
    "QS",
    "CH",
    "NF",
    "LANG",
    "IDEM",
    "VIS",
    "ERASE",
    "RET",
    "COV",
    "FID",
    "THR",
    "TIME",
    "IDENT",
    "RED",
    "ATTR",
    "SNAP",
    "EMB",
)

#: The gates whose evidence is labeled H data split across optimization / test / validation.
H_SPLIT_GATES: tuple[str, ...] = ("QS", "NF", "LANG", "RET")

#: The provisional/holdout gates, in the frozen order the verdict line renders them in (§3.8).
HOLDOUT_GATES: tuple[str, ...] = ("FID", "THR", "IDENT", "ATTR")

#: Statuses a criteria entry may carry (§3.4).
CRITERION_STATUSES: frozenset[str] = frozenset(
    {PASS, FAIL, ERROR, INCOMPLETE, PENDING, SKIPPED, NOT_APPLICABLE}
)

#: Statuses a gate may carry (§3.3).
GATE_STATUSES: frozenset[str] = frozenset({PASS, FAIL, ERROR, INCOMPLETE, SKIPPED})

#: Statuses the block may carry (§3.5).
BLOCK_STATUSES: tuple[str, ...] = (PASS, FAIL, ERROR)

_DECIDING_STATUSES: frozenset[str] = frozenset({PASS, FAIL, ERROR, INCOMPLETE})
_EXIT_CODES: dict[str, int] = {PASS: EXIT_PASS, FAIL: EXIT_FAIL, ERROR: EXIT_ERROR}


def _is_deciding(entry: Mapping[str, object]) -> bool:
    """True when a criteria entry may set its gate's status.

    Diagnostic-only and directional entries are excluded by §3.4; `pending` (a `.validation`
    entry outside the founder run), `skipped` and `N/A` carry no verdict of their own.
    """
    if bool(entry.get("diagnostic_only")) or bool(entry.get("directional")):
        return False
    return entry.get("status") in _DECIDING_STATUSES


def derive_gate_status(criteria: Sequence[Mapping[str, object]]) -> str:
    """Return the gate status implied by its criteria entries (§3.5).

    Args:
        criteria: The gate's criteria entries; each carries at least `status`,
            `diagnostic_only` and `directional`.

    Returns:
        `ERROR` if any deciding entry errored, else `incomplete` if any is incomplete, else
        `FAIL` if any failed, else `PASS`. A gate with no deciding entry at all — an empty
        sequence, or only diagnostic / directional / pending entries — is `incomplete`, never
        a vacuous PASS (§16.10).
    """
    observed = {entry.get("status") for entry in criteria if _is_deciding(entry)}
    if not observed:
        return INCOMPLETE
    for status in (ERROR, INCOMPLETE, FAIL):
        if status in observed:
            return status
    return PASS


def derive_block_status(
    *,
    gates: Mapping[str, Mapping[str, object]],
    partial: bool,
    aborted: bool,
    integrity_ok: bool,
) -> str:
    """Return the block status implied by the run's gates and its integrity flags (§3.5).

    Args:
        gates: Gate name → gate entry carrying at least `status`.
        partial: True iff `--gates` restricted the run (never an acceptance path).
        aborted: True iff a gate never reached a terminal status.
        integrity_ok: False iff a lock, roster, closure or cleanup check failed.

    Returns:
        `ERROR`, `FAIL` or `PASS`. An aborted run, an integrity failure and a partial run are
        `ERROR` even when every gate that ran passed.
    """
    if not integrity_ok or aborted:
        return ERROR
    if partial:
        return ERROR
    observed = {entry.get("status") for entry in gates.values()}
    if observed & {ERROR, INCOMPLETE, SKIPPED}:
        return ERROR
    if FAIL in observed:
        return FAIL
    return PASS


def exit_code_for(block_status: str) -> int:
    """Map a BLOCK status to the process exit code of §3.5.

    Args:
        block_status: One of `PASS`, `FAIL`, `ERROR`.

    Returns:
        0, 1 or 2 respectively.

    Raises:
        ResultBlockError: The value is not one of the three block statuses — gate-level
            statuses such as `incomplete` are explicitly refused (§16.10).
    """
    try:
        return _EXIT_CODES[block_status]
    except KeyError:
        raise ResultBlockError(
            f"exit_code_for expects one of {BLOCK_STATUSES}, got {block_status!r}"
        ) from None
