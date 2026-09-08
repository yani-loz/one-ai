"""
Role: The §16.3 run-id grammar, in ONE place: the pattern source, the predicate `is_run_id`,
      the refusal helper `assert_run_id` (the caller names the `Mem01Error` subtype its own
      contract promises), and `new_run_id`, which mints an id and checks it against the very
      same pattern before returning it. A run id identifies a run in the verdict line, in the
      ledger and journal events, and in the probe database name `mem01_probe_<run_id>` — so the
      grammar is a safety boundary, not a formatting convention, and must have one encoding.
Used by: tools.mem01_verify.verdict (`is_run_id` and `RUN_ID_REGEX`, which its line grammar
      splices in), .probe_conn (`assert_run_id` with `ProbeDatabaseError`, reached from
      .probe_db when a probe is created or claimed), and .run_identity, which re-exports
      `new_run_id` as the §1.3 public name the runner imports.
Depends on: tools.mem01_verify.exceptions (`Mem01Error`, `IntegrityViolationError`); stdlib
      `re`, `datetime` and `uuid` otherwise. Nothing else — this is a leaf module, so no caller
      can create an import cycle by reaching for the grammar.
Key invariants:
  - `RUN_ID_REGEX` is the ONLY match-side encoding of the grammar in the instrument;
    `RUN_ID_PATTERN` anchors it and every consumer derives from one of the two — a second
    literal anywhere is the defect this module exists to prevent. `RUN_ID_STAMP_FORMAT` is the
    mint side, and `new_run_id` reconciles the two by checking every minted id against the
    pattern before returning it.
  - Matching is FULL-match everywhere: no leading or trailing character, newline included, is
    tolerated, because the id is interpolated into a Postgres identifier.
  - `new_run_id` never returns a value the pattern would reject; the hex suffix is fresh on
    every call, so two runs stamped at the same second never collide.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from tools.mem01_verify.exceptions import IntegrityViolationError, Mem01Error

RUN_ID_REGEX = r"[0-9]{8}t[0-9]{6}z_[0-9a-f]{8}"
"""The unanchored grammar source, for splicing into a larger pattern (the verdict line)."""

RUN_ID_PATTERN = re.compile(rf"^{RUN_ID_REGEX}$")
"""`<YYYYMMDD>t<HHMMSS>z_<8 lowercase hex>` (§16.3) — chosen so `mem01_probe_<run_id>` is a
valid lowercase Postgres identifier well under 63 characters."""

RUN_ID_GRAMMAR = "<YYYYMMDD>t<HHMMSS>z_<8 lowercase hex>"
"""The human rendering of the grammar, used in every refusal message."""

RUN_ID_STAMP_FORMAT = "%Y%m%dt%H%M%Sz"
"""The `strftime` format whose output the first two fields of the grammar describe."""


def is_run_id(text: str) -> bool:
    """True iff `text` is exactly a §16.3 run id.

    Args:
        text: The candidate value.

    Returns:
        Whether the whole string matches `RUN_ID_PATTERN` — a value with surrounding
        whitespace, an uppercase stamp or a suffix of the wrong length is not a run id.
    """
    return RUN_ID_PATTERN.fullmatch(text) is not None


def assert_run_id(run_id: str, *, error: type[Mem01Error]) -> str:
    """Return `run_id` after proving it matches the §16.3 grammar.

    Args:
        run_id: The candidate value.
        error: The `Mem01Error` subtype the CALLER's own contract promises for a bad id —
            `ProbeDatabaseError` on the probe path, `IntegrityViolationError` when the
            instrument is checking its own output.

    Returns:
        The unchanged `run_id`, so the check composes inside an expression.

    Raises:
        error: The value does not match `RUN_ID_PATTERN`.
    """
    if not is_run_id(run_id):
        raise error(f"run id {run_id!r} does not match {RUN_ID_GRAMMAR}")
    return run_id


def new_run_id(now: datetime) -> str:
    """Return a fresh run id in the §16.3 form `<YYYYMMDD>t<HHMMSS>z_<8 lowercase hex>`.

    Args:
        now: The instant to stamp; a naive value is read as UTC, an aware one is converted.

    Returns:
        The minted id, already checked against `RUN_ID_PATTERN`.

    Raises:
        IntegrityViolationError: The mint produced a value the grammar rejects (a year the
            stamp cannot render in four digits) — the instrument refuses to name a run, a
            ledger entry or a probe database with an id it would later reject.
    """
    stamped = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    minted = f"{stamped.strftime(RUN_ID_STAMP_FORMAT)}_{uuid4().hex[:8]}"
    return assert_run_id(minted, error=IntegrityViolationError)
