"""
Role: Runs the whole attack corpus against the REAL execution path, every time. This is the
      regression floor for the Ask layer's security: a change that re-opens any defect we have
      ever closed fails here, named by the case that found it.
Used by: pytest (CI runs the full suite against a migrated database).
Depends on: tests/ask/security/attack_corpus (the data), app.ask.tools.sql_execution,
      app.ask.tools.tool_helpers, app.core.database.reader_session.
Key invariants:
  - Attacks go through execute_guarded_sql with EVERY layer live, exactly as production calls
    it. The tests assert only that the statement does not execute — never HOW it was refused,
    so the defence can be redesigned without rewriting this file (it has been, twice).
  - The ALLOWED corpus runs in the same place: hardening that breaks legitimate retrieval is a
    regression too, and this is the half that catches it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.ask.exceptions import AskError
from app.ask.tools.sql_execution import execute_guarded_sql
from app.ask.tools.tool_helpers import redact_uuids
from app.core.database import reader_session
from tests.ask.security.attack_corpus import (
    LAUNDERING_INPUTS,
    LAUNDERING_UUID,
    REDACTED_STATEMENTS,
    SQL_HATCH_ALLOWED,
    SQL_HATCH_ATTACKS,
    AllowedCase,
    AttackCase,
    RedactedCase,
)

pytestmark = pytest.mark.usefixtures("ask_schema")


@pytest.mark.parametrize("case", SQL_HATCH_ATTACKS, ids=lambda c: c.case_id)
async def test_known_attack_never_executes(case: AttackCase) -> None:
    org = uuid4()

    async with reader_session(org) as session:
        with pytest.raises(AskError):
            await execute_guarded_sql(session, case.sql, max_rows=10)


@pytest.mark.parametrize("case", SQL_HATCH_ALLOWED, ids=lambda c: c.case_id)
async def test_legitimate_query_still_answers(case: AllowedCase) -> None:
    # An empty org is fine: what matters is that the statement is ACCEPTED and returns rows
    # (RLS filters the content). A rejection here means hardening cost us the product.
    org = uuid4()

    async with reader_session(org) as session:
        _, rows = await execute_guarded_sql(session, case.sql, max_rows=10)

    assert isinstance(rows, list)


@pytest.mark.parametrize("case", REDACTED_STATEMENTS, ids=lambda c: c.case_id)
async def test_a_computed_id_never_reaches_the_caller(case: RedactedCase) -> None:
    # These statements are allowed to run — what must not happen is a fabricated id arriving
    # in `rows` looking like something the database returned.
    org = uuid4()

    async with reader_session(org) as session:
        try:
            _, rows = await execute_guarded_sql(session, case.sql, max_rows=5)
        except AskError:
            return  # refusing outright is also an acceptable outcome

    assert case.forbidden_value not in str(rows)


@pytest.mark.parametrize("echoed", LAUNDERING_INPUTS)
def test_caller_supplied_uuid_cannot_survive_an_echo(echoed: str) -> None:
    # Every path that echoes model-supplied text back into an observation runs it through
    # this; the grader then scans observations for ids. If a uuid survives, the model can mint
    # its own evidence by asking for an id it invented and reading it straight back.
    assert LAUNDERING_UUID not in redact_uuids(echoed)


def test_corpus_entries_are_traceable() -> None:
    # A case whose provenance nobody remembers is a case nobody dares delete and nobody
    # trusts. Every entry must say where it came from and which guarantee it protects.
    for case in SQL_HATCH_ATTACKS:
        assert case.found.strip(), f"{case.case_id} has no provenance"
        assert case.guarantee.strip(), f"{case.case_id} names no guarantee"
    for allowed in SQL_HATCH_ALLOWED:
        assert allowed.why_it_matters.strip(), f"{allowed.case_id} has no rationale"

    ids = [c.case_id for c in SQL_HATCH_ATTACKS] + [c.case_id for c in SQL_HATCH_ALLOWED]
    assert len(ids) == len(set(ids)), "duplicate case ids make failures ambiguous"
