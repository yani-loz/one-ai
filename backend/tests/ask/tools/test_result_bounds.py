"""
Role: Unit pins for the two controls that bound how much a generated statement can MATERIALISE
      — the appended row cap, and the planner-estimate ceiling.
Used by: pytest (tests/ask/tools). Pure functions over SQL text and a plan dict: no database.
Depends on: app.ask.tools.sql_guard.validate_generated_sql,
      app.ask.tools.sql_execution._refuse_unbounded_result.
Key invariants:
  - These live here rather than in the attack corpus because the defence matrix runs every
    corpus case against an EMPTY throwaway org, where the planner's row estimate is always tiny
    and no cardinality attack can be demonstrated at all. A pin that cannot fail is not a pin.
  - The hazard is memory in THIS process, not a slow query: SQLAlchemy buffers the whole result
    before `fetchmany(max_rows)` slices 50 rows off it, and there is no streaming, no
    server-side row bound and no `statement_timeout` anywhere in the app.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.sql_execution import (
    _MAX_PLAN_ROWS,
    _MAX_PROCESSED_ROWS,
    _refuse_unbounded_result,
)
from app.ask.tools.sql_guard import validate_generated_sql


def _plan_returning(rows: float) -> list[dict[str, Any]]:
    """An EXPLAIN (FORMAT JSON) skeleton whose top node expects `rows` rows."""
    return [{"Plan": {"Node Type": "Limit", "Plan Rows": rows, "Output": ["m.id"]}}]


# — The appended cap: only an OUTER limit bounds the result ————————————————————————————


@pytest.mark.parametrize(
    "statement",
    [
        # The measured escape: a LIMIT inside a subquery bounded nothing at the top level, yet
        # suppressed the cap. On the dev corpus this is a ~35M-row cartesian product.
        "SELECT a.id FROM email_message a, email_message b, (SELECT id FROM person LIMIT 1) z",
        "WITH x AS (SELECT id FROM person LIMIT 1) SELECT m.id FROM email_message m, x",
        "SELECT m.id FROM email_message m WHERE m.id IN (SELECT id FROM email_message LIMIT 10)",
        # A LIMIT that is only ever a search TERM must not count either.
        "SELECT id FROM email_message WHERE subject ILIKE '%limit%'",
        "SELECT id FROM email_message",
    ],
)
def test_a_statement_without_an_outer_limit_is_capped(statement: str) -> None:
    assert "LIMIT 50" in validate_generated_sql(statement)


def test_an_outer_limit_is_left_alone() -> None:
    # The model may legitimately ask for fewer rows; the cap exists to bound the unbounded, not
    # to overrule an explicit choice.
    capped = validate_generated_sql("SELECT id FROM email_message ORDER BY sent_at LIMIT 10")

    assert "LIMIT 50" not in capped
    assert capped.rstrip().endswith("LIMIT 10")


# — The ceiling: the backstop for an enormous OUTER limit the cap cannot help with ————————


def test_a_plan_expecting_too_many_rows_is_refused() -> None:
    with pytest.raises(ToolExecutionError):
        _refuse_unbounded_result(_plan_returning(_MAX_PLAN_ROWS + 1))


def test_an_ordinary_plan_passes() -> None:
    _refuse_unbounded_result(_plan_returning(50))
    _refuse_unbounded_result(_plan_returning(_MAX_PLAN_ROWS))


def test_a_plan_without_an_estimate_is_not_refused() -> None:
    # Fail towards ANSWERING here, deliberately: a missing estimate is a rendering difference,
    # not evidence of a huge result, and the appended cap is the primary bound in any case.
    _refuse_unbounded_result([{"Plan": {"Node Type": "Result"}}])


# — The PROCESSED ceiling: what the top node cannot see ————————————————————————————————


def _aggregate_over(processed: float) -> list[dict[str, Any]]:
    """A plan shaped `Limit -> Aggregate -> Nested Loop`, as a cross-join count really plans.

    The top two nodes estimate ONE row each — which is why a top-node-only check was blind to
    the work underneath, and why `SELECT count(*) FROM email_message a, email_message b` executed
    a cross join over the corpus to return a single number.
    """
    return [
        {
            "Plan": {
                "Node Type": "Limit",
                "Plan Rows": 1,
                "Output": ["count(*)"],
                "Plans": [
                    {
                        "Node Type": "Aggregate",
                        "Plan Rows": 1,
                        "Plans": [{"Node Type": "Nested Loop", "Plan Rows": processed}],
                    }
                ],
            }
        }
    ]


def test_an_aggregate_over_a_cross_join_is_refused() -> None:
    # The top node says 1 row. The work is underneath it.
    with pytest.raises(ToolExecutionError):
        _refuse_unbounded_result(_aggregate_over(_MAX_PROCESSED_ROWS + 1))


def test_an_ordinary_aggregate_is_not_refused() -> None:
    # A real aggregate scans plenty of rows; the processed ceiling is deliberately far looser
    # than the returned one, because over-refusing ordinary analytics costs more than the tail.
    _refuse_unbounded_result(_aggregate_over(100_000))


def test_the_estimate_walk_reaches_every_depth() -> None:
    # Assert the machinery, not only the verdict: a walk that stops at the top silently restores
    # the blindness this check exists to remove, and every behavioural assertion above would
    # still pass.
    from app.ask.tools.sql_execution import _plan_row_estimates

    assert max(_plan_row_estimates(_aggregate_over(4242))) == 4242
