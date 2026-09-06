"""
Role: LIVE tests for the generated-SQL execution seam — the structural backstop behind the
      PF-FBP-8 denylist. Proves that a statement which moves the tenant/person scope FAILS and
      the scope is restored, even when the guard let the statement through.
Used by: pytest (tests/ask/tools). Needs a live reader plane: GUC behaviour is a database
      property, so a mocked session would prove nothing.
Depends on: app.ask.tools.sql_execution, app.core.database.reader_session, tests/ask/conftest.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.sql_execution import execute_guarded_sql
from app.core.database import reader_session

pytestmark = pytest.mark.usefixtures("ask_schema")

# PostgreSQL DECODES unicode escapes in identifiers, so this IS set_config to the server while
# a name-based scan sees only backslash-digits. It was accepted by the guard and executed by
# the restricted reader role, rewriting both scope GUCs — the reason this seam exists.
_UESCAPE_SET_CONFIG = (
    "SELECT U&\"\\0073et_config\"('app.current_org_id', "
    "'00000000-0000-0000-0000-000000000009', true) AS hijacked"
)


async def test_guard_rejects_unicode_escape_identifiers() -> None:
    org = uuid4()

    async with reader_session(org) as session:
        with pytest.raises(ToolExecutionError, match="Unicode-escape"):
            await execute_guarded_sql(session, _UESCAPE_SET_CONFIG, max_rows=10)


def _disable_front_line_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub BOTH the text guard and the plan review, leaving only the scope tripwire.

    The tripwire exists for the day a statement gets past everything in front of it, so it
    has to be tested with everything in front of it removed — otherwise the test only proves
    that the plan review works, which is a different test.
    """
    monkeypatch.setattr(
        "app.ask.tools.sql_execution.validate_generated_sql", lambda sql: sql
    )

    async def _allow_any_plan(session: object, safe_sql: str) -> None:
        return None

    monkeypatch.setattr(
        "app.ask.tools.sql_execution._assert_plan_is_safe", _allow_any_plan
    )


async def test_scope_change_fails_the_call_even_if_the_guard_is_bypassed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate a TOTAL failure of both front-line checks, so the scope-hijacking statement
    # really does reach the database and only the tripwire is left to catch it.
    _disable_front_line_checks(monkeypatch)
    org = uuid4()

    async with reader_session(org) as session:
        with pytest.raises(ToolExecutionError, match="session scope"):
            await execute_guarded_sql(
                session,
                "SELECT set_config('app.current_org_id', "
                "'00000000-0000-0000-0000-000000000009', true) AS hijacked",
                max_rows=10,
            )

        restored = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar_one()

    assert restored == str(org)  # the scope is put back, not merely reported


async def test_person_scope_change_is_also_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The person GUC is what separates colleagues inside ONE org — it needs the same tripwire
    # as the org GUC, and a person-bound session is the realistic MCP-01 shape.
    _disable_front_line_checks(monkeypatch)
    org, person = uuid4(), uuid4()

    async with reader_session(org, person) as session:
        with pytest.raises(ToolExecutionError, match="session scope"):
            await execute_guarded_sql(
                session,
                "SELECT set_config('app.current_person_id', "
                "'00000000-0000-0000-0000-000000000008', true) AS hijacked",
                max_rows=10,
            )

        restored = (
            await session.execute(
                text("SELECT current_setting('app.current_person_id', true)")
            )
        ).scalar_one()

    assert restored == str(person)


async def test_ordinary_statement_passes_through_untouched() -> None:
    # The checks must not cost anything on the normal path. Note the statement reads a real
    # table: a constant-only SELECT is refused now, because a statement that touches none of
    # the corpus has nothing legitimate to say and is how model literals reached `rows`.
    org = uuid4()

    async with reader_session(org) as session:
        safe_sql, rows = await execute_guarded_sql(
            session, "SELECT count(*) AS n FROM email_message", max_rows=10
        )

    assert rows == [{"n": 0}]  # RLS-filtered to this empty org, not an error
    assert safe_sql.startswith("SELECT count(*) AS n FROM email_message")


