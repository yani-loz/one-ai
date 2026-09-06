"""
Role: find_person — identity lookup over the person graph: matches by name/address, and
      surfaces what the exact-email resolver misses (unlinked sender addresses, company
      domains, and same-person domain-migration candidates).
Used by: app.ask.tools.shared_core (registry assembly).
Depends on: app.ask.tools.tool_helpers, app.ask.tools.registry, SQLAlchemy Core.
Key invariants:
  - Runs on the caller's READER-plane session: org RLS + person visibility apply server-side.
  - same_person_candidates are UNVERIFIED (sender names are spoofable) — the tool never
    merges identities, it only reports candidates with the identity_note attached.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.registry import ToolSpec
from app.ask.tools.tool_helpers import _LIMIT_PARAM, _clamp_limit, _like


async def _find_person(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """Match persons by name/address; include their latest signature block + unlinked addresses.

    The signature excerpt comes from the person's most recent NON-REPLY authored message
    (replies end in quoted history, not the author's signature) — it carries the
    role/title/phone facts that exist nowhere else. `unlinked_addresses` surfaces sender
    addresses matching the term that are NOT bound to any person row (the identity graph
    under-merges) — search those as participants too for complete coverage.
    `same_person_candidates` (per person) + `identity_note` surface a POSSIBLE domain migration:
    a sender address sharing a linked local-part AND the full display name on another domain,
    which the exact-email resolver splits into two identities — an UNVERIFIED candidate (sender
    names are spoofable) the model must confirm from message content before merging.
    """
    term = str(args.get("name_or_email") or "").strip()
    if not term:
        raise ToolExecutionError("name_or_email is required.")
    rows = await session.execute(
        text(
            """
            SELECT p.id, p.display_name, p.is_internal,
                   seen.first_seen, seen.last_seen,
                   array_remove(array_agg(DISTINCT pe.email), NULL) AS addresses,
                   (SELECT right(coalesce(m.body_text, ''), 400)
                    FROM email_message m
                    WHERE m.org_id = p.org_id
                      AND m.from_address IN (
                          SELECT pe3.email FROM person_email pe3
                          WHERE pe3.person_id = p.id AND pe3.org_id = p.org_id)
                    ORDER BY m.is_reply ASC, m.sent_at DESC NULLS LAST
                    LIMIT 1) AS signature_block
            FROM person p
            LEFT JOIN person_email pe ON pe.person_id = p.id AND pe.org_id = p.org_id
            -- The contact window is recomputed from the messages THIS CALLER can see, never
            -- read from person.first_seen_at/last_seen_at: those columns are maintained on the
            -- write plane over EVERY ingested message, and `person` carries org isolation only
            -- (the PF-01 per-person policy covers the content tables). Serving them let a
            -- colleague learn the date of correspondence they hold no grant for — while
            -- search_emails, in the same session, certified that message does not exist.
            LEFT JOIN LATERAL (
                SELECT min(vm.sent_at)::date AS first_seen,
                       max(vm.sent_at)::date AS last_seen
                FROM email_message vm
                WHERE vm.org_id = p.org_id
                  AND (vm.from_address IN (
                           SELECT pe4.email FROM person_email pe4
                           WHERE pe4.person_id = p.id AND pe4.org_id = p.org_id)
                       OR EXISTS (
                           SELECT 1 FROM email_recipient vr
                           WHERE vr.email_id = vm.id AND vr.org_id = vm.org_id
                             -- Same BCC rule as get_email and the participant filters: a
                             -- contact WINDOW computed over blind-copy rows is an oracle too
                             -- — first_seen/last_seen would shift for an address only ever
                             -- blind-copied, answering the question BCC exists to hide.
                             AND vr.kind IN ('to', 'cc')
                             AND vr.address IN (
                                 SELECT pe5.email FROM person_email pe5
                                 WHERE pe5.person_id = p.id AND pe5.org_id = p.org_id)))
            ) seen ON true
            WHERE p.display_name ILIKE :term OR EXISTS (
                  SELECT 1 FROM person_email pe2
                  WHERE pe2.person_id = p.id AND pe2.org_id = p.org_id
                    AND pe2.email ILIKE :term)
            GROUP BY p.id, p.org_id, p.display_name, p.is_internal,
                     seen.first_seen, seen.last_seen
            ORDER BY seen.last_seen DESC NULLS LAST
            LIMIT :limit
            """
        ),
        {"term": _like(term), "limit": _clamp_limit(args)},
    )
    persons = [dict(r) for r in rows.mappings()]
    linked = {a for person in persons for a in (person.get("addresses") or [])}
    unlinked = await session.execute(
        text(
            """
            SELECT DISTINCT lower(from_address) AS address
            FROM email_message
            WHERE org_id = current_setting('app.current_org_id', true)::uuid
              AND (from_address ILIKE :term OR from_name ILIKE :term)
            LIMIT 10
            """
        ),
        {"term": _like(term)},
    )
    extra = [r["address"] for r in unlinked.mappings() if r["address"] not in linked]
    # Company names rarely literal-match address strings (e.g. 'Acme+' vs acme-corp.com) —
    # normalize to alphanumerics and scan sender DOMAINS so enumeration can be fed even
    # when no person row matches.
    normalized = re.sub(r"[^a-z0-9]", "", term.lower())
    domains: list[str] = []
    if len(normalized) >= 3:
        domain_rows = await session.execute(
            text(
                """
                SELECT DISTINCT lower(split_part(from_address, '@', 2)) AS domain
                FROM email_message
                WHERE org_id = current_setting('app.current_org_id', true)::uuid
                  AND regexp_replace(lower(split_part(from_address, '@', 2)),
                                     '[^a-z0-9]', '', 'g') LIKE :norm_like
                LIMIT 5
                """
            ),
            {"norm_like": f"%{normalized}%"},
        )
        domains = [r["domain"] for r in domain_rows.mappings()]
    # Same-person alias candidates (identity law): the resolver merges only on an EXACT
    # normalized email, so a person who changed domains (same mailbox local-part + same full
    # display name on a NEW domain) splits into two identities. For each matched person,
    # surface sender addresses that share BOTH a linked local-part AND the full display name —
    # never auto-merge; the model (and later the HITL tier) decides. A merely shared name
    # TOKEN is a DIFFERENT person, so the match requires the WHOLE display name to be equal.
    any_candidates = False
    # Bounded to the top 5 matched persons: the candidate query runs once PER person, so an
    # unbounded scan over up to 50 matches is an N+1 fan-out. RLS still gates every row.
    for person in persons[:5]:
        addresses = person.get("addresses") or []
        display_name = str(person.get("display_name") or "").strip()
        # Guard len>=4: a short local-part ('jd', 'hr') is too common to assert identity on.
        local_parts = sorted(
            {
                addr.split("@", 1)[0].lower()
                for addr in addresses
                if "@" in addr and len(addr.split("@", 1)[0]) >= 4
            }
        )
        if not local_parts or not display_name:
            continue
        linked_lower = [addr.lower() for addr in addresses]
        candidate_rows = await session.execute(
            text(
                """
                SELECT DISTINCT lower(m.from_address) AS address
                FROM email_message m
                WHERE m.org_id = current_setting('app.current_org_id', true)::uuid
                  AND m.from_address IS NOT NULL
                  AND split_part(lower(m.from_address), '@', 1)
                      = ANY(CAST(:local_parts AS text[]))
                  AND lower(m.from_name) = lower(:display_name)
                  AND lower(m.from_address) <> ALL(CAST(:linked AS text[]))
                LIMIT 5
                """
            ),
            {"local_parts": local_parts, "display_name": display_name, "linked": linked_lower},
        )
        candidates = [r["address"] for r in candidate_rows.mappings()]
        if candidates:
            person["same_person_candidates"] = candidates
            any_candidates = True
    result: dict[str, Any] = {
        "persons": persons,
        "unlinked_addresses": extra,
        "matching_domains": domains,
    }
    if any_candidates:
        result["identity_note"] = (
            "CANDIDATE only — same local-part and same full display name on another domain "
            "often means one person who changed domains, but sender names are unverified and "
            "can be spoofed by lookalike domains. VERIFY from message content (signatures, "
            "thread continuity) before treating the addresses as one person; a shared first "
            "or last name alone is a DIFFERENT person."
        )
    return result


FIND_PERSON_SPEC = ToolSpec(
    name="find_person",
    description=(
        "Look up people by name or email address (case-insensitive, partial "
        "matches). Returns each match with all known email addresses, whether "
        "they are internal, first/last time seen, and a signature_block from "
        "their own emails — READ the signature_block: it states the person's "
        "job title, role, company, and phone, which you should include when "
        "describing who someone is. Also check unlinked_addresses for the "
        "person's other addresses not yet linked to their record. For COMPANIES, "
        "matching_domains lists their email domains — pass those to "
        "search_emails participants to enumerate the whole relationship. If "
        "same_person_candidates is present, the person MAY also use another "
        "address on a different domain (a domain migration) — per identity_note "
        "these are UNVERIFIED candidates from spoofable sender names, so confirm "
        "from message content before treating them as the same person."
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
)
