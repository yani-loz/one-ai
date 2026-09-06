"""
Role: The one question this module answers about a generated statement: did each value in the
      result come from the DATABASE, or from the caller? Holds the constant rules, the
      per-value provenance test, and the result-column-name rule.
Used by: app.ask.tools.sql_execution (the executor calls into here before and after running a
      statement).
Depends on: app.ask.exceptions, app.ask.tools.tool_helpers.redact_uuids. Pure functions over an
      EXPLAIN (FORMAT JSON, VERBOSE) plan and the rows that came back — no session, no I/O.
Key invariants:
  - PROVENANCE, NOT SHAPE. Round 5 broke the old shape-based rules four ways in one pass: a
    uuid rule missed a fabricated SENTENCE, a top-level-only scan missed a constant parked
    behind an optimisation fence, a constant rule missed a fact ASSEMBLED by a call, and the
    first fix's aggregate exemption would have re-admitted an aggregate over a constant. The
    question is never "does this look like an id" but "could this value depend on a row".
  - FAIL TOWARDS REDACTION. When the plan-to-column map is unusable, every computed column is
    replaced rather than guessed at. Over-redacting a rare shape costs a re-query; under-
    redacting puts a fabricated fact in front of the citation grader as tool-returned evidence.
  - These checks are SWITCHED (`_ENFORCE_*`) so scripts/ask_loop/defence_matrix.py can disable
    exactly one and measure what still holds. An unswitched check cannot be measured, and is
    therefore reported as covered no matter how load-bearing it is.
"""

from __future__ import annotations

import re
from typing import Any

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.tool_helpers import redact_uuids

# A function call as PostgreSQL renders it in a plan: name immediately followed by '('.
_PLAN_CALL = re.compile(r"\b([a-z_][a-z0-9_]*)\(", re.IGNORECASE)

# Enforcement switches — see the module docstring. All True in production; only the defence
# matrix ever flips them, and it restores them afterwards.
_ENFORCE_LITERAL_OUTPUT_RULE = True
_ENFORCE_ALIAS_ID_RULE = True
_ENFORCE_COMPUTED_ID_REDACTION = True


# A string literal as EXPLAIN renders it: single-quoted, internal quotes doubled.
PLAN_LITERAL = re.compile(r"'(?:[^']|'')*'")
# A uuid appearing as a CONSTANT in a plan expression (the ::uuid cast is how a selected
# literal id renders; literals are masked before the call scan but not before this one).
_UUID_CONSTANT = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
# The cast suffix as the planner renders it, reused by the count-shape test below.
_CAST_SUFFIX = r"(?:\s*::\s*[\w\." + r'"' + r"\[\] ]+)*"
# `$0`, `$1` — the planner's REFERENCE to a value computed by an InitPlan elsewhere in the
# tree. It is not an expression and reads nothing on its own, so the provenance test would call
# it caller-authored and refuse every legitimate scalar subquery. The node it points at is
# scanned on its own account, which is where the real answer is.
_PLAN_REFERENCE = re.compile(r"^\(*\s*\$\d+\s*\)*$")


def plan_output_entries(plan: Any) -> list[str]:
    """Every expression in every node's `Output` list, at any depth in the plan tree.

    The top-level Output is not the whole story: a constant defined inside a CTE or a fenced
    subquery lives in THAT node's Output, and the top node shows only the Var referring to it.
    Reading the top alone is what let a fabricated id ride in looking like a column value.
    """
    found: list[str] = []
    if isinstance(plan, dict):
        entries = plan.get("Output")
        if isinstance(entries, list):
            found.extend(str(entry) for entry in entries)
        for value in plan.values():
            found.extend(plan_output_entries(value))
    elif isinstance(plan, list):
        for item in plan:
            found.extend(plan_output_entries(item))
    return found


# A bare column reference as the planner writes it: `id`, `email_message.id`, `m.id`. Anything
# containing a call, an operator, a literal or a cast is a COMPUTED value.
_BARE_COLUMN = re.compile(r"^[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)*$")

