"""
Role: search_attachments + get_attachment — the document layer. Attachments are searched by
      filename/extracted text/format family; get_attachment pages through one document's
      extracted text, where money, terms and deadlines actually live.
Used by: app.ask.tools.shared_core (registry assembly).
Depends on: app.ask.tools.email_filters (participant terms), app.ask.tools.tool_helpers,
      app.ask.tools.registry.
Key invariants:
  - Format names are MIME FAMILIES on purpose ('word' matches modern AND legacy Word), and
    the filename-suffix fallback catches mislabeled files.
  - Embedded inline images (signatures/logos) are excluded — counts here are documents.
  - Same anti-fabrication and absence-honesty rules as the email tools: not-found envelopes
    never echo the requested id, and a zero-result search says what zero means.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.email_filters import _participant_terms
from app.ask.tools.registry import ToolSpec
from app.ask.tools.tool_helpers import (
    _LIMIT_PARAM,
    _SNIPPET_CHARS,
    _clamp_limit,
    _like,
    parse_id_arg,
)

# ASK-02 F6: common format names do not appear in their official MIME strings (docx MIME is
# application/vnd.openxmlformats-officedocument.wordprocessingml.document — no 'docx'), so a
# naive ILIKE matched 0 of the corpus's Word files. Standard-format -> MIME-family patterns.
_MIME_FAMILIES: dict[str, list[str]] = {
    "docx": ["%wordprocessingml%", "%msword%"],
    "doc": ["%wordprocessingml%", "%msword%"],
    "word": ["%wordprocessingml%", "%msword%"],
    "xlsx": ["%spreadsheetml%", "%ms-excel%"],
    "xls": ["%spreadsheetml%", "%ms-excel%"],
    "excel": ["%spreadsheetml%", "%ms-excel%"],
    "pptx": ["%presentationml%", "%ms-powerpoint%"],
    "ppt": ["%presentationml%", "%ms-powerpoint%"],
    "pdf": ["%pdf%"],
    "image": ["image/%"],
    "zip": ["%zip%", "%compressed%"],
}


async def _search_attachments(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Search attachments by filename/extracted text and/or content-type, newest first."""
    query = str(args.get("query") or "").strip() or None
    content_type = str(args.get("content_type") or "").strip() or None
    # Derived ONCE: both were recomputed three and two times respectively, and a filter that is
    # re-derived is a filter that can drift between the count, the pattern list and the envelope.
    participants = _participant_terms(args)
    limit = _clamp_limit(args)
    if not query and not content_type and not participants:
        raise ToolExecutionError("Provide at least one of: query, content_type, participants.")
    normalized_type = content_type.lower().lstrip(".") if content_type else None
    if normalized_type:
        # content_type_ext_like feeds a raw LIKE — LIKE metacharacters must not pass through
        # unescaped, so restrict the extension to safe chars rather than escaping. All
        # _MIME_FAMILIES keys are alphanumeric, so the family lookup below is unaffected.
        normalized_type = re.sub(r"[^a-z0-9]", "", normalized_type) or None
    mime_patterns = _MIME_FAMILIES.get(normalized_type) if normalized_type else None
    rows = await session.execute(
        text(
            f"""
            SELECT a.id, a.email_id, a.filename, a.content_type, a.size_bytes,
                   left(coalesce(a.extracted_text, ''), {_SNIPPET_CHARS}) AS text_snippet,
                   m.sent_at, m.from_address, m.subject
            FROM email_attachment a
            JOIN email_message m ON m.id = a.email_id AND m.org_id = a.org_id
            WHERE a.is_inline = false
              AND (CAST(:query AS text) IS NULL
                   OR a.filename ILIKE :query_like OR a.extracted_text ILIKE :query_like)
              AND (CAST(:content_type AS text) IS NULL
                   OR a.content_type ILIKE ANY(CAST(:content_type_likes AS text[]))
                   OR a.filename ILIKE :content_type_ext_like)
              AND (CAST(:att_participants_count AS int) = 0
                   OR m.from_address ILIKE ANY(CAST(:att_participant_likes AS text[]))
                   OR m.from_name ILIKE ANY(CAST(:att_participant_likes AS text[])))
            ORDER BY m.sent_at DESC NULLS LAST
            LIMIT :limit
            """
        ),
        {
            "query": query,
            "query_like": _like(query) if query else None,
            "content_type": content_type,
            "content_type_likes": (
                mime_patterns or ([_like(content_type)] if content_type else [""])
            ),
            "content_type_ext_like": f"%.{normalized_type}" if normalized_type else "",
            "att_participants_count": len(participants),
            "att_participant_likes": [_like(term) for term in participants] or [""],
            "limit": limit,
        },
    )
    results = [dict(r) for r in rows.mappings()]
    # Control fields first, bulky `results` last — same envelope-order contract as
    # search_emails, so budget truncation can only ever cost rows, never the flags.
    att_envelope: dict[str, Any] = {"listing_complete": bool(results) and len(results) < limit}
    if results:
        att_envelope["listing"] = [
            f"{str(r['sent_at'])[:10]} · {r['from_address']} · {r['filename']} · [id: {r['id']}]"
            for r in results
        ]
    else:
        # Same absence-honesty rule as search_emails: no hits under THESE filters is not
        # verified absence of the document (format families, language, inline filtering).
        att_envelope["listing_note"] = (
            "0 attachments matched THESE filters — absence of matches, not verified absence "
            "of the document. Embedded signature images are excluded by design; retry with "
            "a different content_type/format, translated filename keywords, or a participant "
            "filter, and search the carrying emails before concluding it does not exist."
        )
    att_envelope["results"] = results
    return att_envelope