@pytest.mark.parametrize(
    ("label", "statement"),
    [
        # Each of these moves the scope, READS with it, and moves it back inside ONE
        # statement — PostgreSQL evaluates that happily, and the before/after scope check
        # sees nothing wrong. Only reviewing the PLAN catches them.
        (
            "scalar subqueries",
            "SELECT (SELECT set_config('app.current_org_id', 'ORG-B', true)) AS hijack, "
            "(SELECT count(*) FROM email_message) AS leaked, "
            "(SELECT set_config('app.current_org_id', 'ORG-A', true)) AS restore",
        ),
        (
            "from-clause ordering",
            "SELECT m.id FROM (SELECT set_config('app.current_org_id', 'ORG-B', true)) "
            "AS h(x), email_message m",
        ),
        (
            "cte",
            "WITH h AS (SELECT set_config('app.current_org_id', 'ORG-B', true) AS x) "
            "SELECT (SELECT x FROM h) AS hijack, count(*) AS leaked FROM email_message",
        ),
        # The planner renders names canonically, so the unicode-escape spelling that beat a
        # raw-text scan is already decoded by the time the plan describes the call.
        (
            "unicode-escaped name",
            "SELECT U&\"\\0073et_config\"('app.current_org_id', 'X', true)",
        ),
        # Executes SQL from a string argument the planner never sees — blocked by NAME.
        ("query_to_xml", "SELECT query_to_xml('SELECT 1', true, true, '')"),
        ("query_to_xml in FROM", "SELECT * FROM query_to_xml('SELECT 1', true, true, '') AS x"),
    ],
)
async def test_plan_review_rejects_scope_attacks_even_with_the_text_guard_disabled(
    monkeypatch: pytest.MonkeyPatch, label: str, statement: str
) -> None:
    # The text guard is stubbed to a pass-through so this proves the PLAN review stands on
    # its own. It has to: the guard filters a language PostgreSQL parses and we do not.
    monkeypatch.setattr(
        "app.ask.tools.sql_execution.validate_generated_sql", lambda sql: sql
    )
    org = uuid4()

    async with reader_session(org) as session:
        # Only that it does not execute — never HOW it was refused. The rejection reason is
        # an implementation detail that has already changed twice; the contract is that the
        # statement never runs.
        with pytest.raises(ToolExecutionError):
            await execute_guarded_sql(session, statement, max_rows=10)


async def test_scope_moved_by_a_nested_string_query_is_caught_by_the_tripwire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # query_to_xml() executes SQL from a STRING argument — a payload NEITHER the text guard
    # nor the planner ever inspects. With both front-line checks stubbed out, this is the
    # shape that only the scope tripwire can catch, which is why the tripwire is kept.
    _disable_front_line_checks(monkeypatch)
    org = uuid4()
    hijack = (
        "SELECT query_to_xml("
        "'SELECT set_config(''app.current_org_id'', ''HIJACKED'', false)', true, true, '')"
    )

    async with reader_session(org) as session:
        with pytest.raises(ToolExecutionError, match="session scope"):
            await execute_guarded_sql(session, hijack, max_rows=10)

        restored = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar_one()

    assert restored == str(org)


@pytest.mark.parametrize(
    "statement",
    [
        # Every one of these is SELECT-granted to the reader role and absent from the
        # M-Schema: without the allowlist the hatch's reach is the grant list, not the
        # documented six tables. audit_log carries actor emails and IP addresses;
        # connector_connection carries mailbox usernames and secret ciphertext.
        "SELECT actor_email FROM audit_log",
        "SELECT username, secret_ciphertext FROM connector_connection",
        "SELECT count(*) FROM principal_source_identity",
        # …and a subquery must not smuggle one past the check either.
        "SELECT (SELECT count(*) FROM audit_log) AS leaked",
    ],
)
async def test_statement_reading_outside_the_documented_schema_is_rejected(
    statement: str,
) -> None:
    org = uuid4()

    async with reader_session(org) as session:
        with pytest.raises(ToolExecutionError, match="outside the documented schema"):
            await execute_guarded_sql(session, statement, max_rows=10)


async def test_documented_tables_remain_readable() -> None:
    # The allowlist must not break the hatch's actual job.
    org = uuid4()

    async with reader_session(org) as session:
        _, rows = await execute_guarded_sql(
            session, "SELECT count(*) AS n FROM email_message", max_rows=10
        )

    assert rows == [{"n": 0}]  # RLS-filtered to this (empty) org, not an error


async def test_a_failed_statement_leaves_the_session_usable() -> None:
    # The pipeline promises it "can only add": a failure must not poison the transaction for
    # the fall-through path or for the scope re-read.
    org = uuid4()

    async with reader_session(org) as session:
        with pytest.raises(ToolExecutionError):
            await execute_guarded_sql(
                session, "SELECT no_such_column FROM email_message", max_rows=10
            )

        still_works = (await session.execute(text("SELECT 1 AS one"))).scalar_one()

    assert still_works == 1
