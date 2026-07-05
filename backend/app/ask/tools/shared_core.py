"""
Role: The v0 shared-core retrieval tools — five generic primitives every Ask specialist gets:
      find_person, search_emails, get_email, search_attachments, count_emails. These are the
      monolithic BASELINE tool set of the ask-tools loop; iterations mutate/extend from here.
Used by: app.ask.services.agent_runner via build_shared_core_registry(); the ask_loop harness.
Depends on: app.ask.tools.registry, SQLAlchemy Core (text) against the Connect content tables.
Key invariants:
  - Every query runs on the caller's READER-plane session: org RLS + person visibility policies
    apply server-side; the org_id/person GUCs are already bound — tools add NO tenant logic.
  - Generic by construction: no entity names, domains, folder names, or date literals from any
    question set may appear here (loop anti-bias rule; verifier-audited).
  - Result payloads are compact JSON (ids, ISO dates, truncated snippets) — token economy for
    a small reader; full bodies only via get_email.
  - LIMITs are capped at _MAX_LIMIT server-side regardless of what the model asks for.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.registry import ToolRegistry, ToolSpec

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
        raise ToolExecutionError(f"{field} must be YYYY-MM-DD, got {value!r}.") from exc


def _like(term: str) -> str:
    """Wrap a search term for ILIKE containment (escape SQL LIKE metacharacters)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _find_person(session: AsyncSession, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Match persons by display name or any bound email address (case-insensitive contains)."""
    term = str(args.get("name_or_email") or "").strip()
    if not term:
        raise ToolExecutionError("name_or_email is required.")
    rows = await session.execute(
        text(
            """
            SELECT p.id, p.display_name, p.is_internal,
                   p.first_seen_at::date AS first_seen, p.last_seen_at::date AS last_seen,
                   array_remove(array_agg(DISTINCT pe.email), NULL) AS addresses
            FROM person p
            LEFT JOIN person_email pe ON pe.person_id = p.id AND pe.org_id = p.org_id
            WHERE p.display_name ILIKE :term OR EXISTS (
                  SELECT 1 FROM person_email pe2
                  WHERE pe2.person_id = p.id AND pe2.org_id = p.org_id
                    AND pe2.email ILIKE :term)
            GROUP BY p.id, p.display_name, p.is_internal, p.first_seen_at, p.last_seen_at
            ORDER BY p.last_seen_at DESC NULLS LAST
            LIMIT :limit
            """
        ),
        {"term": _like(term), "limit": _clamp_limit(args)},
    )
    return [dict(r) for r in rows.mappings()]


_MAX_QUERY_TERMS = 5

_EMAIL_FILTERS = """
      (CAST(:terms_count AS int) = 0
       OR m.subject ILIKE ANY(CAST(:term_likes AS text[]))
       OR m.body_text ILIKE ANY(CAST(:term_likes AS text[])))
  AND (CAST(:participant AS text) IS NULL
       OR m.from_address ILIKE :participant_like OR m.from_name ILIKE :participant_like
       OR EXISTS (SELECT 1 FROM email_recipient r
                  WHERE r.email_id = m.id AND r.org_id = m.org_id
                    AND (r.address ILIKE :participant_like OR r.name ILIKE :participant_like)))
  AND (CAST(:date_from AS date) IS NULL OR m.sent_at >= CAST(:date_from AS date))
  AND (CAST(:date_to AS date) IS NULL
       OR m.sent_at < CAST(:date_to AS date) + INTERVAL '1 day')
"""


def _query_terms(args: dict[str, Any]) -> list[str]:
    """Collect keyword variants from `queries` (list) or legacy `query` (string)."""
    raw = args.get("queries")
    if raw is None:
        raw = [args.get("query")] if args.get("query") else []
    if not isinstance(raw, list):
        raw = [raw]
    terms = [str(t).strip() for t in raw if t and str(t).strip()]
    return terms[:_MAX_QUERY_TERMS]


def _email_filter_params(args: dict[str, Any]) -> dict[str, Any]:
    """Shared param builder for search_emails/count_emails (identical filter semantics)."""
    terms = _query_terms(args)
    participant = str(args.get("participant") or "").strip() or None
    return {
        "terms_count": len(terms),
        "term_likes": [_like(t) for t in terms] or [""],
        "participant": participant,
        "participant_like": _like(participant) if participant else None,
        "date_from": _parse_iso_date(args.get("date_from"), "date_from"),
        "date_to": _parse_iso_date(args.get("date_to"), "date_to"),
    }


async def _search_emails(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Search messages by keyword variants and/or participant and/or date window, newest first."""
    params = _email_filter_params(args)
    if not params["terms_count"] and not params["participant"] and not params["date_from"]:
        raise ToolExecutionError("Provide at least one of: queries, participant, date_from.")
    rows = await session.execute(
        text(
            f"""
            SELECT m.id, m.sent_at, m.from_address, m.from_name, m.subject,
                   m.direction, m.has_attachments,
                   left(coalesce(m.body_text, ''), {_SNIPPET_CHARS}) AS snippet,
                   (m.subject ILIKE ANY(CAST(:term_likes AS text[]))) AS subject_hit
            FROM email_message m
            WHERE {_EMAIL_FILTERS}
            ORDER BY (m.subject ILIKE ANY(CAST(:term_likes AS text[]))) DESC,
                     m.sent_at DESC NULLS LAST
            LIMIT :limit
            """
        ),
        {**params, "limit": _clamp_limit(args)},
    )
    results = [dict(r) for r in rows.mappings()]
    total = int(
        (
            await session.execute(
                text(f"SELECT count(*) FROM email_message m WHERE {_EMAIL_FILTERS}"), params
            )
        ).scalar_one()
    )
    envelope: dict[str, Any] = {"total_matches": total, "results": results}
    if params["terms_count"]:
        envelope["per_term_matches"] = await _per_term_counts(session, args)
    if total > len(results):
        envelope["hint"] = (
            f"Showing {len(results)} of {total} matches (subject hits first, then newest). "
            "Narrow with date_from/date_to, participant, or more specific terms to reach "
            "the rest."
        )
    if not results and params["terms_count"]:
        envelope["hint"] = (
            "No matches for these terms. The archive content may be in another language — "
            "retry with translated keywords and transliterated names before concluding "
            "no data exists."
        )
    return envelope


async def _per_term_counts(session: AsyncSession, args: dict[str, Any]) -> dict[str, int]:
    """Per-variant match counts (same non-keyword filters) — which term actually hits."""
    counts: dict[str, int] = {}
    for term in _query_terms(args):
        single = _email_filter_params({**args, "queries": [term], "query": None})
        counts[term] = int(
            (
                await session.execute(
                    text(f"SELECT count(*) FROM email_message m WHERE {_EMAIL_FILTERS}"), single
                )
            ).scalar_one()
        )
    return counts


async def _count_emails(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Count messages matching the same filters as search_emails (never hand-count rows)."""
    params = _email_filter_params(args)
    result = await session.execute(
        text(f"SELECT count(*) AS matches FROM email_message m WHERE {_EMAIL_FILTERS}"),
        params,
    )
    envelope: dict[str, Any] = {"matches": int(result.scalar_one())}
    if params["terms_count"] > 1:
        envelope["per_term_matches"] = await _per_term_counts(session, args)
    return envelope


async def _get_email(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Fetch one full message (body, recipients, attachment list) by its id."""
    email_id = str(args.get("email_id") or "").strip()
    if not email_id:
        raise ToolExecutionError("email_id is required.")
    message = (
        await session.execute(
            text(
                """
                SELECT m.id, m.sent_at, m.from_address, m.from_name, m.subject,
                       m.direction, m.is_reply, m.body_text
                FROM email_message m WHERE m.id = CAST(:email_id AS uuid)
                """
            ),
            {"email_id": email_id},
        )
    ).mappings().first()
    if message is None:
        return {"found": False, "email_id": email_id}
    recipients = await session.execute(
        text(
            """
            SELECT kind, name, address FROM email_recipient
            WHERE email_id = CAST(:email_id AS uuid) ORDER BY kind, address
            """
        ),
        {"email_id": email_id},
    )
    attachments = await session.execute(
        text(
            """
            SELECT id, filename, content_type, size_bytes, extraction_status
            FROM email_attachment WHERE email_id = CAST(:email_id AS uuid) ORDER BY filename
            """
        ),
        {"email_id": email_id},
    )
    return {
        "found": True,
        **dict(message),
        "recipients": [dict(r) for r in recipients.mappings()],
        "attachments": [dict(r) for r in attachments.mappings()],
    }


async def _search_attachments(session: AsyncSession, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Search attachments by filename/extracted text and/or content-type, newest first."""
    query = str(args.get("query") or "").strip() or None
    content_type = str(args.get("content_type") or "").strip() or None
    if not query and not content_type:
        raise ToolExecutionError("Provide at least one of: query, content_type.")
    rows = await session.execute(
        text(
            """
            SELECT a.id, a.email_id, a.filename, a.content_type, a.size_bytes,
                   m.sent_at, m.from_address, m.subject
            FROM email_attachment a
            JOIN email_message m ON m.id = a.email_id AND m.org_id = a.org_id
            WHERE a.is_inline = false
              AND (CAST(:query AS text) IS NULL
                   OR a.filename ILIKE :query_like OR a.extracted_text ILIKE :query_like)
              AND (CAST(:content_type AS text) IS NULL
                   OR a.content_type ILIKE :content_type_like)
            ORDER BY m.sent_at DESC NULLS LAST
            LIMIT :limit
            """
        ),
        {
            "query": query,
            "query_like": _like(query) if query else None,
            "content_type": content_type,
            "content_type_like": _like(content_type) if content_type else None,
            "limit": _clamp_limit(args),
        },
    )
    return [dict(r) for r in rows.mappings()]


async def _get_counterparty_summary(
    session: AsyncSession, args: dict[str, Any]
) -> list[dict[str, Any]]:
    """One-hop relationship dossier per counterparty domain (derived view, citable ids)."""
    term = str(args.get("name_or_domain") or "").strip().lower()
    if not term:
        raise ToolExecutionError("name_or_domain is required.")
    if "@" in term:
        term = term.split("@", 1)[1]
    summaries = (
        await session.execute(
            text(
                """
                SELECT domain, first_contact, last_contact,
                       first_message_id, last_message_id,
                       inbound_count, outbound_count, total_mentions, distinct_addresses
                FROM counterparty_summary
                WHERE domain ILIKE :term_like
                ORDER BY total_mentions DESC
                LIMIT 3
                """
            ),
            {"term_like": _like(term)},
        )
    ).mappings().all()
    results = []
    for row in summaries:
        top = (
            await session.execute(
                text(
                    """
                    SELECT lower(coalesce(from_address, '')) AS address, count(*) AS messages
                    FROM email_message
                    WHERE from_address ILIKE :domain_like
                    GROUP BY 1 ORDER BY messages DESC LIMIT 5
                    """
                ),
                {"domain_like": f"%@{row['domain']}"},
            )
        ).mappings().all()
        results.append({**dict(row), "top_senders": [dict(t) for t in top]})
    return results


_LIMIT_PARAM = {
    "type": "integer",
    "description": f"Max results (default {_DEFAULT_LIMIT}, cap {_MAX_LIMIT}).",
}


def build_shared_core_registry() -> ToolRegistry:
    """Assemble the v0 baseline registry (the loop's parent configuration)."""
    return ToolRegistry(
        [
            ToolSpec(
                name="find_person",
                description=(
                    "Look up people by name or email address (case-insensitive, partial "
                    "matches). Returns each match with all known email addresses, whether "
                    "they are internal, and first/last time seen in communications."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name_or_email": {
                            "type": "string",
                            "description": "Name fragment or email address to look up.",
                        },
                        "limit": _LIMIT_PARAM,
                    },
                    "required": ["name_or_email"],
                },
                executor=_find_person,
            ),
            ToolSpec(
                name="search_emails",
                description=(
                    "Search email messages by keyword variants (subject+body), participant "
                    "(sender or recipient name/address), and/or date window. ALWAYS pass "
                    "several keyword variants together — the original term plus its "
                    "translations into the archive's likely languages and transliterations "
                    "of names — results match ANY variant. Returns newest-first message "
                    "summaries with a short body snippet; use get_email for full messages."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Up to 5 keyword variants (term + translations + "
                                "transliterations); a message matches if ANY variant matches."
                            ),
                        },
                        "participant": {
                            "type": "string",
                            "description": "Name or address of a sender/recipient.",
                        },
                        "date_from": {"type": "string", "description": "YYYY-MM-DD inclusive."},
                        "date_to": {"type": "string", "description": "YYYY-MM-DD inclusive."},
                        "limit": _LIMIT_PARAM,
                    },
                    "required": [],
                },
                executor=_search_emails,
            ),
            ToolSpec(
                name="count_emails",
                description=(
                    "Count email messages matching the same filters as search_emails "
                    "(keyword variants, participant, date window). Use this for any "
                    "'how many' question instead of counting search results yourself."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Up to 5 keyword variants (term + translations + "
                                "transliterations); a message matches if ANY variant matches."
                            ),
                        },
                        "participant": {
                            "type": "string",
                            "description": "Name or address of a sender/recipient.",
                        },
                        "date_from": {"type": "string", "description": "YYYY-MM-DD inclusive."},
                        "date_to": {"type": "string", "description": "YYYY-MM-DD inclusive."},
                    },
                    "required": [],
                },
                executor=_count_emails,
            ),
            ToolSpec(
                name="get_email",
                description=(
                    "Fetch one email in full by id: complete body text, all recipients "
                    "(to/cc), and the list of attachments with types and sizes."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "email_id": {"type": "string", "description": "The message id (UUID)."}
                    },
                    "required": ["email_id"],
                },
                executor=_get_email,
            ),
            ToolSpec(
                name="search_attachments",
                description=(
                    "Search email attachments by filename or extracted document text, "
                    "optionally filtered by content type (e.g. 'pdf'). Returns the file "
                    "info plus the sender, date, and subject of the carrying email."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Filename fragment or document keyword.",
                        },
                        "content_type": {
                            "type": "string",
                            "description": "Content-type fragment, e.g. 'pdf' or 'zip'.",
                        },
                        "limit": _LIMIT_PARAM,
                    },
                    "required": [],
                },
                executor=_search_attachments,
            ),
            ToolSpec(
                name="get_counterparty_summary",
                description=(
                    "Call this FIRST for any question about the history, relationship, "
                    "status, or overview of a company/organization/counterparty: returns "
                    "their communication dossier — first and last contact dates (with "
                    "first_message_id/last_message_id to CITE as evidence), inbound/"
                    "outbound volumes, and top sender addresses — in one call. Then drill "
                    "into specifics with search_emails."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name_or_domain": {
                            "type": "string",
                            "description": (
                                "Company name fragment, email domain, or address "
                                "(e.g. 'acme' or 'acme.com')."
                            ),
                        }
                    },
                    "required": ["name_or_domain"],
                },
                executor=_get_counterparty_summary,
            ),
        ]
    )
