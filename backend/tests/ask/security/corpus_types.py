"""
Role: The three shapes a security corpus case can take — must never execute, must keep
      working, and may run but must not leak. Data classes only.
Used by: tests.ask.security.attack_corpus, tests.ask.security.fabrication_corpus.
Depends on: nothing (plain dataclasses — importable with no DB and no app module).
Key invariants:
  - They live apart from the cases so the two corpus modules can import them without
    importing each other.
  - Every case type carries its PROVENANCE. A case whose origin nobody remembers is a case
    nobody dares delete and nobody trusts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttackCase:
    """One statement the generated-SQL hatch must refuse, and why it exists."""

    case_id: str
    guarantee: str
    found: str
    sql: str


@dataclass(frozen=True)
class AllowedCase:
    """One statement the hatch must keep accepting (the over-rejection guard)."""

    case_id: str
    why_it_matters: str
    sql: str


@dataclass(frozen=True)
class RedactedCase:
    """A statement that may RUN, but whose fabricated id must not reach the caller.

    Some laundering shapes are not worth refusing — the query is otherwise ordinary and the
    caller may have a real reason to compute a string. What must not happen is the fabricated
    id arriving in `rows` looking like something the database returned.

    INVARIANT: `sql` MUST be aggregate-shaped (carry a `count(*)` or equivalent). Unlike an
    AttackCase, which proves REFUSAL and is therefore decided at EXPLAIN time before any row
    exists, a redaction case has to RUN and exhibit a value in `rows`. The defence matrix
    executes every case against an EMPTY throwaway org, so a case written as
    `SELECT concat(…) AS x FROM email_message LIMIT 1` returns ZERO rows there, exhibits
    nothing, and passes while proving nothing — coverage in appearance only. An aggregate forces
    exactly one row out of an empty table. The matrix also asserts the row count rather than
    trusting this note.
    """

    case_id: str
    found: str
    sql: str
    forbidden_value: str