# `count` is the ONE call that reads the corpus while naming no column, so an output entry that
# IS a count is data. The exemption is deliberately STRUCTURAL, not a name-presence test: an
# earlier version asked whether the expression MENTIONED count anywhere, which let any authored
# payload buy immunity by carrying one as decoration —
# `substr(concat_ws(' ','Acme','owes','42000'), 1, 19 + 0 * count(*))` returns the fabricated
# sentence and nothing else, while mentioning count. Now the whole entry must be the count.
#
# Two earlier versions of this exemption were each a hole of exactly the kind this function
# exists to close (every aggregate; then any expression mentioning count). That is worth
# remembering before widening it a third time.
_COUNT_OUTPUT = re.compile(r"^\(*\s*count\s*\([^()]*\)" + _CAST_SUFFIX + r"\s*\)*$", re.IGNORECASE)
# A cast as the planner renders it, so type names are not mistaken for column references.
_CAST_FRAGMENT = re.compile(r"::\s*[\w\." + r'"' + r"\[\] ]+(?:\(\d+(?:,\s*\d+)?\))?")
_ANY_IDENTIFIER = re.compile(r"[A-Za-z_][\w$]*")
# A result column name the caller could plausibly have meant as a NAME: identifier-shaped and
# short. Anything longer, spaced, hyphenated or carrying a long digit run is a phrase.
_PLAIN_COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
_LONG_DIGIT_RUN = re.compile(r"\d{3,}")
_NOT_FROM_THE_DATABASE = "[value not from the database]"


def _readable_column_names() -> frozenset[str]:
    """Every column name a generated statement could legitimately read.

    Built from the ORM models rather than from the hand-written M-Schema card, because the card
    is documentation and was measured WRONG (it described `email_recipient.kind` as 'to' or
    'cc' while the CHECK allowed five values — which is part of why a BCC leak survived four
    review rounds). Models are the same source the tables are created from.

    Imported here rather than at module top so this stays a leaf: nothing in the import path
    touches a session.
    """
    from app.access.models.acl_grant import AclGrant
    from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
    from app.entities.models.person import Person, PersonEmail

    models = (EmailMessage, EmailRecipient, EmailAttachment, Person, PersonEmail, AclGrant)
    names = {column.name.lower() for model in models for column in model.__table__.columns}
    # counterparty_summary is a VIEW, so it has no model; its projection is fixed by 0022.
    return frozenset(names | _COUNTERPARTY_SUMMARY_COLUMNS)


_COUNTERPARTY_SUMMARY_COLUMNS = frozenset(
    {
        "org_id",
        "domain",
        "first_contact",
        "last_contact",
        "first_message_id",
        "last_message_id",
        "inbound_count",
        "outbound_count",
        "total_mentions",
        "distinct_addresses",
    }
)
_KNOWN_COLUMNS = _readable_column_names()

# A value that came out of a SUBPLAN reads whatever that subplan read; the node itself is
# scanned separately, so treating the reference as a corpus read here is not a hole.
_SUBPLAN_REFERENCE = re.compile(r"\b(?:SubPlan|InitPlan)\b|\$\d+")
# A plan-internal quoted name — `"*SELECT* 2"` is how the planner labels a UNION branch. It is
# the PLANNER's text, not the caller's, and its digits are not a payload; masking it is what
# keeps the literal test below honest (the derived counterparty view was refused over one).
_PLAN_QUOTED_NAME = re.compile(r'"(?:[^"]|"")*"')
# Something the CALLER typed: a string literal, or a number that is not part of an identifier.
# Casts are stripped before this runs, because a type carries the planner's own digits
# (`NULL::character varying(10)`) and those are not a payload either.
_CALLER_LITERAL = re.compile(r"'|(?<![\w$.])\d")


