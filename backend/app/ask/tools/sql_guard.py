"""
Role: The guarded-SQL validator (FIX_BEFORE_PROD PF-FBP-8) — every model-generated SQL
      statement passes through validate_generated_sql() before touching the reader session.
      Fail-closed: anything not provably a single plain SELECT is rejected.
Used by: app.ask.tools.sql_tool (the query_database executor); any future SQL hatch.
Depends on: standard library only (auditable in isolation).
Key invariants:
  - DENY set_config and friends: the reader role is SELECT-only, but set_config() is callable
    from a SELECT and could rewrite the person/org GUCs to widen visibility — the documented
    PF-FBP-8 hazard this module exists to close.
  - Single statement, SELECT/WITH only, no SELECT INTO, no statement-separating semicolons,
    comments stripped BEFORE checks (nothing hides in /* */ or --).
  - A LIMIT is appended when absent (payload/token discipline; never widens semantics).
"""

from __future__ import annotations

import re

from app.ask.exceptions import ToolExecutionError

_MAX_SQL_CHARS = 4000
_DEFAULT_LIMIT = 50

# Function calls / statements that must never appear in reader-plane generated SQL.
_FORBIDDEN = re.compile(
    r"\b(set_config|setseed|pg_sleep|pg_terminate_backend|pg_cancel_backend|pg_reload_conf"
    r"|pg_read_file|pg_write_file|pg_ls_dir|lo_import|lo_export|dblink|copy|grant|revoke"
    r"|insert|update|delete|truncate|create|alter|drop|vacuum|reindex|cluster|listen"
    r"|notify|prepare|execute|deallocate|do|call|merge|refresh|security|lock)\b",
    re.IGNORECASE,
)
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_SELECT_INTO = re.compile(r"\bselect\b[^;]*?\binto\b", re.IGNORECASE | re.DOTALL)


def validate_generated_sql(sql: str) -> str:
    """Return the safe, executable form of a generated statement — or raise.

    Contract: comments stripped, trailing semicolon removed, single statement enforced,
    SELECT/WITH-only, forbidden tokens rejected, LIMIT appended when absent. The RETURNED
    string (never the input) is what the caller may execute.

    Raises:
        ToolExecutionError: with a model-repairable reason on any violation.
    """
    if not sql or not sql.strip():
        raise ToolExecutionError("Generated SQL is empty.")
    cleaned = _COMMENT_BLOCK.sub(" ", sql)
    cleaned = _COMMENT_LINE.sub(" ", cleaned).strip()
    if len(cleaned) > _MAX_SQL_CHARS:
        raise ToolExecutionError("Generated SQL exceeds the size limit.")
    cleaned = cleaned.rstrip().rstrip(";").rstrip()
    if ";" in cleaned:
        raise ToolExecutionError("Only a single SQL statement is allowed.")
    lowered = cleaned.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ToolExecutionError("Only SELECT statements are allowed.")
    if _SELECT_INTO.search(cleaned):
        raise ToolExecutionError("SELECT INTO is not allowed.")
    forbidden = _FORBIDDEN.search(cleaned)
    if forbidden:
        raise ToolExecutionError(
            f"Forbidden token {forbidden.group(0)!r} in generated SQL — plain read-only "
            "SELECT only."
        )
    if not re.search(r"\blimit\b", lowered):
        cleaned = f"{cleaned}\nLIMIT {_DEFAULT_LIMIT}"
    return cleaned
