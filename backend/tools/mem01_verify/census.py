"""
Role: CENSUS_V1 (contract §9, §16.5) — the versioned census of one org: it executes every metric
      of the catalogue against the R6 snapshot session, shapes the results into the emitted JSON
      value types, derives each metric's gate-denominator roles, reconciles the numbers the
      MEM-01 documents quote against the measured ones, and writes `census.json`.
Used by: `python -m tools.mem01_verify.release instruments --draft [--only census]`, the §13
      baseline pair, gate diagnostics, and the sealed oracle
      backend/tests/tools/mem01_verify/test_census.py.
Depends on: tools.mem01_verify.census_metrics (the metric catalogue),
      .census_denominators (the per-gate denominator table),
      .corpus_identity (`corpus_digest` — the observation's identity, §5.2), .db
      (`read_alembic_version`, §16.15 — the migration ledger the snapshot plane holds no
      privilege on), .exceptions (`CensusError`); SQLAlchemy Core `text()` on the caller's
      session. It opens no corpus connection of its own.
Key invariants:
  - READ ONLY. Every statement is a `SELECT` executed on the caller's snapshot session; the
    census never commits, rolls back, or writes to the measured database.
  - The emitted metric set is EXACTLY the §16.5 key list — no more, no fewer — and each metric
    carries the SQL that produced it, so `census.json` is re-runnable by hand.
  - Ordering is imposed HERE, not in SQL: distributions are sorted count descending then key
    ascending as Python strings, so the file is byte-identical regardless of the server's
    collation, and a replay over an unchanged org reproduces every value.
  - A failing metric aborts the whole census (`CensusError`): a partially measured census would
    be a silently shrunken denominator (rule R2).
  - `denominators_by_gate` is the single source of truth; every `Metric.denominator_for` is
    DERIVED from it, so the table and the per-metric roles cannot disagree.
  - No personal data is measured or emitted: counts, hashes, lengths, statuses, MIME types,
    header KEYS and file extensions only (rule R5).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tools.mem01_verify import corpus_identity, db
from tools.mem01_verify.census_denominators import (
    CENSUS_GATES,
    DENOMINATORS_BY_GATE,
    NOT_YET_LABELED,
)
from tools.mem01_verify.census_metrics import (
    CENSUS_FILENAME,
    CENSUS_METRICS,
    CENSUS_VERSION,
    MetricSpec,
)
from tools.mem01_verify.exceptions import CensusError

__all__ = ["CENSUS_VERSION", "Census", "Metric", "take_census", "write_census"]

# The numbers the MEM-01 documents quote about the dev corpus (§9). Exact counts must match
# exactly; the percentage matches when the measured ratio, rounded to the quoted precision,
# renders as the quoted string.
QUOTED_EMAILS_TOTAL = 5893
QUOTED_ATTACHMENTS_TOTAL = 8454
QUOTED_ATTACHMENTS_WITH_TEXT = 2850
QUOTED_ATTACHMENTS_TRUNCATED = 5
QUOTED_UNSUPPORTED_DOCUMENTS = 88
QUOTED_LANGUAGE_HEADER_COVERAGE = "74%"

# The five document formats the quoted `88` sums over (msword, pptx, ms-excel, odt, ods),
# matched as substrings of the stored `content_type` so a vendor prefix cannot hide one.
UNSUPPORTED_DOCUMENT_FORMAT_TOKENS: tuple[str, ...] = (
    "msword",
    "presentationml",
    "ms-excel",
    "opendocument.text",
    "opendocument.spreadsheet",
)


@dataclass(frozen=True)
class Metric:
    """One measured metric: its value, the statement behind it, its gate roles and its unit."""

    # `object` because the emitted value is deliberately polymorphic (§1.4): an int, a float, a
    # string, or a list of `{key, count}` / `{status, content_type, count}` objects.
    value: object
    sql: str
    denominator_for: tuple[str, ...]
    unit: str


@dataclass(frozen=True)
class Census:
    """One CENSUS_V1 observation of one org, identified by the corpus digest it was taken on."""

    version: str
    corpus_digest: str
    taken_at: datetime
    org_id: UUID
    metrics: Mapping[str, Metric]
    denominators_by_gate: Mapping[str, Mapping[str, str]]
    docs_reconciliation: tuple[Mapping[str, object], ...]


def _label(value: object) -> str:
    """Render a distribution key as a string; a NULL group becomes the literal `(null)`."""
    return "(null)" if value is None else str(value)


def _scalar(value: object) -> object:
    """Normalise a scalar result: `numeric` arrives as `Decimal`, which is not JSON-serializable."""
    return int(value) if isinstance(value, Decimal) else value


def _distribution_rows(rows: Sequence[Sequence[object]]) -> list[dict[str, object]]:
    """Shape `(key, count)` rows into the §16.5 ordered `{key, count}` list."""
    entries = [{"key": _label(row[0]), "count": int(row[1])} for row in rows]
    entries.sort(key=lambda entry: (-int(entry["count"]), str(entry["key"])))
    return entries


def _named_rows(spec: MetricSpec, rows: Sequence[Sequence[object]]) -> list[dict[str, object]]:
    """Shape multi-column rows into objects with exactly `spec.columns`, count descending.

    The tie-break runs over the remaining columns in declared order, so the list is TOTALLY
    ordered and a replay reproduces it byte for byte.
    """
    records = [
        {
            name: int(value) if name == "count" else _label(value)
            for name, value in zip(spec.columns, row, strict=True)
        }
        for row in rows
    ]
    labels = spec.columns[:-1]
    records.sort(key=lambda item: (-int(item["count"]), *(str(item[name]) for name in labels)))
    return records


async def _measure(conn: AsyncSession, spec: MetricSpec, org_id: UUID) -> object:
    """Execute one metric statement and shape its result.

    Raises:
        CensusError: the statement failed — the census is abandoned rather than emitted with a
            hole in it (R2).
    """
    parameters = {"org_id": str(org_id)} if ":org_id" in spec.sql else {}
    try:
        result = await conn.execute(text(spec.sql), parameters)
        if spec.shape == "scalar":
            return _scalar(result.scalar_one())
        rows = [tuple(row) for row in result.all()]
    except SQLAlchemyError as error:
        raise CensusError(f"census metric {spec.key!r} could not be measured: {error}") from error
    return _distribution_rows(rows) if spec.shape == "distribution" else _named_rows(spec, rows)


def _validate_denominator_table(known_metrics: frozenset[str]) -> None:
    """Refuse a denominator table that names an unknown gate or an unknown metric.

    The mechanical guard behind the "single source of truth" invariant: a metric key renamed in
    the catalogue without updating the table fails the census instead of silently emitting a
    gate role that points at nothing.

    Raises:
        CensusError: a gate is missing or extra, a gate has no denominators, or a value is
            neither a catalogue metric key nor `not_yet_labeled`.
    """
    if set(DENOMINATORS_BY_GATE) != set(CENSUS_GATES):
        raise CensusError("the denominator table does not cover exactly the seventeen gates")
    for gate, denominators in DENOMINATORS_BY_GATE.items():
        if not denominators:
            raise CensusError(f"gate {gate} declares no denominators")
        unknown = {
            name
            for name in denominators.values()
            if name != NOT_YET_LABELED and name not in known_metrics
        }
        if unknown:
            raise CensusError(f"gate {gate} names unknown census metrics: {sorted(unknown)}")


def _denominator_roles() -> dict[str, tuple[str, ...]]:
    """Invert the denominator table: metric key -> the gates it is a denominator for."""
    roles: dict[str, list[str]] = {}
    for gate in CENSUS_GATES:
        for metric_key in DENOMINATORS_BY_GATE[gate].values():
            gates = roles.setdefault(metric_key, [])
            if metric_key != NOT_YET_LABELED and gate not in gates:
                gates.append(gate)
    return {key: tuple(gates) for key, gates in roles.items()}


def _exact_entry(label: str, quoted: int, measured: int) -> dict[str, object]:
    """A docs-reconciliation entry for a quoted exact count (§9: it must match exactly)."""
    return {"label": label, "quoted": quoted, "measured": measured, "match": quoted == measured}


def _percentage_entry(
    label: str, quoted: str, numerator: int, denominator: int
) -> dict[str, object]:
    """A docs-reconciliation entry for a quoted percentage (§9: match at the quoted precision).

    `74.3%` matches a quoted `74%`; a zero denominator is reported as unmeasured and never as a
    match (R2 — an empty denominator is not evidence of agreement).
    """
    if denominator <= 0:
        return {"label": label, "quoted": quoted, "measured": None, "match": False, "ratio": None}
    ratio = numerator / denominator
    decimals = len(quoted.rstrip("%").partition(".")[2])
    measured = f"{ratio * 100:.{decimals}f}%"
    return {
        "label": label,
        "quoted": quoted,
        "measured": measured,
        "match": measured == quoted,
        "ratio": ratio,
    }


def _unsupported_document_total(status_by_content_type: object) -> int:
    """Sum the `unsupported_format` attachments carried by the five quoted document formats."""
    if not isinstance(status_by_content_type, list):
        return 0
    return sum(
        int(row["count"])
        for row in status_by_content_type
        if row.get("status") == "unsupported_format"
        and any(
            token in str(row.get("content_type", ""))
            for token in UNSUPPORTED_DOCUMENT_FORMAT_TOKENS
        )
    )


def _reconcile_docs(values: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Compare every number the MEM-01 documents quote with the measured value (§9).

    Args:
        values: the measured metric values, keyed by §16.5 metric key.

    Returns:
        One entry per quoted number, each carrying `quoted`, `measured` and a boolean `match`.
    """
    emails_total = int(values["emails_total"])  # type: ignore[call-overload]
    return (
        _exact_entry("emails_total", QUOTED_EMAILS_TOTAL, int(values["emails_total"])),  # type: ignore[call-overload]
        _exact_entry(
            "attachments_total",
            QUOTED_ATTACHMENTS_TOTAL,
            int(values["attachments_total"]),  # type: ignore[call-overload]
        ),
        _exact_entry(
            "attachments_with_text",
            QUOTED_ATTACHMENTS_WITH_TEXT,
            int(values["attachments_with_text"]),  # type: ignore[call-overload]
        ),
        _exact_entry(
            "attachments_truncated",
            QUOTED_ATTACHMENTS_TRUNCATED,
            int(values["attachments_text_beyond_extraction_cap"]),  # type: ignore[call-overload]
        ),
        _exact_entry(
            "unsupported_document_formats",
            QUOTED_UNSUPPORTED_DOCUMENTS,
            _unsupported_document_total(values["attachments_status_by_content_type"]),
        ),
        _percentage_entry(
            "content_language_header_coverage",
            QUOTED_LANGUAGE_HEADER_COVERAGE,
            int(values["emails_content_language_present"]),  # type: ignore[call-overload]
            emails_total,
        ),
    )


