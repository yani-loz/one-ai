#!/usr/bin/env python3
"""
Role: Enforce the rule A2 file-size limits — hard-fail at 500 lines, warn at 300.
Used by: CI (.github/workflows/ci.yml) and the pre-commit hook.
Run from the repo root: `python scripts/check_file_size.py`.
Exit code 1 (with annotations) if any source file reaches the 500-line ceiling;
the 300-line soft target only warns (never fails).
"""

from __future__ import annotations

import sys
from pathlib import Path

CEILING = 500
SOFT_TARGET = 300
SCAN_ROOTS = [
    Path("backend/app"),
    # backend/scripts holds the eval harness and the security-seal machinery (grade.py,
    # conformance.py, seal_check.py, defence_matrix.py). It was outside the scan until the
    # round-5 audit, so an entire directory of Python — including the code that decides whether
    # a security finding is still closed — was exempt from rule A2 while the gate reported
    # "all source files under the ceiling".
    Path("backend/scripts"),
    Path("backend/tests"),
    Path("frontend/src"),
    Path("scripts"),
]
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".css"}


def measure_sources() -> list[tuple[Path, int]]:
    """Return (path, line_count) for every source file under the scan roots."""
    sizes: list[tuple[Path, int]] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
                continue
            with path.open(encoding="utf-8", errors="ignore") as handle:
                sizes.append((path, sum(1 for _ in handle)))
    return sizes


def main() -> int:
    sizes = measure_sources()
    over_ceiling = [(p, n) for p, n in sizes if n >= CEILING]
    over_soft = [(p, n) for p, n in sizes if SOFT_TARGET <= n < CEILING]

    for path, count in over_soft:
        print(f"::warning file={path}::{path} has {count} lines (>= {SOFT_TARGET} soft target; split soon)")
    for path, count in over_ceiling:
        print(f"::error file={path}::{path} has {count} lines (>= {CEILING}-line ceiling)")

    if over_ceiling:
        print(f"\n{len(over_ceiling)} file(s) at or over the {CEILING}-line hard ceiling (rule A2).")
        return 1
    if over_soft:
        print(f"{len(over_soft)} file(s) over the {SOFT_TARGET}-line soft target (warning only).")
    print(f"OK - all source files are under the {CEILING}-line ceiling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