def _expression_is_caller_authored(expression: str) -> bool:
    """True if this output expression's value cannot depend on any row in the corpus.

    Refusing pure CONSTANTS is not enough, because a function assembles one just as well:
    `concat_ws(' ', 'Acme', 'owes', '42000')` renders as a CALL, so no constant rule matches it,
    and the value — a fabricated FACT — arrived in `rows` looking like something the archive
    returned (measured, red-team round 5). The uuid redaction did not catch it either, because
    it strips ids and this is a sentence.

    The test: the expression carries something the caller typed, AND nothing in it reads the
    corpus. "Reads the corpus" means a KNOWN COLUMN NAME, a subplan reference, or `count`.

    Requiring a known column rather than "any surviving word" is the round-5 pass-3 fix, and it
    matters more than it looks. `_ANY_IDENTIFIER` cannot tell a column from a SQL KEYWORD, so
    `CASE WHEN (now() IS NOT NULL) THEN 'Acme owes 42000 EUR' ELSE '' END` was classified as
    data on the strength of the word `CASE` — an expression that reads nothing, depends on no
    row, and returns the caller's sentence unconditionally. There is no legitimate capability
    being protected by admitting that, which is what separates it from the dead-branch limit in
    the ledger's O9.

    The literal precondition is what keeps the rule from eating ordinary plumbing: `now()` and
    a bare `NULL` carry nothing the caller wrote, so they are not payloads whatever else is
    true of them.

    Only `count` is exempt among calls — it reads every row while naming no column, and an
    entry that matches it always returns a NUMBER, never the caller's string. Being an
    aggregate is not enough: `string_agg('Acme owes 42000', ',')` reads nothing and returns the
    caller's sentence once per row.
    """
    unquoted = _PLAN_QUOTED_NAME.sub('""', expression)
    masked = PLAN_LITERAL.sub("''", unquoted)
    if _COUNT_OUTPUT.match(masked) or _SUBPLAN_REFERENCE.search(masked):
        return False
    if not _CALLER_LITERAL.search(_CAST_FRAGMENT.sub(" ", unquoted)):
        # Nothing the caller supplied is in here at all, so there is no payload to carry.
        # `now()` and a bare `NULL` land here, and neither asserts anything about the archive.
        return False
    without_calls = _PLAN_CALL.sub("(", masked)
    surviving = _ANY_IDENTIFIER.findall(_CAST_FRAGMENT.sub(" ", without_calls))
    return not any(name.lower() in _KNOWN_COLUMNS for name in surviving)


def _is_authored_output(expression: str) -> bool:
    """`_expression_is_caller_authored`, but blind to the planner's own value REFERENCES.

    `$0` is how a top-level output names a value an InitPlan computed elsewhere. It reads
    nothing itself, so the provenance test calls it caller-authored — which would sentinel every
    legitimate scalar subquery (`(SELECT max(sent_at) FROM email_message)` came back redacted).
    The node it refers to is judged on its own account by `refuse_caller_authored_output`, so if
    that InitPlan were fabricating, the statement never reaches this function at all.
    """
    return not _PLAN_REFERENCE.match(expression) and _expression_is_caller_authored(expression)


