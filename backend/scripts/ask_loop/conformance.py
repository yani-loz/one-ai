"""
Role: Runnable grader conformance suite for the ask-tools loop (verifier CKPT1 finding 3) —
      runs every pinned grading rule and false-positive/false-negative regression WITHOUT
      pytest (pytest truncates the dev corpus). Run:
      `uv run python -m scripts.ask_loop.conformance`.
Used by: the loop operator before/after any grader change; the verifier re-runs it;
      scripts.ask_loop.seal_check executes it as the pin for every `conformance` ledger row.
Depends on: scripts.ask_loop.conformance_cases (the cases), which depends on
      scripts.ask_loop.grade + answer_extraction + conformance_golds. No DB, no network.
Key invariants:
  - Exits non-zero on ANY failed case; every historical grader bug gets a case.
  - The printed COUNT is load-bearing, not decoration: this prints `N/N PASS`, so it stays just
    as green with cases silently deleted. `seal_check` enforces a floor on N for that reason —
    raise `_MIN_CONFORMANCE_CASES` there deliberately whenever cases are added here.
"""

from __future__ import annotations

from scripts.ask_loop.conformance_cases import ALL_CASE_GROUPS

CASES_PASSED = 0


def check(name: str, condition: bool) -> None:
    """Assert one conformance case; raise on failure."""
    global CASES_PASSED
    if not condition:
        raise SystemExit(f"CONFORMANCE FAIL: {name}")
    CASES_PASSED += 1


def main() -> int:
    """Run every case group; print the tally."""
    for case_group in ALL_CASE_GROUPS:
        case_group(check)

    print(f"grader conformance: {CASES_PASSED}/{CASES_PASSED} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
