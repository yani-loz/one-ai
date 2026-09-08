"""
Role: The XLSX and TNEF halves of the FID fixture battery — `fid-027..039` (xlsx) and
      `fid-040..052` (tnef). Each case renders one synthetic ORIGINAL from `fid_cases_b` and
      pairs it with the expectation derived from that same ORIGINAL (contract R12, section 10.5).
      The TNEF half is the CONTAINER case the contract asks for: a tnef stream embedding a
      document that itself carries tables, links and a page boundary.
Used by: `fid_cases.py` (concatenates every half into `build_fid_cases()`).
Depends on: `fid_cases_a` (record vocabulary, `expectation_for_doc`, `fid_case`), `fid_cases_b`
            (the ORIGINAL library and `boundary_sequence`), `fid_builders` (the PDF assembler),
            `fid_builders_b` (the OOXML assemblers), `fid_builders_c` (the TNEF assembler and the
            rtf/html/text renderings). Nothing from `backend/app/` or `backend/tests/`.
Key invariants:
  - For a WORKBOOK the "page order" unit of the taxonomy is SHEET ORDER: the opening narrative
    sheet, then one sheet per table, then the closing sheet with the link labels. Three xlsx cases
    carry the full taxonomy, and their sheet boundary is stated explicitly through
    `boundary_sequence`.
  - Every xlsx link is a REAL cell hyperlink relationship — the destination is never typed into
    the cell text, so a handoff that cannot carry it fails the pair honestly rather than passing
    on an accident.
  - READING ORDER OF A CONTAINER (frozen here): a TNEF container reads as its BODY first, then its
    embedded files in container order. The embedded files' own internal order is the embedded
    document's order. A case never constrains the order of units taken from two different embedded
    files against each other except by that container order.
  - Three TNEF cases carry the full taxonomy through ONE embedded .docx (paragraphs and list items
    in order, a real hyperlink relationship, a page break, table tuples with header association),
    so the container's fidelity is measured on a document that has something to lose.
  - Two of the three full-taxonomy TNEF cases are Bulgarian and one is English; the pdf, xlsx and
    plain-text embeddings cover the remaining formats a container carries in practice.
"""

from __future__ import annotations

from dataclasses import replace

from tools.mem01_verify.fixtures.fid_builders import pdf_from_doc
from tools.mem01_verify.fixtures.fid_builders_b import build_docx, xlsx_from_doc
from tools.mem01_verify.fixtures.fid_builders_c import (
    build_tnef,
    html_from_doc,
    rtf_from_doc,
    text_from_doc,
)
from tools.mem01_verify.fixtures.fid_cases_a import (
    DocSpec,
    FidCase,
    FidExpectation,
    expectation_for_doc,
    fid_case,
)
from tools.mem01_verify.fixtures.fid_cases_b import (
    COLLAPSED_AMOUNTS,
    CYRILLIC_ORIGINALS,
    EN_INVOICE,
    EN_LINKS,
    EN_LIST_ORDER,
    EN_NUMBERS,
    EN_REPORT,
    LATIN_ORIGINALS,
    boundary_sequence,
)

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TNEF_CONTENT_TYPE = "application/ms-tnef"

#: The file names embedded documents ride under inside a TNEF container (synthetic, ASCII).
_EMBEDDED_DOCX = b"document.docx"
_EMBEDDED_PDF = b"document.pdf"
_EMBEDDED_XLSX = b"workbook.xlsx"
_EMBEDDED_TXT = b"notes.txt"


def _taxonomy_expectation(spec: DocSpec) -> FidExpectation:
    """The full-taxonomy expectation of an ORIGINAL split at a real page or sheet boundary."""
    return expectation_for_doc(
        spec,
        taxonomy_complete=True,
        forbidden=COLLAPSED_AMOUNTS,
        extra_sequences=(boundary_sequence(spec),),
    )


def _plain_expectation(spec: DocSpec) -> FidExpectation:
    """The expectation of an ORIGINAL rendered as one continuous flow."""
    return expectation_for_doc(spec, forbidden=COLLAPSED_AMOUNTS)