_ATTACHMENT_TEXT_CAP = 5000


async def _get_attachment(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Fetch one attachment's extracted document text (capped) by attachment id."""
    attachment_id = parse_id_arg(args, "attachment_id")
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT a.id, a.filename, a.content_type, a.size_bytes, a.extraction_status,
                       a.extracted_text, m.sent_at, m.from_address, m.subject, a.email_id
                FROM email_attachment a
                JOIN email_message m ON m.id = a.email_id AND m.org_id = a.org_id
                WHERE a.id = CAST(:attachment_id AS uuid)
                """
                ),
                {"attachment_id": attachment_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        # Same anti-fabrication rule as _get_email: never echo the requested id back.
        return {
            "found": False,
            "note": "No attachment with the requested id is visible to you.",
        }
    extracted = row["extracted_text"] or ""
    result = {k: v for k, v in dict(row).items() if k != "extracted_text"}
    result["found"] = True
    offset = max(0, int(args.get("offset") or 0))
    result["text"] = extracted[offset : offset + _ATTACHMENT_TEXT_CAP]
    result["total_chars"] = len(extracted)
    if offset:
        result["offset"] = offset
    if offset + _ATTACHMENT_TEXT_CAP < len(extracted):
        result["next_offset"] = offset + _ATTACHMENT_TEXT_CAP
        result["note_truncated"] = (
            "Document continues — call get_attachment again with offset=next_offset. "
            "Totals, payment schedules, and signatures are usually near the END."
        )
    if not extracted:
        result["note"] = (
            f"No extracted text available (extraction_status={row['extraction_status']}) — "
            "the document content cannot be read."
        )
    return result


SEARCH_ATTACHMENTS_SPEC = ToolSpec(
    name="search_attachments",
    description=(
        "Search email attachments by filename or extracted document text, "
        "optionally filtered by content type (e.g. 'pdf'). Returns the file "
        "info plus the sender, date, and subject of the carrying email. The "
        "listing field enumerates the matches with their ids; when "
        "listing_complete is false more matches exist beyond this page — say so "
        "rather than presenting the page as the full set. Embedded signature "
        "images and logos are excluded, so counts here are documents, not every "
        "attached file. Read a document's text with get_attachment."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Filename fragment or document keyword.",
            },
            "participants": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Sender names/addresses/domains — restricts to attachments "
                    "on emails FROM these people (use for 'documents X sent us')."
                ),
            },
            "content_type": {
                "type": "string",
                "description": (
                    "Content-type or format fragment, e.g. 'pdf', 'docx', "
                    "'zip'. Format names match their whole MIME FAMILY — "
                    "'docx'/'doc'/'word' all match modern AND legacy Word "
                    "files (deliberate: 'do we have Word docs about X')."
                ),
            },
            "limit": _LIMIT_PARAM,
        },
        "required": [],
    },
    executor=_search_attachments,
)

GET_ATTACHMENT_SPEC = ToolSpec(
    name="get_attachment",
    description=(
        "Read the extracted document text of ONE attachment by its id (from "
        "search_attachments or get_email results). Facts about money, terms, "
        "agreements, and deadlines usually live INSIDE attached documents "
        "(offers, invoices, contracts) — email bodies only reference them. "
        "Long documents are paged: when next_offset is returned, read the REST "
        "of the document too before answering — contract totals and payment "
        "schedules usually sit near the END. When reporting terms from a "
        "contract or agreement, ALWAYS also name the PARTIES (who signed with "
        "whom — stated in the document's OPENING page), not just the numbers."
    ),
    parameters={
        "type": "object",
        "properties": {
            "attachment_id": {
                "type": "string",
                "description": "The attachment id (UUID).",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset to continue reading from "
                "(use next_offset from the previous call).",
            },
        },
        "required": ["attachment_id"],
    },
    executor=_get_attachment,
)