def redact_computed_values(
    plan: Any, columns: list[str], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Blank out uuids that a COMPUTED expression produced, keeping ids read from columns.

    Checking the plan text for a literal id is not enough: `concat('1111…-1111','11111111')`
    is STABLE, so the planner does not fold it, the plan shows two harmless fragments, and the
    full canonical uuid only exists at runtime — where the citation grader then reads it as an
    id a tool returned. (`||` IS folded and was caught; the difference was volatility, not
    intent.) So provenance is decided per VALUE: an id that came out of a column is evidence,
    an id an expression assembled is not.

    The positional map from plan Output to result columns is only used when it is sound —
    `ORDER BY` adds the sort key to Output, so the lengths can differ. When every Output entry
    is a bare column there is nothing to redact; when the map is unusable and something IS
    computed, every uuid is redacted rather than guessed at.
    """
    if not _ENFORCE_COMPUTED_ID_REDACTION:
        return rows
    output = plan[0].get("Plan", {}).get("Output") if isinstance(plan, list) and plan else None
    if not isinstance(output, list):
        return rows
    computed = [not _BARE_COLUMN.match(str(expr)) for expr in output]
    if not any(computed):
        return rows  # every value came straight out of a column
    unmapped = len(output) != len(columns)
    suspect = {column for index, column in enumerate(columns) if unmapped or computed[index]}
    # A column the caller wrote entirely by itself is replaced outright, not just stripped of
    # ids: the fabricated payload may be a sentence, a number or a name, and only the ones
    # shaped like uuids were ever removed.
    #
    # When the positional map is UNUSABLE the conservative answer is the safe one. `ORDER BY` on
    # a column that is not selected adds a sort key to Output, so the lengths differ and no
    # entry can be tied to a result column — and an earlier version simply gave up here, which
    # would have let `SELECT concat_ws('Acme','owes') AS finding FROM email_message ORDER BY
    # sent_at` carry its payload straight through the fix. If anything in the statement is
    # caller-authored and we cannot tell WHICH column it lands in, every computed column is
    # replaced. That over-redacts a rare shape, and only one that already contains a
    # fabricated expression.
    any_authored = any(_is_authored_output(str(expr)) for expr in output)
    if unmapped:
        authored = suspect if any_authored else set()
    else:
        authored = {
            column
            for index, column in enumerate(columns)
            if _is_authored_output(str(output[index]))
        }
    return [
        {key: _redact_one_value(key, value, suspect, authored) for key, value in row.items()}
        for row in rows
    ]


def _redact_one_value(column: str, value: Any, suspect: set[str], authored: set[str]) -> Any:
    """Redact one cell according to where its value came from."""
    if column in authored:
        return _NOT_FROM_THE_DATABASE
    if column in suspect and isinstance(value, str):
        return redact_uuids(value)
    return value


def refuse_caller_authored_output(plan: Any) -> None:
    """Refuse a statement that emits a value the CALLER wrote rather than one the database found.

    Two checks, because a laundered constant can enter at two different depths:

    * TOP-LEVEL output: any entry that is entirely a constant becomes a result column the caller
      reads as data. `SELECT '<uuid>' AS id, count(*) FROM email_message` satisfied every
      structural rule while handing the citation grader a fabricated id, and the same shape with
      prose (`SELECT 'Acme signed the renewal on 2024-03-01' AS finding, count(*) …`) handed the
      critic a fabricated FACT — the uuid-shaped rule never saw it, because evidence does not
      have to be uuid-shaped.

    * ANY node's output: a constant parked one node down is invisible at the top. Behind an
      optimisation fence the planner emits a plain Var, so
      `WITH fake AS MATERIALIZED (SELECT '<uuid>'::uuid AS ev) SELECT fake.ev …` renders its top
      output as `fake.ev` — indistinguishable from a real column by any text rule, and the
      per-value redaction reads it as column provenance and leaves it alone. Measured: the
      fabricated uuid arrived in `rows` intact (red-team round 5).

    There is NO numeric carve-out, and this docstring claimed one until it was measured. The
    worry was `EXISTS (SELECT 1 FROM …)` rendering an inner output of `1`, which the depth scan
    would refuse. On PostgreSQL 16 it does not happen: non-correlated, correlated and
    target-list EXISTS are all rewritten into a semi-join or a hashed SubPlan, whose outputs are
    join keys (`r.email_id`) or subplan references (`(hashed SubPlan 2)`) — never a bare
    constant. A number parked in a CTE (`SELECT 42000 AS amount`) IS refused, which is the
    point. Re-measure this on a major-version upgrade along with everything else in O3.

    Raises:
        ToolExecutionError: some output column is a constant the caller supplied.
    """
    if not _ENFORCE_LITERAL_OUTPUT_RULE:
        return
    authored = [entry for entry in plan_output_entries(plan) if _is_authored_output(entry)]
    if authored:
        raise ToolExecutionError(
            "Generated SQL produces an output column from a value the request supplied rather "
            "than from the database — every column must read something from the emails, "
            "attachments or people tables."
        )


def refuse_authored_column_names(names: list[str]) -> None:
    """Refuse a result whose COLUMN NAMES were written by the caller as evidence.

    A column alias becomes a key in every row dict, and the plan does not show it
    (`SELECT 1 AS "<uuid>"` plans to Output: ['1']) — so a fabricated id, or a fabricated
    sentence, could ride into the observation as a KEY rather than a value, invisible to every
    check that reads expressions. A real column name is identifier-shaped, so refusing ids and
    phrases costs nothing.

    Raises:
        ToolExecutionError: a column is named after an id or a phrase.
    """
    if not _ENFORCE_ALIAS_ID_RULE:
        return
    authored = [name for name in names if not _is_plain_column_name(str(name))]
    if authored:
        raise ToolExecutionError(
            "Generated SQL names a result column after an id, a phrase, or a number — name "
            "columns for what they mean, as short plain identifiers (count, subject, sent_at, "
            "total_emails)."
        )


def _is_plain_column_name(name: str) -> bool:
    """True if this is a column NAME rather than a sentence the caller wants quoted back.

    Whitespace alone was not enough: underscores and hyphens carry a sentence just as well
    (`AS "Acme_owes_42000_EUR_as_of_2024_03_01"`), and a long digit run is the part of a
    fabricated label that actually asserts something — an amount, a date, a count. Names stay
    short and identifier-shaped; `?column?` is PostgreSQL's own name for an unaliased
    expression and is not the caller's text at all.

    A residual stays open and is NOT closed by this: a plausible-looking label on a REAL number
    (`AS emails_from_acme_about_the_renewal`) is caller text with no fabricated payload to
    detect. Column names cannot carry database provenance, so the durable fix is on the
    CONSUMER side — the grader must not read row KEYS as evidence — not on this rule.
    """
    return name == "?column?" or bool(
        _PLAIN_COLUMN_NAME.match(name) and not _LONG_DIGIT_RUN.search(name)
    )
