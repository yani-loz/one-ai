"""
Role: The primitives every Ask retrieval tool shares — result-size clamping, ISO-date arg
      parsing, ILIKE-term escaping, and the `limit` parameter schema all tools expose.
Used by: app.ask.tools.email_filters/person_tool/email_search/email_read/attachment_tools.
Depends on: app.ask.exceptions (nothing else — deliberately dependency-free).
Key invariants:
  - LIMITs are capped at _MAX_LIMIT server-side regardless of what the model asks for.
  - _like() escapes LIKE metacharacters: a search term is data, never a pattern.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import UUID

from app.ask.exceptions import ToolExecutionError

# Deliberately UNANCHORED, matching the citation grader's own pattern character for character:
# a redaction narrower than the extractor is not a redaction. A `\b` boundary here let
# `x11111111-1111-1111-1111-111111111111` survive while the grader still harvested the uuid.
_UUID_ANYWHERE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def parse_id_arg(args: dict[str, Any], field: str) -> str:
    """Read a required UUID argument, rejecting a malformed one BEFORE it reaches the database.

    A bad uuid sent to Postgres raises a driver error that aborts the transaction, so one
    hallucinated id from the model killed every remaining tool call of the question. The
    rejected value is never echoed (an error message lands in the observation, and a
    caller-supplied uuid must never re-enter the evidence base).

    Raises:
        ToolExecutionError: the argument is missing or not a UUID.
    """
    raw = str(args.get(field) or "").strip()
    if not raw:
        raise ToolExecutionError(f"{field} is required.")
    try:
        return str(UUID(raw))
    except ValueError as exc:
        raise ToolExecutionError(
            f"{field} must be a UUID as returned by a search tool — use an id from a result, "
            "never one you composed."
        ) from exc


def redact_uuids(value: str) -> str:
    """Blank out every uuid in text that ECHOES caller-supplied input back to the model.

    Tool payloads are the evidence base: the citation grader (and any downstream provenance
    check) treats a uuid appearing in an observation as an id a tool RETURNED. Anything the
    caller itself supplied — a search term, a malformed argument, a tool name, an echoed SQL
    statement — must therefore be stripped of uuids before it re-enters the payload, or the
    model can mint its own evidence by passing an invented id and reading it straight back.
    """
    return _UUID_ANYWHERE.sub("<uuid>", value)

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 10
_SNIPPET_CHARS = 240


def _clamp_limit(args: dict[str, Any]) -> int:
    """Read `limit` from args, defaulting and capping server-side."""
    try:
        requested = int(args.get("limit") or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        requested = _DEFAULT_LIMIT
    return max(1, min(requested, _MAX_LIMIT))


def _parse_iso_date(value: Any, field: str) -> date | None:
    """Parse an optional YYYY-MM-DD arg; a malformed value is a tool error the model can fix."""
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        # The rejected value is NOT echoed: an error message lands in the observation, so
        # echoing it would carry a caller-supplied uuid into the evidence base.
        raise ToolExecutionError(f"{field} must be a YYYY-MM-DD date string.") from exc


def _like(term: str) -> str:
    """Wrap a search term for ILIKE containment (escape SQL LIKE metacharacters)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"



_LIMIT_PARAM = {
    "type": "integer",
    "description": f"Max results (default {_DEFAULT_LIMIT}, cap {_MAX_LIMIT}).",
}
