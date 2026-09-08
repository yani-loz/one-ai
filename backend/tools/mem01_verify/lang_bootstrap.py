"""
Role: LANG_BOOTSTRAP_V1 — the header-only language bootstrap of contract §8. Maps an email's
      `Content-Language` header to one of `bg | en | other | none` for SAMPLING only. The class
      is a hint used to draw a balanced labeling sample; it is NEVER a language label and never
      an expected value for the LANG gate (contract R12).
Used by: the release `instruments` subcommand and the LANG sampling frame (wave 2); the sealed
      oracle `tests/tools/mem01_verify/test_lang_bootstrap.py`.
Depends on: nothing inside the project — the classification is a pure function of the header
      string, and the wave-2 emitter takes the caller's snapshot session, so the only import
      beyond the standard library is SQLAlchemy Core's `text()`.
Key invariants:
  - Only the FIRST comma-separated tag is read; the rest of the header is ignored (§8).
  - The normalized tag is the first tag stripped and lowercased IN FULL (`en-US` → `en-us`);
    the class is its primary subtag (§16.10).
  - An absent, empty or whitespace-only header normalizes to `""` and classifies as `none`
    (§16.14) — the hand-labeling backlog for stage B, never a silent `other`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LANG_BOOTSTRAP_VERSION = "LANG_BOOTSTRAP_V1"

BootstrapClass = Literal["bg", "en", "other", "none"]

#: Every class in the frozen order the summary counts them in; `none` is the labeling backlog.
BOOTSTRAP_CLASSES: tuple[BootstrapClass, ...] = ("bg", "en", "other", "none")

#: Primary subtags that carry their own class; every other non-empty subtag is `other`.
_NAMED_PRIMARY_SUBTAGS: dict[str, BootstrapClass] = {"bg": "bg", "en": "en"}

RECORDS_FILENAME = "lang_bootstrap.jsonl"
SUMMARY_FILENAME = "lang_bootstrap.summary.json"

#: The statement §8 requires the summary to carry, so no reader mistakes a hint for a label.
HINT_STATEMENT = (
    "The Content-Language header is a HINT, not truth: senders set it inconsistently and most "
    "messages carry none at all, so this class is used only to draw a balanced sampling frame. "
    "The language of every sampled message is hand-labeled at stage B, and the LANG gate scores "
    "those labels — never this bootstrap class."
)


def _normalized_header_tag(header_value: str | None) -> str:
    """Return the `header_value_normalized` of §16.10 for a `Content-Language` header.

    Contract:
        Takes the first comma-separated tag, strips surrounding whitespace and lowercases the
        whole tag (so `en-US` becomes `en-us`, `bg-BG, en` becomes `bg-bg`).

    Edge cases:
        `None`, an empty header, a whitespace-only header and a header whose first tag is empty
        (`", en"`) all normalize to the empty string (§16.14).
    """
    if header_value is None:
        return ""
    return header_value.split(",", 1)[0].strip().lower()


def classify_content_language(header_value: str | None) -> BootstrapClass:
    """Classify a `Content-Language` header into the §8 bootstrap class.

    Contract:
        Normalizes with `_normalized_header_tag`, takes the primary subtag before the first
        hyphen, and maps `bg` → `"bg"`, `en` → `"en"`, any other non-empty subtag → `"other"`,
        absent or empty → `"none"`. Never inspects the message body — this is a header hint.

    Edge cases:
        Case and surrounding whitespace are irrelevant (`" BG "` → `"bg"`). Only the first tag
        counts, so `"bg-BG, en"` is `"bg"` and `" en , bg"` is `"en"`. `None`, `""` and `"   "`
        are `"none"`; an unknown or private-use tag such as `"x-klingon"` is `"other"`.
    """
    primary_subtag = _normalized_header_tag(header_value).split("-", 1)[0]
    if not primary_subtag:
        return "none"
    return _NAMED_PRIMARY_SUBTAGS.get(primary_subtag, "other")


# ── wave 2: the per-org emitter ───────────────────────────────────────────────────────────

_LANGUAGE_HEADER_SQL = text(
    """
    SELECT id, headers ->> 'Content-Language' AS content_language
    FROM email_message
    WHERE org_id = :org_id
    ORDER BY id
    """
)


@dataclass(frozen=True)
class LangSummary:
    """What the bootstrap observed for one org: per-class counts, coverage, and its records file."""

    version: str
    counts: Mapping[str, int]
    coverage: float
    records_path: Path


def _summary_payload(summary: LangSummary) -> dict[str, object]:
    """The `lang_bootstrap.summary.json` object — the counts, the coverage and the §8 statement."""
    return {
        "version": summary.version,
        "counts": dict(summary.counts),
        "coverage": summary.coverage,
        "records": summary.records_path.name,
        "statement": HINT_STATEMENT,
    }


def _write_utf8(path: Path, payload: str) -> None:
    """Write `payload` as UTF-8 with LF line endings (byte-identical on Windows and Linux)."""
    path.write_text(payload, encoding="utf-8", newline="\n")


def _emit_language_files(out_dir: Path, records: Sequence[Mapping[str, str]]) -> LangSummary:
    """Write §8's two files for one org's classified records and return the summary.

    Kept synchronous so every filesystem call of this module sits outside the event loop's
    coroutine (the blocking-call lint the async emitter would otherwise trip).
    """
    counts: dict[str, int] = dict.fromkeys(BOOTSTRAP_CLASSES, 0)
    for record in records:
        counts[record["bootstrap_class"]] += 1
    covered = counts["bg"] + counts["en"] + counts["other"]
    out_dir.mkdir(parents=True, exist_ok=True)
    records_path = out_dir / RECORDS_FILENAME
    lines = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records)
    _write_utf8(records_path, lines)
    summary = LangSummary(
        version=LANG_BOOTSTRAP_VERSION,
        counts=counts,
        coverage=covered / len(records) if records else 0.0,
        records_path=records_path,
    )
    payload = json.dumps(_summary_payload(summary), ensure_ascii=False, indent=1, sort_keys=True)
    _write_utf8(out_dir / SUMMARY_FILENAME, payload + "\n")
    return summary


async def bootstrap_language(conn: AsyncSession, org_id: UUID, out_dir: Path) -> LangSummary:
    """Classify one org's `Content-Language` headers from the R6 snapshot and emit §8's files.

    Args:
        conn: The caller's `REPEATABLE READ` + `READ ONLY` snapshot session (contract R6).
        org_id: The tenant whose emails are classified.
        out_dir: Directory the two files are written into; created when it does not exist.

    Contract:
        Reads `email_message.headers ->> 'Content-Language'` for `org_id` ordered by email id,
        writes one `lang_bootstrap.jsonl` record per email (`email_id`, `header_value_normalized`,
        `bootstrap_class`) and one `lang_bootstrap.summary.json` carrying the version, the counts
        for all four classes, the coverage and the "hint, not truth" statement, and returns the
        summary. Reads no body and no subject; writes nothing to the database.

    Edge cases:
        `coverage` = (bg + en + other) / all emails as plain float division at full precision;
        an org with no emails yields zero counts and a coverage of `0.0`. A header that is
        absent, empty or whitespace-only normalizes to `""` and classifies as `none` (§16.14).
    """
    rows = (await conn.execute(_LANGUAGE_HEADER_SQL, {"org_id": org_id})).all()
    records = [
        {
            "email_id": str(row.id),
            "header_value_normalized": _normalized_header_tag(row.content_language),
            "bootstrap_class": classify_content_language(row.content_language),
        }
        for row in rows
    ]
    return _emit_language_files(out_dir, records)
