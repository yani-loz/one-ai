"""
Role: Unit tests for the plan scanner (_plan_call_text) — the part of the generated-SQL
      defence that decides WHERE in an EXPLAIN plan we look for function calls. Pure dict/str
      transforms over plan fixtures; no database.
Used by: pytest (tests/ask/tools).
Depends on: app.ask.tools.sql_execution (_plan_call_text, _PLAN_CALL, _ALLOWED_PLAN_CALLS).
Key invariants:
  - The scanner must reach strings at EVERY depth. PostgreSQL puts expressions in `Output`,
    which is a LIST of strings; a scanner that only reads string-valued dict entries sees
    nothing there and the whole check silently rests on the layers in front of it.
  - Literals must be blanked before scanning: a user's search term is data, and a scan that
    reads terms as code is how ~19 ordinary English words once became unusable.
"""

from __future__ import annotations

from app.ask.tools.sql_execution import _ALLOWED_PLAN_CALLS, _PLAN_CALL, _plan_call_text


def _hits(plan: object) -> list[str]:
    """Calls the scanner finds in this plan that are NOT on the allowlist."""
    called = {name.lower() for name in _PLAN_CALL.findall(_plan_call_text(plan))}
    return sorted(called - _ALLOWED_PLAN_CALLS)


def test_scanner_reads_expressions_inside_the_output_list() -> None:
    # The exact shape EXPLAIN emits for `SELECT pg_sleep(30)` — the call lives in a LIST.
    plan = [{"Plan": {"Node Type": "Result", "Output": ["pg_sleep(30)"]}}]

    assert _hits(plan) == ["pg_sleep"]


def test_scanner_reads_expressions_nested_in_subplans() -> None:
    # A call buried in a scalar subquery gets its own plan node; hijack-and-restore hides there.
    plan = [
        {
            "Plan": {
                "Node Type": "Result",
                "Output": ["$0", "$1"],
                "Plans": [
                    {
                        "Node Type": "Result",
                        "Subplan Name": "InitPlan 1 (returns $0)",
                        "Output": ["set_config('app.current_org_id'::text, 'x'::text, true)"],
                    }
                ],
            }
        }
    ]

    assert _hits(plan) == ["set_config"]


def test_scanner_reads_from_clause_function_calls() -> None:
    plan = [
        {
            "Plan": {
                "Node Type": "Function Scan",
                "Function Name": "query_to_xml",
                "Function Call": "query_to_xml('SELECT 1'::text, true, true, ''::text)",
            }
        }
    ]

    assert _hits(plan) == ["query_to_xml"]


def test_search_literals_are_not_read_as_calls() -> None:
    # `WHERE subject ILIKE '%set_config(%'` is a question about the WORD, not a call.
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "email_message",
                "Filter": "(subject ~~* '%set_config(%'::text)",
                "Output": ["id", "subject"],
            }
        }
    ]

    assert _hits(plan) == []


def test_doubled_quotes_inside_a_literal_do_not_end_the_mask() -> None:
    # PostgreSQL renders an embedded quote as '' — a mask that stops there would expose the
    # rest of the literal to the scan.
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Filter": "(subject ~~* '%it''s set_config(%'::text)",
            }
        }
    ]

    assert _hits(plan) == []


def test_ordinary_plan_is_clean() -> None:
    plan = [
        {
            "Plan": {
                "Node Type": "Aggregate",
                "Output": ["count(*)"],
                "Plans": [{"Node Type": "Index Scan", "Relation Name": "email_message"}],
            }
        }
    ]

    assert _hits(plan) == []
