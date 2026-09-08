"""
Role: The database and file halves of LEAK_GROUPS_V1 (contract §7) — the two R6-snapshot reads
      that turn one org's `email_message` / `email_attachment` rows into the row shapes
      `leakage.group_rows` partitions, the EVID_NORM_V1 body digest the template edge is defined
      on, and the emitter that writes `leakage_groups.jsonl` + `leakage.summary.json`.
Used by: tools.mem01_verify.leakage (`compute_leakage_groups` calls the two loaders and re-exports
      `write_leakage` from here); the release `instruments` subcommand; the sealed oracle
      `backend/tests/tools/mem01_verify/test_leakage.py`.
Depends on: tools.mem01_verify.evid_norm (the normalized-body digest of §6); SQLAlchemy Core
      `text()` executed on the caller's snapshot session. Deliberately does NOT import `leakage`
      at run time — the two row shapes arrive as constructors injected by the caller, which keeps
      the pure core and this half acyclic while `leakage` stays the single public surface.
Key invariants:
  - Read-only: SELECTs only, each additionally scoped `WHERE org_id = :org_id` on top of the
    session's tenant binding (security.md layer 3), each ordered by `id` so a re-run of the
    instrument emits byte-identical files (the §13 baseline pair compares their hashes).
  - `normalized_body_sha256` is NULL for a body that is NULL or normalizes to the empty string,
    so two blank bodies are never a template edge.
  - An attachment row with a NULL `content_hash` carries no content identity: it is dropped
    before grouping and never reaches the ubiquity review list.
  - Both files are written UTF-8 with LF line endings on every platform, so the release manifest
    hashes the same bytes on Windows and Linux.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools.mem01_verify import evid_norm

if TYPE_CHECKING:  # the row shapes and the result are annotations only — no runtime import
    from tools.mem01_verify.leakage import (
        AttachmentCarrier,
        EmailNode,
        LeakageGroup,
        LeakageResult,
    )

GROUPS_FILENAME = "leakage_groups.jsonl"
SUMMARY_FILENAME = "leakage.summary.json"

#: The two V1 limitations §7 requires the summary to state in prose.
NEAR_DUPLICATE_STATEMENT = (
    "Near-duplicate clustering is NOT implemented in LEAK_GROUPS_V1. The template edge is exact "
    "EVID_NORM_V1 normalized-body identity only; similarity-based near-duplicate clustering is "
    "deferred to stage B, so two messages that merely resemble each other are not joined here."
)
EDGE_COUNT_STATEMENT = (
    "Edge counts are key-class multiplicities, not distinct email pairs: every shared ancestor "
    "token, attachment content hash or normalized-body digest that joins n emails contributes "
    "n*(n-1)/2 to its kind, so a pair joined by several shared keys is counted once per key."
)

# `references` is a reserved SQL word — it stays quoted. `body_text` is read only to be digested.
_EMAIL_SQL = text(
    """
    SELECT id, message_id, in_reply_to, "references", body_text
    FROM email_message
    WHERE org_id = :org_id
    ORDER BY id
    """
)

_ATTACHMENT_SQL = text(
    """
    SELECT email_id, content_hash, content_type, is_inline, extraction_status
    FROM email_attachment
    WHERE org_id = :org_id AND content_hash IS NOT NULL
    ORDER BY id
    """
)


# ── snapshot reads ────────────────────────────────────────────────────────────────────────


def normalized_body_digest(body_text: str | None) -> str | None:
    """Return the `normalized_body_sha256` of §1.4 for one stored body.

    Contract:
        sha256 (lowercase hex) over the UTF-8 bytes of `evid_norm.normalize(body_text).text` —
        the frozen EVID_NORM_V1 normalization, so typographic variation alone never splits a
        template group.

    Edge cases:
        A NULL body, an empty body and a body of nothing but whitespace or zero-width scalars
        all normalize to the empty string and return `None`, which never joins anything.
    """
    if body_text is None:
        return None
    normalized = evid_norm.normalize(body_text).text
    if not normalized:
        return None
    return sha256(normalized.encode("utf-8")).hexdigest()


async def load_email_nodes(
    conn: AsyncSession, org_id: UUID, email_node: Callable[..., EmailNode]
) -> list[EmailNode]:
    """Read one org's emails from the R6 snapshot `conn` as `leakage.EmailNode` rows.

    Args:
        conn: The caller's `REPEATABLE READ` + `READ ONLY` snapshot session (contract R6).
        org_id: The tenant whose emails are grouped.
        email_node: The `leakage.EmailNode` constructor, injected so this module never imports
            `leakage` at run time.

    Contract:
        Returns every email of the org ordered by `id`. `references` is coalesced from SQL NULL
        to an empty tuple; the body is replaced by its EVID_NORM_V1 digest, so no body text is
        retained past this call (R5 — no personal data can reach a report from these rows).
    """
    rows = (await conn.execute(_EMAIL_SQL, {"org_id": org_id})).all()
    return [
        email_node(
            email_id=row.id,
            message_id=row.message_id,
            in_reply_to=row.in_reply_to,
            references=tuple(row.references or ()),
            normalized_body_sha256=normalized_body_digest(row.body_text),
        )
        for row in rows
    ]


async def load_attachment_carriers(
    conn: AsyncSession, org_id: UUID, attachment_carrier: Callable[..., AttachmentCarrier]
) -> list[AttachmentCarrier]:
    """Read one org's attachment carriers from the R6 snapshot `conn` (metadata only).

    Args:
        conn: The caller's `REPEATABLE READ` + `READ ONLY` snapshot session (contract R6).
        org_id: The tenant whose attachments are read.
        attachment_carrier: The `leakage.AttachmentCarrier` constructor (injected, as above).

    Contract:
        Returns one row per attachment carrying a non-NULL `content_hash`, ordered by `id`.
        Neither the filename nor the extracted text is read: the attachment edge is decided by
        content hash, carrier inlineness and content type alone.
    """
    rows = (await conn.execute(_ATTACHMENT_SQL, {"org_id": org_id})).all()
    return [
        attachment_carrier(
            email_id=row.email_id,
            content_hash=row.content_hash,
            content_type=row.content_type or "",
            is_inline=row.is_inline,
            extraction_status=row.extraction_status,
        )
        for row in rows
    ]


# ── the emitted files (§7) ────────────────────────────────────────────────────────────────


def _group_payload(group: LeakageGroup) -> dict[str, object]:
    """One `leakage_groups.jsonl` line: the members and why they were joined."""
    return {
        "group_id": group.group_id,
        "email_ids": [str(email_id) for email_id in group.email_ids],
        "size": group.size,
        "edge_counts": dict(group.edge_counts),
        "attachment_hashes": list(group.attachment_hashes),
    }


def summary_payload(result: LeakageResult) -> dict[str, object]:
    """The `leakage.summary.json` object: the §7 aggregates plus the two V1 statements.

    `size_histogram` is emitted as a list of `{"size", "groups"}` objects rather than an object
    keyed by size: JSON object keys are strings, so `sort_keys` would order size 10 before
    size 2 and the file would read as if the histogram were unsorted.
    """
    return {
        "version": result.version,
        "constants": dict(result.constants),
        "group_count": len(result.groups),
        "singleton_count": result.singleton_count,
        "size_histogram": [
            {"size": size, "groups": count} for size, count in sorted(result.size_histogram.items())
        ],
        "largest_sizes": list(result.largest_sizes),
        "review_trigger_hashes": dict(result.review_trigger_hashes),
        "designated_boilerplate_applied": list(result.designated_boilerplate_applied),
        "collision_edges": result.collision_edges,
        "sibling_edges": result.sibling_edges,
        "template_edges": result.template_edges,
        "corpus_digest": result.input_corpus_digest,
        "statements": [NEAR_DUPLICATE_STATEMENT, EDGE_COUNT_STATEMENT],
    }


def _write_utf8(path: Path, payload: str) -> None:
    """Write `payload` as UTF-8 with LF line endings (byte-identical on Windows and Linux)."""
    path.write_text(payload, encoding="utf-8", newline="\n")


def write_leakage(result: LeakageResult, out_dir: Path) -> None:
    """Write `leakage_groups.jsonl` and `leakage.summary.json` into `out_dir` (§7).

    Contract:
        One JSON object per group in `group_id` order (the order `group_rows` already returns),
        each carrying `group_id`, `email_ids`, `size`, `edge_counts` and `attachment_hashes`;
        then the summary with the constants, the group count, the size histogram, the ten
        largest sizes, the singleton count, the review-trigger hashes, the applied boilerplate
        designations, the three separately reported edge totals, the input `corpus_digest` and
        the two V1 limitation statements. Creates `out_dir` when it does not exist.

    Edge cases:
        An empty result writes an empty groups file (zero bytes) and a well-formed summary.
        `corpus_digest` is JSON `null` when the result came from the pure `group_rows` path.
        Both files are overwritten wholesale; nothing is appended.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = "".join(
        json.dumps(_group_payload(group), ensure_ascii=False, sort_keys=True) + "\n"
        for group in result.groups
    )
    _write_utf8(out_dir / GROUPS_FILENAME, lines)
    summary = json.dumps(summary_payload(result), ensure_ascii=False, indent=1, sort_keys=True)
    _write_utf8(out_dir / SUMMARY_FILENAME, summary + "\n")
