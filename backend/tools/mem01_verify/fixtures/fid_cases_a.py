"""
Role: The FID (extraction-fidelity) fixture VOCABULARY — the frozen record types every
      `fid_cases*` battery module emits, plus the synthetic-ORIGINAL document spec
      (`DocSpec`/`TableSpec`) and the single derivation `expectation_for_doc` that turns one
      ORIGINAL into its independently specified expectation. The spec IS the original: the
      payload builders render it to bytes and this module derives what must survive, so neither
      side ever consults an extractor (Stage-A contract R12, section 10.5).
Used by: `fid_cases.py` (re-exports `FidCase` and the expectation types on the public surface),
         the ORIGINAL library `fid_cases_b.py`, and the battery modules `fid_cases_c.py`
         (pdf, docx), `fid_cases_d.py` (xlsx, tnef), `fid_cases_e.py` (html, rtf, plain text) and
         `fid_cases_f.py` (encoding / Unicode). The FID gate evaluator consumes `FidCase.expected`
         against the STORED `extracted_text`.
Depends on: stdlib only (`dataclasses`, `typing`). Imports nothing from the project — in
            particular nothing from `backend/app/` and nothing from `backend/tests/`.
Key invariants:
  - Every expectation is checked against ONE flat string: the stored `extracted_text` exactly as
    the ingest path stores it (post-sanitize, post-mask). No intermediate object is in scope.
  - REPRESENTATION RULES, frozen here because the criterion sheet freezes "whitespace
    alternatives" without naming them:
      (1) TABLE = ROW-MAJOR. The header cells appear in column order, before any data row; each
          data row's cells appear in column order with no cell of another row of the same table
          between them (`RowGroup`). A key-value or column-major render is a DEFECT, not an
          accepted alternative — that is what "value under a foreign header" means for a flat
          string.
      (2) LAYOUT COLUMNS ARE NOT TABLE ROWS. A multi-column page layout constrains only relative
          order (`ordered_sequences`); it never gets a `RowGroup`.
      (2a) ORDER IS GREEDY FORWARD MATCHING, never "compare first occurrences". An
          `ordered_sequences` entry is satisfied iff the units can be matched left to right, each
          one at or after the end of the previous match: scan for unit 1 from position 0, then for
          unit 2 from the end of unit 1's match, and so on. First-occurrence comparison would
          reject a correct extraction whenever a later unit's literal also appears earlier (a
          repeated cell value, a header word inside a caption), which is a property of the
          document, not a defect. The same rule applies inside a `RowGroup`, whose matched span
          runs from the start of its first cell's match to the end of its last.
      (3) LINK = label then destination, both literal, within `LinkPair.max_gap_scalars` Unicode
          scalars of each other, label first.
      (4) NEGATION = `NegationGuard`: every occurrence of `stem` in the stored text must be
          immediately preceded by `required_prefix`. Losing "НЕ"/"NOT" is a defect even though
          every other scalar survived.
      (5) WHITESPACE ALTERNATIVES (frozen): U+00A0 and U+202F may render as U+0020; a run of
          spaces/tabs may collapse to a single space; CRLF may render as LF. Every accepted
          variant is spelled out per unit in `FidUnit.alternatives` — an evaluator accepts a unit
          iff `text` OR one of `alternatives` occurs. DELETING the separator (a collapsed
          "1 250,00" -> "1250,00") is never an alternative and is pinned via `forbidden`.
  - `FidUnit.text` and every `alternatives` entry are literal Unicode scalar sequences taken from
    the ORIGINAL, never from any extractor output.
  - Records are frozen and hashable: tuples only, never lists; `expected` is a dataclass, never a
    mapping.
  - No real corpus text, no real person, no live secret. Addresses only under `example.test`,
    `acme.test`, `partner.test`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FidFormat = Literal["pdf", "docx", "xlsx", "tnef", "html", "rtf", "text"]
UnitKind = Literal[
    "paragraph",
    "list_item",
    "table_caption",
    "table_header",
    "table_cell",
    "link_label",
    "layout_block",
]

#: The criterion every record in this battery answers to (loop-facing, F evidence only).
FID_CRITERION = "fid.provisional"

#: Frozen whitespace alternatives (representation rule 5).
NBSP = "\u00a0"
NARROW_NBSP = "\u202f"

#: Signatures of the two failure modes an encoding case must never exhibit: the Unicode
#: replacement character, and the classic "UTF-8 bytes read as a single-byte codepage" mojibake
#: lead scalars. The synthetic ORIGINALS contain none of these, so forbidding them is a property
#: of the original, not of any decoder.
MOJIBAKE_MARKERS: tuple[str, ...] = ("\ufffd", "Ã", "Ð", "Â")

#: An initial byte-order mark is a signature, not content (Unicode 23.8), so it must not survive
#: into the stored text.
BOM_SCALAR = "\ufeff"

#: Frozen negation prefixes. A cell that opens with one of these carries a negation whose loss is
#: a fidelity defect even when every other scalar survives (criterion: "lost negations").
NEGATION_PREFIXES: tuple[str, ...] = ("НЕ ", "NOT ")


@dataclass(frozen=True, slots=True)
class FidUnit:
    """One required unit of the ORIGINAL that must survive into the stored `extracted_text`.

    Args:
        unit_id: unique within its case; referenced by order and row constraints.
        kind: which taxonomy entry this unit is.
        text: the literal scalar sequence that must occur in the stored text.
        alternatives: frozen accepted renderings of the SAME unit (representation rule 5).
        sheet: workbook sheet or logical table name (`None` outside tables).
        row: 1-based row index inside its table (`0` for the header row).
        column: 1-based column index inside its table.
        header: the column header this value belongs under — the association a column swap breaks.
        page: 1-based page (pdf) or sheet ordinal (xlsx) the unit is painted on.
        layout_column: 1-based reading-order column of a multi-column page layout.
    """

    unit_id: str
    kind: UnitKind
    text: str
    alternatives: tuple[str, ...] = ()
    sheet: str | None = None
    row: int | None = None
    column: int | None = None
    header: str | None = None
    page: int | None = None
    layout_column: int | None = None


@dataclass(frozen=True, slots=True)
class LinkPair:
    """A link label -> destination pair of the ORIGINAL (representation rule 3)."""

    label: str
    destination: str
    max_gap_scalars: int = 400


@dataclass(frozen=True, slots=True)
class RowGroup:
    """One table row's cells in column order, with the cells that must not interleave.

    Args:
        unit_ids: the row's cell units, in column order (representation rule 1) — matched greedily
            forward (representation rule 2a).
        excluded_unit_ids: units of OTHER rows of the same table; none of their literals may occur
            inside this row's matched span (its first cell's match start to its last cell's end).
    """

    unit_ids: tuple[str, ...]
    excluded_unit_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NegationGuard:
    """Every occurrence of `stem` in the stored text must be immediately preceded by the prefix."""

    stem: str
    required_prefix: str


@dataclass(frozen=True, slots=True)
class FidExpectation:
    """What must be true of the stored `extracted_text` for one fixture.

    Args:
        units: every required unit; a missing or altered one is a fidelity defect.
        ordered_sequences: each entry lists unit ids that must be matchable left to right by
            GREEDY FORWARD MATCHING (representation rule 2a) — document order, page order,
            layout-column reading order, sheet order.
        row_groups: table row integrity (representation rule 1).
        link_pairs: label -> destination pairs (representation rule 3).
        negation_guards: representation rule 4.
        forbidden: scalar sequences that must NOT occur (mojibake signatures, a surviving BOM, a
            separator-collapsed number, an invented unit).
        taxonomy_complete: True iff this case carries the FULL unit taxonomy of contract 10.5
            (ordered paragraphs/list items, link label->destination pairs, page/column order and
            table tuples with header association).
    """

    units: tuple[FidUnit, ...]
    ordered_sequences: tuple[tuple[str, ...], ...] = ()
    row_groups: tuple[RowGroup, ...] = ()
    link_pairs: tuple[LinkPair, ...] = ()
    negation_guards: tuple[NegationGuard, ...] = ()
    forbidden: tuple[str, ...] = ()
    taxonomy_complete: bool = False


@dataclass(frozen=True, slots=True)
class FidCase:
    """One extraction-fidelity fixture: a synthetic ORIGINAL and what must survive it.

    Args:
        case_id: unique `fid-NNN` id across the whole battery.
        criterion_id: always `fid.provisional` (the F-only loop-facing criterion).
        origin: the rule this case pins, in words — never a code path.
        format: the format family the ORIGINAL is authored in.
        content_type: the declared media type the ingest path dispatches on.
        payload: the ORIGINAL's bytes, exactly as they would ride as an attachment.
        expected: the independently specified expectation.
    """

    case_id: str
    criterion_id: str
    origin: str
    format: FidFormat
    content_type: str
    payload: bytes
    expected: FidExpectation


@dataclass(frozen=True, slots=True)
class TableSpec:
    """A table of the ORIGINAL: a caption, a header row and data rows, all as literal strings."""

    caption: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    sheet: str = "Sheet1"


@dataclass(frozen=True)
class DocSpec:
    """A synthetic ORIGINAL document, rendered by every format builder in the SAME order.

    Render order is part of the specification: paragraphs, then list items, then each table
    (caption, header row, data rows), then one paragraph per link carrying its label, then the
    trailing paragraphs. `alternatives` maps a literal unit text to its frozen accepted
    renderings (representation rule 5).
    """

    paragraphs: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    tables: tuple[TableSpec, ...] = ()
    links: tuple[LinkPair, ...] = ()
    trailing: tuple[str, ...] = ()
    alternatives: dict[str, tuple[str, ...]] = field(default_factory=dict)


def negation_guards_for(value: str) -> tuple[NegationGuard, ...]:
    """Derive the negation guards a cell value carries (representation rule 4).

    Args:
        value: one cell or paragraph literal of the ORIGINAL.

    Returns:
        One guard per frozen negation prefix the value opens with; empty when it opens with none.
    """
    return tuple(
        NegationGuard(stem=value[len(prefix) :], required_prefix=prefix)
        for prefix in NEGATION_PREFIXES
        if value.startswith(prefix) and len(value) > len(prefix)
    )


def _paragraph_units(spec: DocSpec) -> tuple[list[FidUnit], list[str]]:
    """Units and flow ids for the spec's paragraphs and list items, in render order."""
    units: list[FidUnit] = []
    flow: list[str] = []
    for index, text in enumerate(spec.paragraphs, start=1):
        units.append(FidUnit(f"p{index}", "paragraph", text, spec.alternatives.get(text, ())))
        flow.append(f"p{index}")
    for index, text in enumerate(spec.bullets, start=1):
        units.append(FidUnit(f"b{index}", "list_item", text, spec.alternatives.get(text, ())))
        flow.append(f"b{index}")
    return units, flow


