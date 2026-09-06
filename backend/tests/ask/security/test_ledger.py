"""
Role: Keeps the findings ledger honest. A finding is closed only when a named test would fail
      if the fix were reverted, so every `corpus:<case_id>` pin in the ledger must name a case
      that actually exists — and every corpus case should be claimed by a ledger row, or it is
      coverage nobody can account for.
Used by: pytest (tests/ask/security). Pure text/data checks — no database.
Depends on: docs/PM/ask/ASK-SECURITY-LEDGER.md, tests/ask/security/attack_corpus.
Key invariants:
  - A pin cannot rot silently: deleting or renaming a corpus case fails here, naming the
    ledger row that just lost its evidence.
  - The ledger is the hand-off between review rounds. If it drifts from the corpus, the next
    round is briefed with a lie about what is already closed, and it re-litigates old ground —
    which is the specific waste this file exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.ask.security.attack_corpus import (
    REDACTED_STATEMENTS,
    SQL_HATCH_ALLOWED,
    SQL_HATCH_ATTACKS,
)

_LEDGER = Path(__file__).resolve().parents[4] / "docs" / "PM" / "ask" / "ASK-SECURITY-LEDGER.md"
_PIN = re.compile(r"corpus:([a-z0-9-]+)")
_REDACTED_PIN = re.compile(r"redacted:([a-z0-9-]+)")


def _ledger_text() -> str:
    """The ledger's contents (a missing ledger is itself a failure, not a skip)."""
    assert _LEDGER.exists(), f"ledger not found at {_LEDGER}"
    return _LEDGER.read_text(encoding="utf-8")


def test_every_ledger_pin_names_a_real_corpus_case() -> None:
    known = {c.case_id for c in SQL_HATCH_ATTACKS} | {c.case_id for c in SQL_HATCH_ALLOWED}

    pinned = set(_PIN.findall(_ledger_text()))

    missing = sorted(pinned - known)
    assert not missing, (
        f"ledger rows claim corpus cases that no longer exist: {missing} — either restore the "
        "case or reopen the finding; a pin that names nothing closes nothing"
    )


def test_every_redaction_pin_names_a_real_case() -> None:
    known = {c.case_id for c in REDACTED_STATEMENTS}

    pinned = set(_REDACTED_PIN.findall(_ledger_text()))

    missing = sorted(pinned - known)
    assert not missing, f"ledger rows claim redaction cases that no longer exist: {missing}"


def test_every_attack_case_is_claimed_by_the_ledger() -> None:
    # The reverse direction: a corpus case nobody has written down is coverage that will be
    # deleted the first time someone tidies the file, because its purpose is not recorded.
    pinned = set(_PIN.findall(_ledger_text()))

    unclaimed = sorted(c.case_id for c in SQL_HATCH_ATTACKS if c.case_id not in pinned)

    assert not unclaimed, (
        f"attack cases exist with no ledger row explaining what they close: {unclaimed}"
    )


def test_every_redaction_case_is_claimed_by_the_ledger() -> None:
    # REDACTED_STATEMENTS went unchecked in BOTH directions until the round-5 audit: the whole
    # round-4 provenance finding (concat() is STABLE, so the planner never folds the uuid) had
    # no ledger row and no seal, because every check here iterated SQL_HATCH_ATTACKS only.
    pinned = set(_REDACTED_PIN.findall(_ledger_text()))

    unclaimed = sorted(c.case_id for c in REDACTED_STATEMENTS if c.case_id not in pinned)

    assert not unclaimed, (
        f"redaction cases exist with no ledger row explaining what they close: {unclaimed}"
    )


def test_every_allowed_case_is_claimed_by_the_ledger_or_the_over_rejection_guard() -> None:
    # ALLOWED cases are the over-rejection guard; deleting one silently narrows what the hatch
    # is required to still answer. They need not be pinned individually, but the count must be
    # visible somewhere that fails when it drops.
    assert len(SQL_HATCH_ALLOWED) >= 12, (
        f"the allowed corpus shrank to {len(SQL_HATCH_ALLOWED)} cases — over-rejection is the "
        "other way this layer fails, and these entries are what catches it"
    )


def test_no_finding_is_marked_closed_without_a_pin() -> None:
    # Every row in a CLOSED table must carry something in its "Pin" column. The ledger's whole
    # claim is that "closed" means "a test would fail if this were reverted".
    unpinned: list[str] = []
    in_closed_table = False
    for line in _ledger_text().splitlines():
        if line.startswith("## CLOSED"):
            in_closed_table = True
            continue
        if line.startswith("## OPEN"):
            in_closed_table = False
            continue
        if not (in_closed_table and line.startswith("| ")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5 or cells[0] in {"#", "---"} or set(cells[0]) <= {"-", ":"}:
            continue
        if not cells[4]:
            unpinned.append(cells[0])

    assert not unpinned, f"CLOSED rows with an empty Pin column: {unpinned}"
