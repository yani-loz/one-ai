"""
Role: The ONE way generated SQL reaches the database — validate, snapshot the tenant/person
      scope, execute, then prove the statement did not move that scope. The guard
      (app.ask.tools.sql_guard) is a denylist over a language PostgreSQL parses and we do not;
      this module is the structural backstop that makes a guard miss non-exploitable.
Used by: app.ask.tools.sql_tool (the query_database executor),
      app.ask.services.sql_pipeline (the direct-SQL arm).
Depends on: app.ask.tools.sql_guard (validation), SQLAlchemy Core, app.ask.exceptions.
Key invariants:
  - THE PLAN, NOT THE TEXT, IS THE SUBJECT OF REVIEW. Every statement is EXPLAINed and refused
    unless: every relation it touches is in _ALLOWED_RELATIONS (the surface the M-Schema
    documents, far smaller than the reader role's grant list); it touches at least one of them;
    every function it CALLS is in _ALLOWED_PLAN_CALLS; and its output columns contain no
    literal id. PostgreSQL renders plans CANONICALLY, so lexical evasions that beat a scan of
    the raw SQL are already undone: `U&"\\0073et_config"(…)` appears in the plan as plain
    `set_config(…)`, and a call buried in a scalar subquery gets its own plan node. This is the
    primary control; sql_guard's text checks are a cheap first filter, not it.
  - FUNCTIONS ARE ALLOWLISTED, NOT DENYLISTED. Three review rounds each defeated a denylist
    that "looked complete" — query_to_xml, then the database_/schema_to_xml variants, then
    ts_rewrite (a tsquery helper that runs its second argument as SQL through SPI). PostgreSQL
    ships thousands of functions and any single miss is a tenant-isolation break, so the list
    that must be complete is the one retrieval NEEDS, not the one that is dangerous.
  - THE SCOPE CHECK IS A TRIPWIRE, NOT A BOUNDARY. `app.current_org_id` and
    `app.current_person_id` are compared before and after execution, and a change fails the
    call and restores both GUC levels. It catches a statement that leaves the scope moved —
    but NOT one that moves it, reads, and moves it back inside a single statement, which
    PostgreSQL evaluates happily (demonstrated: a scalar-subquery hijack read another org's
    rows while leaving the GUC exactly as it found it). Never argue that a statement is safe
    because this check passed; it is here to catch drift the plan review missed.
  - The reader plane is SELECT-only at the role level; this module adds no privileges and
    holds no tenant logic of its own.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.sql_guard import validate_generated_sql
from app.ask.tools.sql_provenance import (
    PLAN_LITERAL,
    redact_computed_values,
    refuse_authored_column_names,
    refuse_caller_authored_output,
)

# The GUCs the PF-01 row-level policies read. A generated statement may never move them.
_SCOPE_SETTINGS = ("app.current_org_id", "app.current_person_id")

# The relations the generated-SQL hatch is allowed to read — exactly the surface the M-Schema
# describes to the SQL specialist. The reader ROLE can select from far more (the whole grant
# list), so without this the hatch's reach is the role's, not the documented schema's. The
# planner is the source of truth: EXPLAIN names every relation the statement actually touches,
# including the ones a view expands into, which no text scan could enumerate.
_ALLOWED_RELATIONS = frozenset(
    {
        "email_message",
        "email_recipient",
        "email_attachment",
        "person",
        "person_email",
        "counterparty_summary",
        # Not part of the documented schema: the PF-01 visibility POLICY on the content
        # tables expands into it, so every legitimate plan names it. It carries grant
        # bookkeeping (person -> object), never content, and is org-RLS-scoped like the rest.
        "acl_grant",
    }
)


# Functions that must never be CALLED by a generated statement: they rewrite the scope the
# RLS policies read, execute SQL from a string the planner never sees, reach the filesystem,
# or stall the connection. Matched as CALLS (`name(`) against the planner's own rendering, so
# a literal like '%set_config%' in a search term is data and does not trip it.
# A function call as PostgreSQL renders it in a plan: name immediately followed by '('.
# Requiring adjacency is what makes the allowlist below practical — plan prose such as
# "Hash Cond: (a.x = b.y)" always has a space, so only real calls are extracted.
_PLAN_CALL = re.compile(r"\b([a-z_][a-z0-9_]*)\(", re.IGNORECASE)

# EVERY function a generated statement may call. An ALLOWLIST, after a denylist lost three
# rounds running: it missed query_to_xml, then the database_/schema_to_xml variants, then
# ts_rewrite — a tsquery helper that executes its second argument as SQL through SPI. There is
# no way to enumerate what is dangerous in a catalog of thousands of functions, and every miss
# is a tenant-isolation break; there IS a way to enumerate what retrieval needs. An unknown
# name is refused, which costs a fall-through to the agent and nothing else.
#
# Derived from the plans of the legitimate corpus (tests/ask/security/attack_corpus.py) plus
# the ordinary read-only builtins a data question needs. Extend it deliberately, with a test.
_ALLOWED_PLAN_CALLS = frozenset(
    {
        # aggregates
        "avg",
        "count",
        "max",
        "min",
        "sum",
        "string_agg",
        "array_agg",
        "array_length",
        # text
        "btrim",
        "concat",
        "concat_ws",
        "initcap",
        "left",
        "length",
        "lower",
        "ltrim",
        # `repeat` is deliberately absent: it allocates server-side memory proportional to a
        # caller-chosen count and answers no question about an email archive.
        "position",
        "regexp_replace",
        "replace",
        "right",
        "rtrim",
        "split_part",
        "starts_with",
        "strpos",
        "substr",
        "substring",
        "trim",
        "upper",
        # numeric
        "abs",
        "ceil",
        "ceiling",
        "floor",
        "greatest",
        "least",
        "mod",
        "round",
        "trunc",
        # date/time
        "age",
        "date_part",
        "date_trunc",
        "extract",
        "now",
        "to_char",
        "to_date",
        "to_timestamp",
        # null/conditional handling and casts
        "coalesce",
        "nullif",
        # the RLS policies themselves call this; it only READS a setting (set_config, which
        # WRITES one, is deliberately absent — that is the whole point of the allowlist)
        "current_setting",
        # not a call: PostgreSQL renders the type `character varying(255)` this way
        "varying",
    }
)


# Enforcement switches, one per independent check. They exist so scripts/ask_loop/
# defence_matrix.py can neutralise exactly ONE mechanism and measure what still holds.
# Swapping the allowlist SETS for a permissive stand-in does not work — the checks use set
# subtraction, which never consults __contains__, so such a "mutation" is a silent no-op and
# the matrix reports redundancy that does not exist. A boolean the check reads is honest.
# All are True in production; only the matrix ever flips them, and it restores them after.
#
# EVERY enforcement point in this module must have a switch here, and the matrix asserts that
# count: an unswitched check is one the matrix cannot disable, so it is reported as redundantly
# covered no matter how load-bearing it is. Both post-execution checks were unswitched until
# the round-5 audit, and the alias rule turned out never to fire for its own pinned case.
_ENFORCE_RELATION_ALLOWLIST = True
_REQUIRE_RELATION = True
_ENFORCE_CALL_ALLOWLIST = True
_ENFORCE_PLAN_ROWS_CEILING = True


def _plan_call_text(plan: Any) -> str:
    """Every string ANYWHERE in the plan, with string LITERALS blanked out.

    The planner prints a user's search term as a literal (`subject ~~* '%set_config(%'::text`)
    right beside the function calls it will make. Scanning the raw plan for call syntax
    therefore reads search TERMS as code — the same defect that once made ~19 ordinary English
    words unusable in queries. Masking literals first keeps the scan on actual call sites.

    Recursion must reach strings at EVERY depth, including inside lists: `Output` is a LIST of
    expression strings, and an earlier version of this function only looked at string-valued
    dict entries — so `Output: ["pg_sleep(30)"]` was never scanned at all and the whole check
    silently rested on the layers in front of it. Found by the defence matrix, not by a test.
    """
    if isinstance(plan, str):
        return PLAN_LITERAL.sub("''", plan)
    if isinstance(plan, dict):
        return "\n".join(_plan_call_text(value) for value in plan.values())
    if isinstance(plan, list):
        return "\n".join(_plan_call_text(item) for item in plan)
    return ""


def _plan_relations(plan: Any) -> set[str]:
    """Every 'Relation Name' anywhere in an EXPLAIN (FORMAT JSON) plan tree."""
    found: set[str] = set()
    if isinstance(plan, dict):
        name = plan.get("Relation Name")
        if isinstance(name, str):
            found.add(name)
        for value in plan.values():
            found |= _plan_relations(value)
    elif isinstance(plan, list):
        for item in plan:
            found |= _plan_relations(item)
    return found


async def _assert_plan_is_safe(session: AsyncSession, safe_sql: str) -> Any:
    """Plan the statement and refuse it unless the PLAN itself is acceptable; return the plan.

    Two checks on one EXPLAIN (no ANALYZE, so nothing executes):
      * every relation the plan touches is in _ALLOWED_RELATIONS — the planner sees through
        views and subqueries, so this is the honest measure of what a statement can read;
      * no forbidden function is CALLED anywhere in the plan.

    The plan is the right place to look because PostgreSQL renders it CANONICALLY: the
    unicode-escaped `U&"\\0073et_config"` comes back as plain `set_config(...)`, quoting and
    case are normalised, and a call hidden in a scalar subquery still appears as its own
    InitPlan node. Every lexical evasion that defeats a scan of the raw SQL text is already
    undone by the time the planner describes what it will do.

    Raises:
        ToolExecutionError: the plan reads outside the documented schema, calls a forbidden
            function, or cannot be produced at all (an unplannable statement is not something
            to run hopefully).
    """
    try:
        explained = await session.execute(text(f"EXPLAIN (FORMAT JSON, VERBOSE) {safe_sql}"))
        plan = explained.scalar_one()
    except Exception as exc:
        raise ToolExecutionError(
            f"Generated SQL could not be planned ({type(exc).__name__}) — rephrase the "
            "request more concretely."
        ) from exc
    if isinstance(plan, str):
        plan = json.loads(plan)
    relations = _plan_relations(plan)
    disallowed = sorted(relations - _ALLOWED_RELATIONS) if _ENFORCE_RELATION_ALLOWLIST else []
    if disallowed:
        raise ToolExecutionError(
            f"Generated SQL reads tables outside the documented schema ({', '.join(disallowed)})"
            " — restrict the request to emails, attachments, and people."
        )
    if _REQUIRE_RELATION and not relations:
        # A statement that reads NO table satisfies the allowlist vacuously, and two separate
        # escapes lived in that gap: an SPI function that dumps tables through a channel the
        # planner never describes, and `SELECT '<uuid>' AS id` — a caller-authored constant
        # arriving in `rows` as if the database had returned it, which the citation grader
        # then accepts as evidence. This hatch exists to answer questions ABOUT the corpus;
        # a statement that touches none of it has nothing legitimate to say.
        raise ToolExecutionError(
            "Generated SQL reads no table from the documented schema — answer the question "
            "with a query over the emails, attachments, or people tables."
        )
    called = {name.lower() for name in _PLAN_CALL.findall(_plan_call_text(plan))}
    unknown = sorted(called - _ALLOWED_PLAN_CALLS) if _ENFORCE_CALL_ALLOWLIST else []
    if unknown:
        raise ToolExecutionError(
            f"Generated SQL calls functions that are not permitted on the retrieval plane "
            f"({', '.join(unknown)}) — use plain SELECT expressions and ordinary aggregates "
            "over the documented tables."
        )
    if _ENFORCE_PLAN_ROWS_CEILING:
        _refuse_unbounded_result(plan)
    refuse_caller_authored_output(plan)
    return plan


# TWO ceilings, because there are two distinct hazards and the first version only saw one.
#
# RETURNED rows are a MEMORY hazard in this process: SQLAlchemy buffers the whole result before
# `fetchmany(max_rows)` slices 50 rows off it.
#
# PROCESSED rows are a TIME hazard, and the top node cannot see them. Any aggregate reports
# `Plan Rows: 1` at the top, so `SELECT count(*) FROM email_message a, email_message b` sailed
# through a top-node-only check and then executed a cross join over the corpus — minutes of
# server CPU holding a reader-pool connection to return one row. Measured: top node 1, the
# Nested Loop beneath it 1600 on an almost-empty test database. No adversary is needed for this;
# a small text-to-SQL model emitting a join without a join condition is an ordinary failure.
#
# The processed ceiling is deliberately far looser than the returned one: a legitimate aggregate
# over a real corpus scans a lot of rows, and over-refusing ordinary analytics costs more than
# the tail it would catch. `statement_timeout` below is the real bound on time; this is the
# cheap structural one that fires before execution starts.
_MAX_PLAN_ROWS = 50_000
_MAX_PROCESSED_ROWS = 5_000_000
# A hard server-side bound on any single generated statement. Nothing else bounds TIME: there is
# no streaming and no server-side row limit anywhere in the app. SET LOCAL scopes it to the
# surrounding SAVEPOINT, so it cannot leak into the caller's session, and it touches none of
# _SCOPE_SETTINGS, so the scope tripwire does not see it.
_STATEMENT_TIMEOUT_MS = 15_000


def _plan_row_estimates(plan: Any) -> list[float]:
    """Every node's `Plan Rows` estimate, at any depth in the plan tree."""
    found: list[float] = []
    if isinstance(plan, dict):
        estimate = plan.get("Plan Rows")
        if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
            found.append(float(estimate))
        for value in plan.values():
            found.extend(_plan_row_estimates(value))
    elif isinstance(plan, list):
        for item in plan:
            found.extend(_plan_row_estimates(item))
    return found


def _refuse_unbounded_result(plan: Any) -> None:
    """Refuse a statement whose plan expects to RETURN or to PROCESS an unreasonable number of rows.

    The appended `LIMIT` is the primary bound on what is returned, and applies whenever the OUTER
    statement has none (an inner `LIMIT` inside a subquery used to suppress it). This adds the
    two ceilings the cap cannot provide: a model writing its own enormous outer LIMIT, and — the
    one a top-node check is blind to — an aggregate over a cross join, which returns one row
    while processing the square of the corpus.

    Estimates, not true counts: an estimate is what exists before execution, and approximate is
    fine for ceilings set this far above legitimate use.

    Raises:
        ToolExecutionError: the plan returns more than the plane serves, or processes more than
            any real question needs.
    """
    top = plan[0].get("Plan", {}) if isinstance(plan, list) and plan else {}
    returned = top.get("Plan Rows")
    if isinstance(returned, (int, float)) and returned > _MAX_PLAN_ROWS:
        raise ToolExecutionError(
            "Generated SQL is expected to return an unreasonable number of rows — add a "
            "filter, an aggregate, or a smaller LIMIT."
        )
    estimates = _plan_row_estimates(plan)
    if estimates and max(estimates) > _MAX_PROCESSED_ROWS:
        raise ToolExecutionError(
            "Generated SQL is expected to scan an unreasonable number of rows — check that "
            "every joined table has a join condition, and filter before aggregating."
        )


async def _read_scope(session: AsyncSession) -> dict[str, str | None]:
    """Current value of every scope GUC (missing/empty reads back as None)."""
    row = await session.execute(
        text(
            "SELECT "
            + ", ".join(
                f"nullif(current_setting('{name}', true), '') AS s{i}"
                for i, name in enumerate(_SCOPE_SETTINGS)
            )
        )
    )
    values = row.mappings().one()
    return {name: values[f"s{i}"] for i, name in enumerate(_SCOPE_SETTINGS)}


async def _restore_scope(session: AsyncSession, scope: dict[str, str | None]) -> None:
    """Force the scope GUCs back to their pre-statement values, at BOTH levels.

    A hijack may have been written with is_local=false (session level), which a
    transaction-local restore cannot undo: the original value would return the moment this
    transaction ends, on a pooled connection someone else will use. Restoring session level
    first and transaction level second leaves both consistent with what the caller bound.
    """
    for name, value in scope.items():
        for is_local in (False, True):
            await session.execute(
                text("SELECT set_config(:name, coalesce(:value, ''), :is_local)"),
                {"name": name, "value": value, "is_local": is_local},
            )


async def execute_guarded_sql(
    session: AsyncSession, generated_sql: str, *, max_rows: int
) -> tuple[str, list[dict[str, Any]]]:
    """Validate, execute, and scope-verify one generated statement.

    Args:
        session: the caller's READER-plane session (org/person GUCs already bound).
        generated_sql: raw text from the SQL specialist — never trusted, never executed as-is.
        max_rows: hard row cap for the fetch (payload discipline).

    Returns:
        (safe_sql, rows) — the validated statement actually executed, and its rows.

    Raises:
        ToolExecutionError: the statement failed validation, failed to execute, or MOVED THE
            TENANT/PERSON SCOPE. The last case means the guard was bypassed: the scope is
            restored first, and the message deliberately says nothing a caller could use to
            probe the guard further.
    """
    safe_sql = validate_generated_sql(generated_sql)
    scope_before = await _read_scope(session)
    try:
        # SAVEPOINT: a server-side error otherwise aborts the caller's whole transaction
        # (SQLSTATE 25P02) and every later statement on the session dies with it — including
        # the scope re-read below and any fall-through path that shares the session.
        async with session.begin_nested():
            # The only bound on TIME. Every other control here bounds what a statement may READ
            # or RETURN; nothing bounded how long it may run, and the plan-estimate ceiling is a
            # pre-execution guess that a bad estimate can walk straight past. SET LOCAL scopes
            # this to the savepoint, so it is reverted with it and cannot leak into the caller's
            # session; it is not one of _SCOPE_SETTINGS, so the scope tripwire ignores it.
            await session.execute(text(f"SET LOCAL statement_timeout = {_STATEMENT_TIMEOUT_MS}"))
            plan = await _assert_plan_is_safe(session, safe_sql)
            result = await session.execute(text(safe_sql))
            # Column NAMES are the caller's text and reach the observation as row-dict keys,
            # which no check that reads plan EXPRESSIONS can see. Values are decided by
            # provenance. Both live in sql_provenance.
            columns = list(result.keys())
            refuse_authored_column_names(columns)
            rows = redact_computed_values(
                plan, columns, [dict(r) for r in result.mappings().fetchmany(max_rows)]
            )
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError(
            f"Generated SQL failed to execute ({type(exc).__name__}) — rephrase the "
            "request more concretely."
        ) from exc
    scope_after = await _read_scope(session)
    if scope_after != scope_before:
        await _restore_scope(session, scope_before)
        raise ToolExecutionError(
            "Generated SQL attempted to change the session scope and was rejected."
        )
    return safe_sql, rows