def _table_units(
    spec: DocSpec, table: TableSpec, prefix: str
) -> tuple[list[FidUnit], list[str], list[tuple[str, ...]], list[RowGroup], list[NegationGuard]]:
    """Units, flow ids, order sequences, row groups and negation guards for one table."""
    units = [FidUnit(f"{prefix}cap", "table_caption", table.caption, sheet=table.sheet)]
    flow = [f"{prefix}cap"]
    header_ids: list[str] = []
    for column, header in enumerate(table.headers, start=1):
        unit_id = f"{prefix}h{column}"
        units.append(
            FidUnit(
                unit_id,
                "table_header",
                header,
                spec.alternatives.get(header, ()),
                sheet=table.sheet,
                row=0,
                column=column,
            )
        )
        header_ids.append(unit_id)
        flow.append(unit_id)
    sequences: list[tuple[str, ...]] = [tuple(header_ids)]
    all_cell_ids = [
        f"{prefix}r{r}c{c}"
        for r in range(1, len(table.rows) + 1)
        for c in range(1, len(table.headers) + 1)
    ]
    row_groups: list[RowGroup] = []
    guards: list[NegationGuard] = []
    for row_index, row in enumerate(table.rows, start=1):
        row_ids: list[str] = []
        for column, value in enumerate(row, start=1):
            unit_id = f"{prefix}r{row_index}c{column}"
            units.append(
                FidUnit(
                    unit_id,
                    "table_cell",
                    value,
                    spec.alternatives.get(value, ()),
                    sheet=table.sheet,
                    row=row_index,
                    column=column,
                    header=table.headers[column - 1],
                )
            )
            row_ids.append(unit_id)
            flow.append(unit_id)
            guards.extend(negation_guards_for(value))
        row_groups.append(
            RowGroup(tuple(row_ids), tuple(i for i in all_cell_ids if i not in row_ids))
        )
        sequences.append((header_ids[0], row_ids[0]))
    return units, flow, sequences, row_groups, guards