async def take_census(conn: AsyncSession, org_id: UUID) -> Census:
    """Measure the CENSUS_V1 metric set for `org_id` through the caller's R6 snapshot session.

    Every metric of §16.5 is executed against the same snapshot, so the whole census describes
    ONE database state — identified by the `corpus_digest` of §5.2, which is taken on the same
    session. The migration ledger is the single exception: `oneai_app` holds no privilege on
    `alembic_version` (migration 0013), so it is read through `db.read_alembic_version` in its
    own READ ONLY transaction on the global role (§16.15).

    Args:
        conn: an open `readonly_corpus_snapshot` session; neither committed nor rolled back here.
        org_id: the tenant to measure.

    Returns:
        The `Census` — version, corpus digest, timestamp, org, metrics, the per-gate denominator
        table and the documents reconciliation.

    Raises:
        CensusError: a metric statement failed, the migration ledger was unreadable, or the
            denominator table names something the catalogue does not define.
    """
    _validate_denominator_table(frozenset(spec.key for spec in CENSUS_METRICS))
    identity = await corpus_identity.corpus_digest(conn, org_id)
    try:
        database = str((await conn.execute(text("SELECT current_database()"))).scalar_one())
        revision = await db.read_alembic_version(database)
    except SQLAlchemyError as error:
        raise CensusError(f"the census could not identify its database: {error}") from error

    values: dict[str, object] = {}
    for spec in CENSUS_METRICS:
        values[spec.key] = (
            revision if spec.key == "schema_alembic_version" else await _measure(conn, spec, org_id)
        )

    roles = _denominator_roles()
    metrics = {
        spec.key: Metric(
            value=values[spec.key],
            sql=spec.sql,
            denominator_for=roles.get(spec.key, ()),
            unit=spec.unit,
        )
        for spec in CENSUS_METRICS
    }
    return Census(
        version=CENSUS_VERSION,
        corpus_digest=identity.corpus_digest,
        taken_at=datetime.now(UTC),
        org_id=org_id,
        metrics=metrics,
        denominators_by_gate={gate: dict(DENOMINATORS_BY_GATE[gate]) for gate in CENSUS_GATES},
        docs_reconciliation=_reconcile_docs(values),
    )