def _prefixed(expectation: FidExpectation, prefix: str) -> FidExpectation:
    """Re-key one expectation's unit ids so two documents can share one container's expectation.

    A container case carries units from more than one ORIGINAL; the ids of the two derivations
    would otherwise collide. Only the ids move — no unit text, order or association changes.

    Args:
        expectation: the derivation of one embedded or body ORIGINAL.
        prefix: the container part this derivation came from (e.g. "body", "att1").

    Returns:
        The same expectation with every unit id prefixed.
    """
    rename = {unit.unit_id: f"{prefix}:{unit.unit_id}" for unit in expectation.units}
    return replace(
        expectation,
        units=tuple(replace(unit, unit_id=rename[unit.unit_id]) for unit in expectation.units),
        ordered_sequences=tuple(
            tuple(rename[unit_id] for unit_id in sequence)
            for sequence in expectation.ordered_sequences
        ),
        row_groups=tuple(
            replace(
                group,
                unit_ids=tuple(rename[i] for i in group.unit_ids),
                excluded_unit_ids=tuple(rename[i] for i in group.excluded_unit_ids),
            )
            for group in expectation.row_groups
        ),
    )


def _container_expectation(parts: tuple[tuple[str, FidExpectation], ...]) -> FidExpectation:
    """Merge the derivations of a container's parts, in the container's reading order.

    Every part keeps its own units, order constraints, row groups and link pairs; the parts are
    ordered against each other only by the container order (body first, then the embedded files),
    which is expressed as the sequence of the parts' first units.
    """
    merged = tuple(_prefixed(expectation, name) for name, expectation in parts)
    across = tuple(part.units[0].unit_id for part in merged if part.units)
    sequences = tuple(sequence for part in merged for sequence in part.ordered_sequences)
    return FidExpectation(
        units=tuple(unit for part in merged for unit in part.units),
        ordered_sequences=(across, *sequences) if len(across) > 1 else sequences,
        row_groups=tuple(group for part in merged for group in part.row_groups),
        link_pairs=tuple(link for part in merged for link in part.link_pairs),
        negation_guards=tuple(
            dict.fromkeys(guard for part in merged for guard in part.negation_guards)
        ),
        forbidden=tuple(dict.fromkeys(item for part in merged for item in part.forbidden)),
    )


def build_xlsx_cases() -> tuple[FidCase, ...]:
    """Build the xlsx half of the battery (`fid-027` .. `fid-039`).

    Three full-taxonomy workbooks (two Bulgarian, one English) whose sheet order carries the
    boundary, then ten single-flow workbooks over the remaining ORIGINALS.

    Returns:
        The xlsx cases in `case_id` order.
    """
    taxonomy = (
        ("фактура на кирилица: лист Разчети след началния лист", CYRILLIC_ORIGINALS[0][1]),
        ("протокол на кирилица с отрицание в клетка на отделен лист", CYRILLIC_ORIGINALS[1][1]),
        ("English invoice workbook: opening sheet, Settlement sheet, closing sheet", EN_INVOICE),
    )
    cases: list[FidCase] = []
    for index, (origin, spec) in enumerate(taxonomy, start=27):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                "full unit taxonomy in xlsx — "
                f"{origin}: ordered narrative cells, a real cell hyperlink, sheet order as page "
                "order, and table tuples (sheet, row, column, value, header)",
                "xlsx",
                XLSX_CONTENT_TYPE,
                xlsx_from_doc(spec),
                _taxonomy_expectation(spec),
            )
        )
    remaining = [*CYRILLIC_ORIGINALS[2:], *LATIN_ORIGINALS[1:]]
    for index, (origin, spec) in enumerate(remaining[:10], start=30):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                f"xlsx workbook — {origin}",
                "xlsx",
                XLSX_CONTENT_TYPE,
                xlsx_from_doc(spec),
                _plain_expectation(spec),
            )
        )
    return tuple(cases)


def _tnef_taxonomy_cases() -> list[FidCase]:
    """Three TNEF containers, each embedding ONE full-taxonomy .docx (2 Bulgarian, 1 English)."""
    specs = (
        ("вградена фактура .docx на кирилица", CYRILLIC_ORIGINALS[0][1]),
        ("вграден протокол .docx с отрицание в таблицата", CYRILLIC_ORIGINALS[1][1]),
        ("embedded English invoice .docx", EN_INVOICE),
    )
    cases: list[FidCase] = []
    for index, (origin, spec) in enumerate(specs, start=40):
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                "full unit taxonomy inside a TNEF container — "
                f"{origin}: the container carries no body, so every required unit must come out "
                "of the embedded document, page boundary and header association included",
                "tnef",
                TNEF_CONTENT_TYPE,
                build_tnef(
                    attachments=((_EMBEDDED_DOCX, build_docx(spec, page_break_before_tables=True)),)
                ),
                _taxonomy_expectation(spec),
            )
        )
    return cases


