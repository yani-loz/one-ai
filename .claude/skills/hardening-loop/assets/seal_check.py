#!/usr/bin/env python3
"""
Role: Runs the seal on EVERY closed finding and reports, per finding, whether it still holds.
      The ledger says what was fixed; this proves it, today, by EXECUTING the pin each row names.
Used by: the operator before trusting a branch, and CI.
Key invariants (each earned the hard way — do not relax one without replacing it):
  - EXECUTES, never inspects. Nothing is judged by reading code.
  - A module pin must actually ASSERT something. pytest exits 0 when every test SKIPS, so an
    exit-code check reports every DB-backed finding SEALED with zero assertions run on a machine
    without a database. A seal that cannot tell "held" from "never ran" is not a seal.
  - A CLOSED row with NO runnable pin FAILS. The ledger's own rule is that such a row is not
    closed; printing it and exiting 0 makes the checker disagree with the document it checks.
  - Counters need a FLOOR: a suite that prints `N/N PASS` passes just as loudly with cases
    deleted, so assert the expected count, not just success.

TEMPLATE — adapt the marked sections to the project.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ── ADAPT ─────────────────────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
_LEDGER = _ROOT / "docs" / "FINDINGS_LEDGER.md"
_MODULE_PIN = re.compile(r"(tests/[\w/]+\.py)")  # how a test-module pin is written in the ledger
# RAISE THIS as findings are closed. It is the floor that stops a VACUOUS GREEN: with an empty
# CLOSED table this script prints "0 broken · 0 with no runnable pin" and "OK" and exits 0 —
# which are exactly the strings the goal evaluator matches on. Reproduced, not theorised.
_MIN_CLOSED_FINDINGS = 1
# ──────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LedgerRow:
    """One closed finding and the pins that are supposed to keep it closed."""

    finding_id: str
    title: str
    test_modules: tuple[str, ...]


def parse_ledger() -> list[LedgerRow]:
    """Every row of every CLOSED table, with the pins it names."""
    rows: list[LedgerRow] = []
    in_closed = False
    for line in _LEDGER.read_text(encoding="utf-8").splitlines():
        if line.startswith("## CLOSED"):
            in_closed = True
            continue
        if line.startswith("## OPEN"):
            in_closed = False
            continue
        if not (in_closed and line.startswith("| ")):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Skip the header row and the |---|---| separator.
        if len(cells) < 5 or cells[0] == "#" or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(
            LedgerRow(cells[0], cells[1], tuple(_MODULE_PIN.findall(cells[4])))
        )
    return rows


_PASSED = re.compile(r"(\d+) passed")


def check_module(module: str) -> bool:
    """Run one pinned test module; True ONLY if it actually asserted something."""
    finished = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "pytest", module, "-q", "-p", "no:cacheprovider"],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        return False
    passed = _PASSED.search(finished.stdout + finished.stderr)
    return bool(passed) and int(passed.group(1)) > 0


def main() -> int:
    """Print the per-finding seal report; non-zero if any seal is broken OR any row is unpinned."""
    rows = parse_ledger()
    if len(rows) < _MIN_CLOSED_FINDINGS:
        # A seal that seals NOTHING must fail, not print OK. This is the floor the docstring
        # above calls a key invariant, applied to this script's own counter.
        print(
            f"FAIL — {len(rows)} closed findings parsed, expected at least "
            f"{_MIN_CLOSED_FINDINGS}. Either the ledger path/format is wrong, or nothing is "
            "closed yet — in both cases a green seal would be a lie."
        )
        return 1
    cache: dict[str, bool] = {}
    broken: list[str] = []

    print(f"{'finding':8s} {'status':10s} finding / broken pin")
    for row in rows:
        failures = []
        for module in row.test_modules:
            if module not in cache:
                cache[module] = check_module(module)
            if not cache[module]:
                failures.append(module)
        if failures:
            broken.append(row.finding_id)
        status = "SEALED" if not failures else "BROKEN"
        detail = row.title if not failures else f"{row.title}  <-- {', '.join(failures)}"
        print(f"{row.finding_id:8s} {status:10s} {detail[:110]}")

    unpinned = [row.finding_id for row in rows if not row.test_modules]
    print(f"\n{len(rows)} closed findings · {len(broken)} broken · {len(unpinned)} with no runnable pin")

    if broken:
        print(f"\nFAIL — no longer sealed: {', '.join(broken)}")
    if unpinned:
        # A row nothing can execute does not meet the ledger's own definition of closed.
        print(f"FAIL — CLOSED with nothing to run: {', '.join(unpinned)} — add a pin or reopen.")
    if broken or unpinned:
        return 1
    print("\nOK — every closed finding is still sealed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