def write_census(census: Census, out_dir: Path) -> Path:
    """Write `census.json` under `out_dir` and return its path (§9).

    The file carries `census_version`, `corpus_digest`, `taken_at`, `org_id`, every metric with
    its SQL, the denominator table and the documents reconciliation — everything needed to
    re-run the observation by hand. Written as UTF-8 bytes with LF endings so a replay over an
    unchanged corpus hashes identically on every platform.

    Args:
        census: the census to serialize.
        out_dir: the directory to write into (created if absent).

    Returns:
        The path of the written `census.json`.

    Raises:
        CensusError: the file could not be written or a metric value is not JSON-serializable.
    """
    document = {
        "census_version": census.version,
        "corpus_digest": census.corpus_digest,
        "taken_at": census.taken_at.isoformat(),
        "org_id": str(census.org_id),
        "metrics": {
            key: {
                "value": metric.value,
                "sql": metric.sql,
                "denominator_for": list(metric.denominator_for),
                "unit": metric.unit,
            }
            for key, metric in census.metrics.items()
        },
        "denominators_by_gate": {
            gate: dict(denominators) for gate, denominators in census.denominators_by_gate.items()
        },
        "docs_reconciliation": [dict(entry) for entry in census.docs_reconciliation],
    }
    path = out_dir / CENSUS_FILENAME
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
        path.write_bytes(payload.encode("utf-8"))
    except (OSError, TypeError, ValueError) as error:
        raise CensusError(f"cannot write the census to {path}: {error}") from error
    return path