def _tnef_body_cases() -> list[FidCase]:
    """Containers whose content rides in the BODY layer (compressed RTF, HTML, plain text)."""
    return [
        fid_case(
            "fid-043",
            "TNEF compressed-RTF body: a Bulgarian ORIGINAL in the body layer, tables as real "
            "RTF rows, the link as a HYPERLINK field whose destination is not typed in the label",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(rtf_body=rtf_from_doc(CYRILLIC_ORIGINALS[0][1])),
            _plain_expectation(CYRILLIC_ORIGINALS[0][1]),
        ),
        fid_case(
            "fid-044",
            "TNEF HTML body: the ORIGINAL's table is a real <table> with <th> headers, so a "
            "flattener that drops the row structure loses the header association",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(html_body=html_from_doc(CYRILLIC_ORIGINALS[1][1])),
            _plain_expectation(CYRILLIC_ORIGINALS[1][1]),
        ),
        fid_case(
            "fid-045",
            "TNEF plain-text body: the ordered checklist must keep its step order",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(body=text_from_doc(EN_LIST_ORDER)),
            _plain_expectation(EN_LIST_ORDER),
        ),
        fid_case(
            "fid-046",
            "TNEF RTF body carrying grouped amounts: an NBSP thousands separator may render as a "
            "plain space but must never be deleted",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(rtf_body=rtf_from_doc(EN_NUMBERS)),
            _plain_expectation(EN_NUMBERS),
        ),
        fid_case(
            "fid-047",
            "TNEF HTML body with three links: every label must keep its own destination",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(html_body=html_from_doc(EN_LINKS)),
            _plain_expectation(EN_LINKS),
        ),
    ]


def _tnef_embedded_cases() -> list[FidCase]:
    """Containers carrying embedded pdf / xlsx / text documents, and a body plus an attachment."""
    embedded_report = _plain_expectation(EN_REPORT)
    return [
        fid_case(
            "fid-048",
            "TNEF embedding a PDF: the table is painted cell by cell at real column offsets, so a "
            "reader that loses the geometry loses the header association",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(attachments=((_EMBEDDED_PDF, pdf_from_doc(EN_REPORT)),)),
            _plain_expectation(EN_REPORT),
        ),
        fid_case(
            "fid-049",
            "TNEF embedding a workbook: sheet order is the container's page order",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(attachments=((_EMBEDDED_XLSX, xlsx_from_doc(CYRILLIC_ORIGINALS[2][1])),)),
            _plain_expectation(CYRILLIC_ORIGINALS[2][1]),
        ),
        fid_case(
            "fid-050",
            "TNEF embedding a UTF-8 plain-text note on Cyrillic: no unit may arrive replaced",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(attachments=((_EMBEDDED_TXT, text_from_doc(CYRILLIC_ORIGINALS[3][1])),)),
            _plain_expectation(CYRILLIC_ORIGINALS[3][1]),
        ),
        fid_case(
            "fid-051",
            "TNEF with a plain-text body AND an embedded .docx: the container reads body first, "
            "then the embedded document, and neither may swallow the other",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(
                body=text_from_doc(EN_LIST_ORDER),
                attachments=((_EMBEDDED_DOCX, build_docx(EN_REPORT)),),
            ),
            _container_expectation(
                (("body", _plain_expectation(EN_LIST_ORDER)), ("att1", embedded_report))
            ),
        ),
        fid_case(
            "fid-052",
            "TNEF with TWO embedded documents: container order fixes which document's units come "
            "first; a reader that emits only the last attachment loses a whole document",
            "tnef",
            TNEF_CONTENT_TYPE,
            build_tnef(
                attachments=(
                    (_EMBEDDED_DOCX, build_docx(EN_LIST_ORDER)),
                    (_EMBEDDED_TXT, text_from_doc(EN_LINKS)),
                )
            ),
            _container_expectation(
                (
                    ("att1", _plain_expectation(EN_LIST_ORDER)),
                    ("att2", _plain_expectation(EN_LINKS)),
                )
            ),
        ),
    ]


def build_tnef_cases() -> tuple[FidCase, ...]:
    """Build the tnef half of the battery (`fid-040` .. `fid-052`)."""
    return tuple(_tnef_taxonomy_cases() + _tnef_body_cases() + _tnef_embedded_cases())
