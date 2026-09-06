"""
Role: Mutation test for the generated-SQL defences. Disables each mechanism in turn and re-runs
      the whole attack corpus, producing a matrix of which mechanism actually stops which
      attack. Answers the question a passing test suite cannot: is a mechanism load-bearing, or
      is it redundant cover — and does every mechanism the ledger credits with a fix actually
      DO that job, or does something earlier in the pipeline reject the case first?
Used by: CI and the operator before trusting a change to the hatch
      (`uv run python -m scripts.ask_loop.defence_matrix`); the result belongs in the diary.
Depends on: tests.ask.security.attack_corpus (the data), app.ask.tools.sql_execution,
      app.core.database.reader_session. Needs a migrated + role-provisioned database, and must
      NOT run concurrently with pytest — both use the same test database.
Key invariants:
  - Disables mechanisms WITHOUT editing them: each is flipped at the switch the check itself
    reads, so this can never leave the codebase in a mutated state, and a mutation that fails
    to take effect raises instead of silently reporting redundancy that does not exist.
  - Read-only: every corpus attack that gets through is executed against an EMPTY throwaway
    org, so a defence gap is measured, never exploited against real data.
  - EVERY enforcement point in sql_execution has a column here. The count is asserted, because
    an unswitched check is invisible to this script and therefore reported as covered no matter
    how load-bearing it is.
  - Every mechanism carries a CAUSAL CLAIM: a case that must get THROUGH when that one
    mechanism is off. A mechanism with no such case is either dead cover or shadowed by a check
    that fires earlier — which is how a pin passes for the wrong reason and a fix can be
    reverted with the build still green.
  - Known confound, stated so nobody has to rediscover it: disabling the "text guard" swaps
    validate_generated_sql for the identity function, which removes the DESC rewrite and the
    LIMIT injection along with the checks. That column therefore changes more than one thing.
    It is the only impure mutation here, and it is why the text guard carries no causal claim —
    a "proven" result on that column would not have isolated anything.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from app.ask.exceptions import AskError
from app.ask.tools import sql_execution, sql_provenance
from app.core.database import reader_session
from tests.ask.security.attack_corpus import (
    REDACTED_STATEMENTS,
    SQL_HATCH_ALLOWED,
    SQL_HATCH_ATTACKS,
)

# Each entry is ONE mechanism that can regress on its own. "Plan review" used to be a single
# column here, but it is four independent checks — a regression in any one of them was hidden
# behind the other three (round-3 armour audit). The two POST-EXECUTION checks were missing
# entirely until the round-5 audit: they had no switch, so this script could not disable them,
# and reported them as redundantly covered while one of them never ran at all.
_BLOCKING_LAYERS = (
    "text guard",
    "relation allowlist",
    "touches-a-relation",
    "call allowlist",
    "plan-rows ceiling",
    "literal-output",
    "alias-id output",
    "scope tripwire",
)
# Redaction does not REFUSE a statement, it strips the fabricated id out of the rows. A
# blocked/through grid cannot express that, so it is measured on its own corpus below.
_REDACTION_LAYER = "computed-id redaction"


# Each mechanism is turned off at the switch the CHECK ITSELF reads. An earlier version
# swapped the allowlist SETS for a "contains everything" object — but the checks use set
# SUBTRACTION, which never consults __contains__, so those mutations were silent no-ops and
# the matrix reported redundancy that did not exist. Flipping the flag the code branches on
# is the only form of disable that cannot lie.
#
# Each entry names the MODULE the switch lives in as well as the attribute. The completeness
# assertion below scans every module in this map, which is how it caught the round-5 split of
# the provenance rules into their own module: three switches "no longer existed" in
# sql_execution, and had the matrix simply looked in one place it would have kept reporting
# those three mechanisms as covered while never disabling them.
_SWITCHES: dict[str, tuple[Any, str]] = {
    "relation allowlist": (sql_execution, "_ENFORCE_RELATION_ALLOWLIST"),
    "touches-a-relation": (sql_execution, "_REQUIRE_RELATION"),
    "call allowlist": (sql_execution, "_ENFORCE_CALL_ALLOWLIST"),
    "plan-rows ceiling": (sql_execution, "_ENFORCE_PLAN_ROWS_CEILING"),
    "literal-output": (sql_provenance, "_ENFORCE_LITERAL_OUTPUT_RULE"),
    "alias-id output": (sql_provenance, "_ENFORCE_ALIAS_ID_RULE"),
    _REDACTION_LAYER: (sql_provenance, "_ENFORCE_COMPUTED_ID_REDACTION"),
}


# The CAUSAL claim for each mechanism: with this mechanism alone disabled, these cases must get
# THROUGH. This is deliberately NOT stored on the corpus cases themselves — the corpus records
# input and required outcome only, so that a future redesign with different checkpoints still
# has to satisfy it. This table is the opposite: it describes THIS architecture, and it is
# expected to be rewritten when the architecture changes.
#
# Its purpose is to catch the failure mode a green suite cannot see. `SELECT 1 AS "<uuid>"` is
# relationless, so touches-a-relation refuses it during plan review and the alias check never
# executes; the alias rule could be deleted with its own ledger pin still passing. A mechanism
# with no case here is unproven, whatever the ledger says about it.
_CAUSAL_CLAIMS: dict[str, tuple[str, ...]] = {
    "relation allowlist": ("read-audit-log", "smuggle-forbidden-table-in-a-subquery"),
    "touches-a-relation": ("relationless-count",),
    "call allowlist": (
        "forbidden-call-in-a-filter",
        "ts-rewrite-spi-executor",
        "database-to-xml-dump",
    ),
    "literal-output": (
        "decorative-from-launders-a-literal-id",
        "table-anchored-prose-laundering",
        "cte-fenced-uuid-laundering",
        "cte-fenced-prose-laundering",
        "cte-fenced-function-assembled",
        "cte-fenced-number",
        "scalar-subquery-assembled-prose",
        "relationless-function-assembled-prose",
    ),
    "alias-id output": (
        "uuid-shaped-column-alias-over-a-real-table",
        "phrase-shaped-column-alias",
        "underscore-phrase-column-alias",
    ),
}

# A mechanism may legitimately block nothing on its own — but it must say so HERE, in writing,
# rather than by being absent. Silence is how an unmeasured check passes for a load-bearing one.
_NO_BLOCKING_CLAIM: dict[str, str] = {
    "text guard": (
        "measured non-load-bearing at RUNTIME: with it disabled, multi-statement, "
        "write-attempt and unterminated-literal are refused by EXPLAIN itself (asyncpg's "
        "extended protocol will not carry two commands, and a write does not plan on a "
        "SELECT-only role), and select-into reaches execution but is refused by the reader "
        "role's privileges. It remains the cheap first filter and the source of the useful "
        "error message — but the plan review and the role are what actually hold, which is "
        "what this project claimed when it inverted the denylist into an allowlist."
    ),
    "plan-rows ceiling": (
        "cannot be exercised here: the matrix runs every attack against an EMPTY throwaway org, "
        "so the planner's row estimate is always tiny and no cardinality attack can be "
        "demonstrated. Pinned instead by tests/ask/tools/test_result_bounds.py, which feeds it "
        "a plan directly"
    ),
    "scope tripwire": (
        "documented as a tripwire, NOT a boundary — a statement that moves the scope, reads, "
        "and moves it back inside ONE statement leaves it unchanged (see sql_execution)"
    ),
}


def _disable(layer: str) -> list[tuple[Any, str, Any]]:
    """Neutralise ONE mechanism; returns (module, name, original) triples for restoration.

    Raises:
        RuntimeError: the mutation did not take effect — a matrix whose disable is a no-op
            reports every attack as redundantly covered, which is worse than no matrix.
    """
    saved: list[tuple[Any, str, Any]] = []
    if layer in _SWITCHES:
        module, attribute = _SWITCHES[layer]
        saved.append((module, attribute, getattr(module, attribute)))
        setattr(module, attribute, False)
        if getattr(module, attribute) is not False:
            raise RuntimeError(f"disabling {layer!r} had no effect")
    elif layer == "text guard":
        guard = sql_execution.validate_generated_sql
        saved.append((sql_execution, "validate_generated_sql", guard))
        sql_execution.validate_generated_sql = lambda sql: sql
    elif layer == "scope tripwire":
        saved.append((sql_execution, "_read_scope", sql_execution._read_scope))  # noqa: SLF001

        async def _constant_scope(session: Any) -> dict[str, str | None]:
            return {}

        sql_execution._read_scope = _constant_scope  # noqa: SLF001
    else:
        raise RuntimeError(f"no disable defined for {layer!r}")
    return saved


def _restore(saved: list[tuple[Any, str, Any]]) -> None:
    """Put every swapped attribute back, and verify it landed.

    A restore that silently misses leaves every LATER row of the matrix running with the
    mechanism still disabled — including the "all on" column, which then reports escapes that
    are artefacts and redundancy that is fiction.
    """
    for module, name, original in saved:
        setattr(module, name, original)
        if getattr(module, name) is not original:
            raise RuntimeError(f"failed to restore {name!r}")


async def _blocked(sql: str) -> bool:
    """True if the statement was refused (any AskError) on an empty throwaway org.

    A fresh session per case: the scope tripwire and its restore run OUTSIDE the savepoint, so
    one poisoned session would make every later case fail with a DB error and be scored as a
    broken defence — measurement slander, which costs as much as flattery.
    """
    async with reader_session(uuid4()) as session:
        try:
            await sql_execution.execute_guarded_sql(session, sql, max_rows=5)
        except AskError:
            return True
        except Exception:  # noqa: BLE001 — a DB error is not a defence; report it as a gap
            return False
    return False


def _assert_every_enforcement_point_has_a_column() -> None:
    """Fail if sql_execution grew a check this script cannot disable.

    The switch block is the contract. A new enforcement point added without a switch is
    invisible here, so the matrix would report it as redundantly covered forever.

    Raises:
        RuntimeError: the module's switches and this script's columns have diverged.
    """
    modules = {module for module, _ in _SWITCHES.values()} | {sql_execution, sql_provenance}
    declared = {
        (module, name)
        for module in modules
        for name in vars(module)
        if name.startswith(("_ENFORCE_", "_REQUIRE_"))
    }
    monitored = set(_SWITCHES.values())
    unmonitored = sorted(f"{m.__name__}.{n}" for m, n in declared - monitored)
    if unmonitored:
        raise RuntimeError(
            f"enforcement switches with no matrix column: {unmonitored} — add a column and a "
            "causal claim, or the check is unmeasured"
        )
    stale = sorted(f"{m.__name__}.{n}" for m, n in monitored - declared)
    if stale:
        raise RuntimeError(f"matrix names switches that no longer exist: {stale}")
    # A switch can be registered in _SWITCHES and still never be exercised, if whoever added it
    # forgot the grid. It would then be disable-able in principle and disabled by nothing in
    # practice — and because the causal-claim check iterates the GRID, it would not be asked
    # for a claim either. _REDACTION_LAYER is the one deliberate exception: it does not refuse
    # statements, so it is measured on REDACTED_STATEMENTS instead of in the blocked/through
    # grid.
    ungridded = sorted(set(_SWITCHES) - set(_BLOCKING_LAYERS) - {_REDACTION_LAYER})
    if ungridded:
        raise RuntimeError(
            f"switches registered but never exercised: {ungridded} — add them to "
            "_BLOCKING_LAYERS, or measure them the way the redaction layer is measured"
        )


async def _check_redaction() -> list[str]:
    """Outcome check on REDACTED_STATEMENTS: the fabricated value must not reach the caller.

    This was a CAUSAL check until round 5 - disable the redaction and the value must leak - and
    it stopped being one when the provenance rule started REFUSING every one of these shapes
    before a row is fetched. That is a genuine strengthening, not a regression, but pretending
    the causal claim still holds would be the exact self-flattery this script exists to
    prevent. So the claim moved rather than being quietly kept: the redaction's own behaviour
    is pinned by tests/ask/tools/test_provenance.py, which builds a plan and rows directly.

    A case that produced NO ROW is reported as a problem, not credited. Every case here runs
    against an EMPTY throwaway org, so a statement shaped `SELECT concat(...) AS x FROM
    email_message LIMIT 1` returns nothing, exhibits nothing, and would pass while proving
    nothing - coverage in appearance only. An aggregate forces one row out of an empty table;
    this asserts it rather than trusting the corpus docstring to have been followed.
    """
    problems: list[str] = []
    for case in REDACTED_STATEMENTS:
        rows = await _returned_rows(case.sql)
        if rows is None:
            continue  # refused outright: the value certainly did not reach the caller
        if not rows:
            problems.append(
                f"{case.case_id}: returned no rows on an empty corpus, so it exhibits nothing "
                "and proves nothing - make it aggregate-shaped"
            )
            continue
        if any(case.forbidden_value in str(v) for row in rows for v in row.values()):
            problems.append(f"{case.case_id}: the fabricated value reached the caller")
    return problems


async def _returned_rows(sql: str) -> list[dict[str, Any]] | None:
    """The rows a statement returned, or None if it was refused."""
    async with reader_session(uuid4()) as session:
        try:
            _, rows = await sql_execution.execute_guarded_sql(session, sql, max_rows=5)
        except Exception:  # noqa: BLE001 — refused
            return None
    return rows


async def main() -> int:
    """Print the mechanism x attack matrix; return non-zero if anything is actually broken.

    Exit code is load-bearing: this runs in CI, and a report that always succeeds is a report
    nobody's build ever reads. It fails when an attack executes with everything on, when a
    legitimate query is refused, when a redaction case leaks, or when a mechanism's causal
    claim turns out to be false.
    """
    _assert_every_enforcement_point_has_a_column()
    print(f"{'attack':52s} " + " ".join(f"{lay:>20s}" for lay in ("all on", *_BLOCKING_LAYERS)))
    single_thread: list[str] = []
    escaped: list[str] = []
    through_when_off: dict[str, set[str]] = {layer: set() for layer in _BLOCKING_LAYERS}

    for case in SQL_HATCH_ATTACKS:
        row = [await _blocked(case.sql)]
        for layer in _BLOCKING_LAYERS:
            saved = _disable(layer)
            try:
                blocked = await _blocked(case.sql)
            finally:
                _restore(saved)
            if not blocked:
                through_when_off[layer].add(case.case_id)
            row.append(blocked)
        marks = " ".join(f"{('blocked' if hit else 'THROUGH'):>20s}" for hit in row)
        print(f"{case.case_id:52s} {marks}")
        if not row[0]:
            escaped.append(case.case_id)
        elif sum(1 for hit in row[1:] if hit) < len(_BLOCKING_LAYERS):
            # It got through with some single mechanism disabled => that one is the only thread.
            held_by = [lay for lay, hit in zip(_BLOCKING_LAYERS, row[1:], strict=True) if not hit]
            single_thread.append(f"{case.case_id} — held ONLY by: {', '.join(held_by)}")

    print("\nAttacks held by a SINGLE mechanism (a regression there is a real escape):")
    for entry in single_thread or ["(none — every attack is stopped by at least two)"]:
        print(f"  {entry}")

    print("\nCausal claims (with this mechanism OFF, at least one case must get through):")
    unproven: list[str] = []
    undeclared = sorted(set(_BLOCKING_LAYERS) - set(_CAUSAL_CLAIMS) - set(_NO_BLOCKING_CLAIM))
    for layer in _BLOCKING_LAYERS:
        if layer in _NO_BLOCKING_CLAIM:
            print(f"  {layer:22s} - no blocking claim: {_NO_BLOCKING_CLAIM[layer][:70]}")
            continue
        if layer in undeclared:
            print(f"  {layer:22s} - UNDECLARED (needs a case or a written reason)")
            continue
        named = set(_CAUSAL_CLAIMS[layer])
        proving = sorted((named & through_when_off[layer]) - set(escaped))
        shadowed = sorted(named - through_when_off[layer])
        # ANY, not ALL. A case that stays blocked is SHADOWED by another mechanism, which is
        # worth printing but is not a failure: layers legitimately overlap, and demanding that
        # every named case get through would force pruning good cases each time some other
        # mechanism is strengthened. What must never happen is a mechanism with NOTHING that
        # proves it - that is the state in which a fix can be deleted with the build green.
        status = f"proven by {proving[0]}" if proving else "UNPROVEN - nothing gets through"
        if shadowed:
            status += f" (shadowed: {chr(44).join(shadowed)})"
        print(f"  {layer:22s} - {status}")
        if not proving:
            unproven.append(f"{layer} ({chr(44).join(sorted(named))})")
    unproven.extend(f"{layer} (undeclared)" for layer in undeclared)

    print("\nRedaction (the fabricated value must not reach the caller):")
    redaction_problems = await _check_redaction()
    for problem in redaction_problems or ["  no redaction case leaked"]:
        print(f"  {problem}")

    print("\nOver-rejection check (legitimate queries must answer with everything on):")
    rejected = [a.case_id for a in SQL_HATCH_ALLOWED if await _blocked(a.sql)]
    print(f"  rejected: {rejected or 'none'}")

    if escaped or rejected or unproven or redaction_problems:
        print(
            f"\nFAIL — {len(escaped)} attack(s) executed with every layer on "
            f"({', '.join(escaped) or 'none'}); "
            f"{len(rejected)} legitimate query/queries refused "
            f"({', '.join(rejected) or 'none'}); "
            f"{len(unproven)} unproven causal claim(s) ({', '.join(unproven) or 'none'}); "
            f"{len(redaction_problems)} redaction problem(s)."
        )
        return 1
    print("\nOK — every attack blocked, every claim proven, every legitimate query answered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
