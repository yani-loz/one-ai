"""
Role: Runs the seal on EVERY closed finding and reports, per finding, whether it still holds.
      The ledger says what was fixed; this proves it, today, by executing the pin each row
      names. A finding whose pin no longer holds is reported BROKEN and the command fails.
Used by: the operator before trusting the branch, and CI
      (`uv run python -m scripts.ask_loop.seal_check`). Needs a migrated + role-provisioned
      database, same as the test suite.
Depends on: docs/PM/ask/ASK-SECURITY-LEDGER.md (the rows), tests/ask/security/attack_corpus
      (the executable attack/allowed cases), scripts/ask_loop/conformance, pytest for
      module-level pins.
Key invariants:
  - EXECUTES, never inspects. A corpus pin is checked by running the statement through the
    real execute_guarded_sql; a conformance pin by running the conformance suite; a module pin
    by running that test module. Nothing is judged by reading code.
  - Attack cases must be REFUSED and allowed cases must be ANSWERED — both directions, because
    a seal that blocks legitimate work is a broken seal too.
  - Exit code is the product: 0 only when every closed finding is still sealed.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.ask.exceptions import AskError
from app.ask.tools.sql_execution import execute_guarded_sql
from app.core.database import reader_session
from tests.ask.security.attack_corpus import (
    REDACTED_STATEMENTS,
    SQL_HATCH_ALLOWED,
    SQL_HATCH_ATTACKS,
)

_BACKEND = Path(__file__).resolve().parents[2]
_LEDGER = _BACKEND.parent / "docs" / "PM" / "ask" / "ASK-SECURITY-LEDGER.md"
_CORPUS_PIN = re.compile(r"corpus:([a-z0-9-]+)")
# A redaction case RUNS and is judged on what came back, so it cannot share the corpus pin's
# refused/answered binary — it gets its own notation.
_REDACTED_PIN = re.compile(r"redacted:([a-z0-9-]+)")
_MODULE_PIN = re.compile(r"(tests/[\w/]+\.py)")


@dataclass(frozen=True)
class LedgerRow:
    """One closed finding and the pins that are supposed to keep it closed."""

    finding_id: str
    title: str
    corpus_cases: tuple[str, ...]
    redacted_cases: tuple[str, ...]
    test_modules: tuple[str, ...]
    uses_conformance: bool


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
        if len(cells) < 5 or cells[0] == "#" or set(cells[0]) <= {"-", ":"}:
            continue
        pin = cells[4]
        rows.append(
            LedgerRow(
                finding_id=cells[0],
                title=cells[1],
                corpus_cases=tuple(_CORPUS_PIN.findall(pin)),
                redacted_cases=tuple(_REDACTED_PIN.findall(pin)),
                test_modules=tuple(_MODULE_PIN.findall(pin)),
                uses_conformance="conformance" in pin,
            )
        )
    return rows


async def check_corpus() -> dict[str, bool]:
    """Run every corpus case for real; True means it behaved as the corpus requires.

    A FRESH session per case. The scope tripwire and its restore run outside the savepoint, so
    a single case that poisons the session would make every later one fail with a DB error and
    be scored as a broken seal — a wave of false BROKEN rows sends the next round chasing
    phantoms, which costs exactly as much as a seal that flatters.
    """
    outcome: dict[str, bool] = {}
    for case in SQL_HATCH_ATTACKS:
        async with reader_session(uuid4()) as session:
            try:
                await execute_guarded_sql(session, case.sql, max_rows=5)
                outcome[case.case_id] = False  # it executed — the seal is broken
            except AskError:
                outcome[case.case_id] = True
            except Exception:  # noqa: BLE001 — a DB error is not a defence
                outcome[case.case_id] = False
    for allowed in SQL_HATCH_ALLOWED:
        async with reader_session(uuid4()) as session:
            try:
                await execute_guarded_sql(session, allowed.sql, max_rows=5)
                outcome[allowed.case_id] = True
            except Exception:  # noqa: BLE001 — refusing real work is a broken seal too
                outcome[allowed.case_id] = False
    return outcome


async def check_redacted() -> dict[str, bool]:
    """Run every redaction case; True means the fabricated id did NOT reach the caller.

    These statements are allowed to RUN — the guarantee is about what comes back, not about
    refusal — so they cannot be judged by the attack corpus's refused/answered test. A refusal
    also satisfies the guarantee: a redesign that chooses to reject the shape outright is still
    keeping the fabricated id away from the citation grader.
    """
    outcome: dict[str, bool] = {}
    for case in REDACTED_STATEMENTS:
        async with reader_session(uuid4()) as session:
            try:
                _, rows = await execute_guarded_sql(session, case.sql, max_rows=5)
            except Exception:  # noqa: BLE001 — refused outright; the id never reached anyone
                outcome[case.case_id] = True
                continue
        leaked = any(case.forbidden_value in str(v) for row in rows for v in row.values())
        outcome[case.case_id] = not leaked
    return outcome


def _run(command: list[str]) -> tuple[int, str]:
    """Run a checker as a subprocess; return (exit code, combined output)."""
    finished = subprocess.run(  # noqa: S603 — fixed argv, no shell
        command, cwd=_BACKEND, check=False, capture_output=True, text=True
    )
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


_CONFORMANCE_COUNT = re.compile(r"grader conformance: (\d+)/")
# Raise this deliberately when cases are added. It exists because the suite prints N/N — it
# passes just as loudly with cases silently deleted, and nothing else in the repo counts them.
_MIN_CONFORMANCE_CASES = 53


def check_conformance() -> bool:
    """Run the grader conformance suite; True only if it ran the expected number of cases.

    Exit code alone is not proof here either: `conformance` reports `N/N PASS`, so removing
    cases leaves it green with a smaller N. Requiring a floor makes deletion visible.
    """
    code, output = _run([sys.executable, "-m", "scripts.ask_loop.conformance"])
    if code != 0:
        return False
    counted = _CONFORMANCE_COUNT.search(output)
    return bool(counted) and int(counted.group(1)) >= _MIN_CONFORMANCE_CASES


_PASSED = re.compile(r"(\d+) passed")


def check_module(module: str) -> bool:
    """Run one pinned test module; True only if it actually ASSERTED something.

    Exit code alone is not proof. pytest exits 0 when every test SKIPS, and these modules skip
    themselves when the database roles are missing — so on a machine without a provisioned
    database an exit-code check would report every DB-backed finding SEALED with zero
    assertions executed. A seal that cannot tell "held" from "never ran" is not a seal.
    """
    code, output = _run(
        [sys.executable, "-m", "pytest", module, "-q", "--no-cov", "-p", "no:cacheprovider"]
    )
    if code != 0:
        return False
    passed = _PASSED.search(output)
    return bool(passed) and int(passed.group(1)) > 0


async def main() -> int:
    """Print the per-finding seal report; return non-zero if any seal is broken."""
    rows = parse_ledger()
    corpus = await check_corpus()
    redacted = await check_redacted()
    conformance_ok = check_conformance()
    module_cache: dict[str, bool] = {}

    print(f"{'finding':6s} {'status':10s} finding / broken pin")
    broken: list[str] = []
    for row in rows:
        failures: list[str] = []
        for case_id in row.corpus_cases:
            if case_id not in corpus:
                failures.append(f"corpus:{case_id} (missing)")
            elif not corpus[case_id]:
                failures.append(f"corpus:{case_id}")
        for case_id in row.redacted_cases:
            if case_id not in redacted:
                failures.append(f"redacted:{case_id} (missing)")
            elif not redacted[case_id]:
                failures.append(f"redacted:{case_id}")
        if row.uses_conformance and not conformance_ok:
            failures.append("conformance")
        for module in row.test_modules:
            if module not in module_cache:
                module_cache[module] = check_module(module)
            if not module_cache[module]:
                failures.append(module)
        status = "SEALED" if not failures else "BROKEN"
        if failures:
            broken.append(row.finding_id)
        detail = row.title if not failures else f"{row.title}  <-- {', '.join(failures)}"
        print(f"{row.finding_id:6s} {status:10s} {detail[:110]}")

    unpinned = [
        row.finding_id
        for row in rows
        if not (row.corpus_cases or row.redacted_cases or row.test_modules or row.uses_conformance)
    ]
    print(
        f"\n{len(rows)} closed findings · {len(broken)} broken · "
        f"{len(unpinned)} with no runnable pin"
    )
    # A row with no runnable pin FAILS. The ledger's own rule is that "closed" means a named
    # test would fail if the fix were reverted; a row nothing can execute does not meet it,
    # however convincing the prose is. Printing it and exiting 0 made the checker disagree with
    # the document it checks — and left the one status nobody would ever notice.
    if unpinned:
        print(f"  no runnable pin — these are NOT closed: {', '.join(unpinned)}")
    if broken or unpinned:
        if broken:
            print(f"\nFAIL — these findings are no longer sealed: {', '.join(broken)}")
        if unpinned:
            print(
                f"FAIL — these findings claim CLOSED with nothing to run: {', '.join(unpinned)}"
                " — add a pin or move the row back to OPEN."
            )
        return 1
    print("\nOK — every closed finding is still sealed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