def expectation_for_doc(
    spec: DocSpec,
    *,
    taxonomy_complete: bool = False,
    forbidden: tuple[str, ...] = (),
    extra_sequences: tuple[tuple[str, ...], ...] = (),
) -> FidExpectation:
    """Derive the expectation of one synthetic ORIGINAL — from the spec, never from an extractor.

    Emits one unit per paragraph, list item, table caption, header cell, data cell, link label and
    trailing paragraph; the document-order sequence over all of them; a per-table header-order
    sequence and a `RowGroup` per data row (representation rule 1); the link pairs; and a
    `NegationGuard` for every cell whose text opens with a frozen negation prefix.

    Args:
        spec: the ORIGINAL.
        taxonomy_complete: mark this case as carrying the full unit taxonomy of contract 10.5.
        forbidden: extra scalar sequences that must not occur in the stored text.
        extra_sequences: additional order constraints (page order, layout-column reading order).

    Returns:
        The frozen expectation for this ORIGINAL.
    """
    units, flow = _paragraph_units(spec)
    sequences: list[tuple[str, ...]] = []
    row_groups: list[RowGroup] = []
    guards: list[NegationGuard] = []
    for table_index, table in enumerate(spec.tables, start=1):
        t_units, t_flow, t_seq, t_rows, t_guards = _table_units(spec, table, f"t{table_index}")
        units.extend(t_units)
        flow.extend(t_flow)
        sequences.extend(t_seq)
        row_groups.extend(t_rows)
        guards.extend(t_guards)
    for index, link in enumerate(spec.links, start=1):
        units.append(FidUnit(f"l{index}", "link_label", link.label))
        flow.append(f"l{index}")
    for index, text in enumerate(spec.trailing, start=1):
        units.append(FidUnit(f"z{index}", "paragraph", text, spec.alternatives.get(text, ())))
        flow.append(f"z{index}")
        guards.extend(negation_guards_for(text))
    if len(flow) > 1:
        sequences.insert(0, tuple(flow))
    sequences.extend(extra_sequences)
    return FidExpectation(
        units=tuple(units),
        ordered_sequences=tuple(dict.fromkeys(sequences)),
        row_groups=tuple(row_groups),
        link_pairs=spec.links,
        negation_guards=tuple(dict.fromkeys(guards)),
        forbidden=forbidden,
        taxonomy_complete=taxonomy_complete,
    )


def fid_case(
    case_id: str,
    origin: str,
    fid_format: FidFormat,
    content_type: str,
    payload: bytes,
    expected: FidExpectation,
) -> FidCase:
    """Build one FID fixture record, stamping the battery's single criterion id.

    Args:
        case_id: unique `fid-NNN` id.
        origin: the rule this case pins, in words.
        fid_format: the format family of the ORIGINAL.
        content_type: the declared media type the ingest path dispatches on.
        payload: the ORIGINAL's bytes.
        expected: the independently specified expectation.

    Returns:
        The frozen `FidCase`.
    """
    return FidCase(case_id, FID_CRITERION, origin, fid_format, content_type, payload, expected)
