"""FID battery entry point — the public `build_fid_cases()` named in contract 1.3.

Role: the single public surface of the extraction-fidelity fixture battery
    (`fixtures.fid_cases.build_fid_cases`). It concatenates the authored halves — pdf and docx
    (`fid_cases_c`), xlsx and tnef (`fid_cases_d`), html, rtf and plain text (`fid_cases_e`) and
    the encoding / Unicode cases (`fid_cases_f`) — in `case_id` order, validates the battery
    against the contract's own minimums, and re-exports the record types so a consumer needs one
    import. The split into `fid_cases_a` (vocabulary), `fid_cases_b` (the ORIGINAL library) and the
    four case halves exists only to keep each module under the house file-size ceiling
    (`.claude/rules/code-quality.md` A2); it carries no semantics.
Used by: the FID gate evaluator `tools.mem01_verify.gates.gate_fid` (contract 10.5 / criterion
    `fid.provisional`), `tools.mem01_verify.fixtures.digest.fixtures_digest` (the battery digest
    enters `config_hash`), and the instrument tests under `backend/tests/tools/mem01_verify/`.
Depends on: `fid_cases_a` and the four case halves; through them `fid_builders*`, `python-docx`,
    `openpyxl` and `compressed-rtf`. NO measured component is imported or invoked (contract R12):
    no extractor, no `redact_secrets`, no sanitizer. Nothing from `backend/app/`, nothing from
    `backend/tests/`.
Key invariants:
    - `build_fid_cases()` returns at least 70 cases (`fid.provisional`'s declared `minimum`) —
      124 today. Ninety come from the per-format halves (13 pdf, 13 docx, 13 xlsx, 13 tnef, 13
      html, 13 rtf, 12 plain text) and 34 from the encoding / Unicode half, which is itself
      authored across formats (25 text, 4 html, 2 docx, 2 rtf, 1 xlsx). Counting both, every
      format clears the contract's ≥10: pdf 13, tnef 13, xlsx 14, docx 15, rtf 15, html 17,
      text 37. `_validate_coverage` enforces every one of these floors at build time.
    - At least three cases per format that can carry tables, links or multi-column layout (pdf,
      docx, xlsx, html, rtf, and a tnef container embedding such a document) carry
      `expected.taxonomy_complete`.
    - Every `case_id` is unique, of the form `fid-NNN`, and every record carries
      `criterion_id == "fid.provisional"` — this battery scores exactly one criterion.
    - Ordering is stable (pdf, docx, xlsx, tnef, html, rtf, text, encoding — ascending `case_id`):
      the fixture battery's identity must not change because a case moved.
    - EXPECTATIONS ARE SCORED AGAINST THE STORED `extracted_text` — the value the ingest path
      stores after sanitization and secret masking — never against an intermediate extractor
      object. A unit the stored string cannot carry is a FAILURE of this gate, which is what the
      gate exists to find (contract 10.5).
    - Expectations are derived from the synthetic ORIGINALS, never by running an extractor. Where
      the current pipeline is known to lose a unit, the fixture FAILS today; that is the point.
"""

from __future__ import annotations

from collections import Counter

from tools.mem01_verify.exceptions import FixtureError
from tools.mem01_verify.fixtures.fid_cases_a import (
    FID_CRITERION,
    DocSpec,
    FidCase,
    FidExpectation,
    FidFormat,
    FidUnit,
    LinkPair,
    NegationGuard,
    RowGroup,
    TableSpec,
)
from tools.mem01_verify.fixtures.fid_cases_c import build_docx_cases, build_pdf_cases
from tools.mem01_verify.fixtures.fid_cases_d import build_tnef_cases, build_xlsx_cases
from tools.mem01_verify.fixtures.fid_cases_e import (
    build_html_cases,
    build_rtf_cases,
    build_text_cases,
)
from tools.mem01_verify.fixtures.fid_cases_f import build_encoding_cases

#: The `minimum` denominator `fid.provisional` declares in the criteria file.
FID_MINIMUM_CASES = 70

#: Contract 10.5: at least ten fixtures per supported format.
FID_MINIMUM_PER_FORMAT = 10

#: Contract 10.5: at least thirty encoding / Unicode cases.
FID_MINIMUM_ENCODING_CASES = 30

#: Contract 10.5: formats that can carry tables, links or multi-column layout need at least three
#: full-unit-taxonomy cases each. `text` is absent by construction — plain text carries none of
#: the three, so a plain-text case never claims the full taxonomy.
FID_TAXONOMY_FORMATS: tuple[FidFormat, ...] = ("pdf", "docx", "xlsx", "tnef", "html", "rtf")

#: Contract 10.5: at least three full-taxonomy cases per format above.
FID_MINIMUM_TAXONOMY_PER_FORMAT = 3


def _validate(cases: tuple[FidCase, ...]) -> None:
    """Check the assembled battery against the contract's minimums and its own invariants.

    Args:
        cases: the concatenated battery.

    Raises:
        FixtureError: on any violation — a battery that silently fell below a minimum would make
            the gate's denominator wrong, and R2 forbids an error shrinking a denominator.
    """
    if len(cases) < FID_MINIMUM_CASES:
        raise FixtureError(f"fid_cases: battery below the minimum of {FID_MINIMUM_CASES}")
    if len({case.case_id for case in cases}) != len(cases):
        raise FixtureError("fid_cases: duplicate case_id in the battery")
    for case in cases:
        if case.criterion_id != FID_CRITERION:
            raise FixtureError(f"fid_cases: {case.case_id} has a foreign criterion_id")
        if not case.payload:
            raise FixtureError(f"fid_cases: {case.case_id} has an empty payload")
        if not case.expected.units:
            raise FixtureError(f"fid_cases: {case.case_id} requires no unit")
        if len({unit.unit_id for unit in case.expected.units}) != len(case.expected.units):
            raise FixtureError(f"fid_cases: {case.case_id} has a duplicate unit_id")
    _validate_coverage(cases)


def _validate_coverage(cases: tuple[FidCase, ...]) -> None:
    """Check the per-format, per-taxonomy and encoding minimums of contract 10.5."""
    per_format = Counter(case.format for case in cases)
    for fid_format, count in per_format.items():
        if count < FID_MINIMUM_PER_FORMAT:
            raise FixtureError(f"fid_cases: only {count} {fid_format} fixtures")
    taxonomy = Counter(case.format for case in cases if case.expected.taxonomy_complete)
    for fid_format in FID_TAXONOMY_FORMATS:
        if taxonomy[fid_format] < FID_MINIMUM_TAXONOMY_PER_FORMAT:
            raise FixtureError(
                f"fid_cases: {taxonomy[fid_format]} full-taxonomy {fid_format} fixtures"
            )
    encoding = sum(1 for case in cases if case.origin.startswith("encoding — "))
    if encoding < FID_MINIMUM_ENCODING_CASES:
        raise FixtureError(f"fid_cases: only {encoding} encoding fixtures")


def build_fid_cases() -> tuple[FidCase, ...]:
    """Build the whole extraction-fidelity battery, in `case_id` order.

    Every ORIGINAL is assembled in memory on each call (no fixture file exists on disk), so the
    call is not free; a caller that needs the battery more than once should hold on to the tuple.

    Returns:
        The 124 fixtures of `fid.provisional`, ascending by `case_id`.

    Raises:
        FixtureError: the assembled battery violates a contract minimum or an invariant of this
            module.
    """
    cases = (
        build_pdf_cases()
        + build_docx_cases()
        + build_xlsx_cases()
        + build_tnef_cases()
        + build_html_cases()
        + build_rtf_cases()
        + build_text_cases()
        + build_encoding_cases()
    )
    _validate(cases)
    return cases


__all__ = [
    "FID_CRITERION",
    "FID_MINIMUM_CASES",
    "FID_MINIMUM_ENCODING_CASES",
    "FID_MINIMUM_PER_FORMAT",
    "FID_MINIMUM_TAXONOMY_PER_FORMAT",
    "FID_TAXONOMY_FORMATS",
    "DocSpec",
    "FidCase",
    "FidExpectation",
    "FidFormat",
    "FidUnit",
    "LinkPair",
    "NegationGuard",
    "RowGroup",
    "TableSpec",
    "build_fid_cases",
]
