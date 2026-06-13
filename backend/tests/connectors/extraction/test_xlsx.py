"""
Role: Unit tests for the xlsx/xlsm extractor (design §2.5) — the dual output (a TYPED sparse cell
      grid in result.structured + a bounded text render in result.text): typed capture (number→n,
      date→d ISO, bool→b, string→s; number_format preserved; a formula's CACHED value read under
      data_only), the text render (sheet markers + serialize_table rows), the sparse skip of empty
      cells, MAX_CELLS structured-truncation (grid truncated, text still extracted), the empty
      workbook → empty, OLE magic → encrypted, corrupt zip → corrupt, the SHARED OOXML zip-bomb
      guards firing for xlsx too (an EOCD member flood → corrupt), the vendor-log payload-leak
      regression, and the NEVER-raises property. Fixtures are built in-memory with openpyxl (see
      conftest.build_xlsx) — pure, no I/O.
Used by: pytest (tests/connectors/extraction).
Depends on: app.connectors.extraction.xlsx, .extraction_result, the conftest build_xlsx builder,
            and the shared zip helpers in test_docx.
"""

from __future__ import annotations

import datetime
import logging
import struct

import pytest

import app.connectors.extraction.xlsx as xlsx_module
from app.connectors.extraction.extraction_result import (
    EXTRACTION_STATUSES,
    STATUS_CORRUPT,
    STATUS_EMPTY,
    STATUS_ENCRYPTED,
    STATUS_EXTRACTED,
    STATUS_PENDING,
    STATUS_TRUNCATED,
    ExtractionResult,
)
from app.connectors.extraction.xlsx import extract_xlsx_text
from tests.connectors.extraction.conftest import build_xlsx
from tests.connectors.extraction.test_docx import _zip_with

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _cells_by_ref(result: ExtractionResult, sheet_index: int = 0) -> dict[str, dict]:
    """The captured cells of one sheet, keyed by their A1 ref (for typed-value assertions)."""
    assert result.structured is not None
    return {cell["ref"]: cell for cell in result.structured["sheets"][sheet_index]["cells"]}


# — typed capture (the structured grid is the lossless source of truth) —


def test_extract_xlsx_number_cell_captured_as_n_with_float_value() -> None:
    result = extract_xlsx_text(build_xlsx(sheets=[("S", [["price", 3.5]])]))

    cells = _cells_by_ref(result)
    assert cells["B1"] == {"ref": "B1", "t": "n", "v": 3.5}


def test_extract_xlsx_integer_cell_captured_as_n() -> None:
    result = extract_xlsx_text(build_xlsx(sheets=[("S", [["qty", 12]])]))

    assert _cells_by_ref(result)["B1"]["t"] == "n"
    assert _cells_by_ref(result)["B1"]["v"] == 12


def test_extract_xlsx_date_cell_captured_as_d_iso8601() -> None:
    when = datetime.datetime(2026, 6, 13, 10, 30)
    result = extract_xlsx_text(build_xlsx(sheets=[("S", [[when]])]))

    cell = _cells_by_ref(result)["A1"]
    assert cell["t"] == "d"
    assert cell["v"] == "2026-06-13T10:30:00"


def test_extract_xlsx_bool_cell_captured_as_b_not_n() -> None:
    # Python's bool is a subclass of int — bool MUST be classified before number.
    result = extract_xlsx_text(build_xlsx(sheets=[("S", [["flag", True]])]))

    cell = _cells_by_ref(result)["B1"]
    assert cell["t"] == "b"
    assert cell["v"] is True


def test_extract_xlsx_string_cell_captured_as_s() -> None:
    result = extract_xlsx_text(build_xlsx(sheets=[("S", [["hello world"]])]))

    assert _cells_by_ref(result)["A1"] == {"ref": "A1", "t": "s", "v": "hello world"}


def test_extract_xlsx_currency_cell_preserves_number_format() -> None:
    payload = build_xlsx(
        sheets=[("S", [["total", 1234.5]])],
        cell_specs={"S": {"B1": '"$"#,##0.00'}},
    )

    cell = _cells_by_ref(extract_xlsx_text(payload))["B1"]
    assert cell["t"] == "n"
    assert cell["nf"] == '"$"#,##0.00'


def test_extract_xlsx_default_format_cell_omits_nf() -> None:
    result = extract_xlsx_text(build_xlsx(sheets=[("S", [[42]])]))

    assert "nf" not in _cells_by_ref(result)["A1"]


def test_extract_xlsx_formula_cell_uses_cached_value_under_data_only() -> None:
    # data_only reads the CACHED value Excel last wrote — never the formula. The fixture injects a
    # cached <v>; v1's documented limit is that a never-opened workbook has no cache (→ skipped).
    payload = build_xlsx(
        sheets=[("S", [["base", 10]])],
        cell_specs={"S": {"A2": ("formula", "B1*5", 50)}},
    )

    cell = _cells_by_ref(extract_xlsx_text(payload))["A2"]
    assert cell["t"] == "n"
    assert cell["v"] == 50


def test_extract_xlsx_overflow_cached_value_captured_as_string_not_n() -> None:
    # A cached formula value that overflowed IEEE-754 (Excel last wrote =1E309) surfaces under
    # data_only as float('inf'). 'inf' is NOT JSONB-storable (the default json serializer emits the
    # bare literal Infinity, which Postgres JSONB rejects → a poison message that dead-letters the
    # whole email). The non-finite guard captures it as a STRING so the cell is preserved + safe.
    payload = build_xlsx(
        sheets=[("S", [["base", 10]])],
        cell_specs={"S": {"A2": ("formula", "B1*5", "1E309")}},
    )

    cell = _cells_by_ref(extract_xlsx_text(payload))["A2"]
    assert cell["t"] == "s"
    assert cell["v"] == "inf"


def test_extract_xlsx_typed_cell_non_finite_floats_are_jsonb_safe() -> None:
    # The contract the guard defends: result.structured must serialize under allow_nan=FALSE — that
    # is exactly what Postgres JSONB accepts (no Infinity/NaN literals). Drives _typed_cell directly
    # for the three non-finite floats (inf via XML injection can't reach -inf/nan portably).
    import json
    import math

    class _FakeCell:
        def __init__(self, value: float) -> None:
            self.value = value
            self.number_format = "General"

    for non_finite in (math.inf, -math.inf, math.nan):
        typed = xlsx_module._typed_cell(_FakeCell(non_finite), column_index=1, row_index=1)

        assert typed is not None
        assert typed["t"] == "s"  # captured as a string, never a non-finite 'n'
        assert isinstance(typed["v"], str)
        # allow_nan=False is the strict JSON Postgres JSONB enforces — must not raise.
        json.dumps(typed, allow_nan=False)


def test_extract_xlsx_grid_format_tag_is_v1() -> None:
    result = extract_xlsx_text(build_xlsx(sheets=[("S", [["x"]])]))

    assert result.structured is not None
    assert result.structured["format"] == "xlsx-grid-v1"


def test_extract_xlsx_records_openpyxl_provenance() -> None:
    from importlib import metadata

    result = extract_xlsx_text(build_xlsx(sheets=[("S", [["x"]])]))

    assert result.extractor_name == "openpyxl"
    assert result.extractor_version == metadata.version("openpyxl")


# — the text render (the embeddable surface) —


def test_extract_xlsx_text_render_has_sheet_markers_and_rows() -> None:
    payload = build_xlsx(
        sheets=[
            ("Items", [["Item", "Qty"], ["Bolts", 12]]),
            ("Notes", [["a note"]]),
        ]
    )

    result = extract_xlsx_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "[sheet: Items]" in result.text
    assert "Item | Qty" in result.text
    assert "Bolts | 12" in result.text
    assert "[sheet: Notes]" in result.text


def test_extract_xlsx_detail_reports_sheet_and_cell_counts() -> None:
    result = extract_xlsx_text(build_xlsx(sheets=[("S", [["a", "b"], ["c", "d"]])]))

    assert result.detail == "sheets=1 cells=4"


# — sparse skip of empty cells —


def test_extract_xlsx_empty_cells_are_skipped_sparse() -> None:
    # Row 2 has gaps (None) — they must NOT appear in the typed grid.
    payload = build_xlsx(sheets=[("S", [["A", "B", "C"], ["x", None, "z"]])])

    refs = set(_cells_by_ref(extract_xlsx_text(payload)))
    assert "B2" not in refs  # the None cell skipped
    assert {"A1", "B1", "C1", "A2", "C2"} <= refs


def test_extract_xlsx_whitespace_only_string_cell_skipped() -> None:
    payload = build_xlsx(sheets=[("S", [["real", "   "]])])

    refs = set(_cells_by_ref(extract_xlsx_text(payload)))
    assert "A1" in refs
    assert "B1" not in refs  # whitespace-only string sanitizes to '' → sparse skip


# — MAX_CELLS truncation (structured.truncated true, text still extracted) —


def test_extract_xlsx_over_max_cells_truncates_grid_but_keeps_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xlsx_module, "MAX_CELLS", 3)
    payload = build_xlsx(sheets=[("S", [["a", "b", "c", "d", "e"]])])

    result = extract_xlsx_text(payload)

    assert result.status == STATUS_EXTRACTED  # text bound not hit → still extracted
    assert result.structured is not None
    assert result.structured["truncated"] is True
    assert len(result.structured["sheets"][0]["cells"]) == 3  # capped, kept what we got
    assert "structured-truncated" in (result.detail or "")
    assert result.text is not None  # the render survives


def test_extract_xlsx_over_max_sheets_marks_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xlsx_module, "MAX_SHEETS", 1)
    payload = build_xlsx(sheets=[("One", [["a"]]), ("Two", [["b"]])])

    result = extract_xlsx_text(payload)

    assert result.structured is not None
    assert result.structured["truncated"] is True
    assert len(result.structured["sheets"]) == 1  # only the first sheet read


def test_extract_xlsx_phantom_empty_cells_bounded_by_scan_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex review: read_only iter_rows pads to the declared dimension, so a sparse sheet would
    # spin through empty cells without ever tripping the non-empty MAX_CELLS bound. The scan cap
    # bounds total VISITS (empty included) so a one-cell-far-away workbook can't tie up the worker.
    monkeypatch.setattr(xlsx_module, "MAX_SCANNED_CELLS", 4)
    # 3 rows x 3 cols = 9 visited cells; only 2 are non-empty (under MAX_CELLS), so ONLY the scan
    # cap can stop this — proving the visit bound, not the non-empty bound, fires.
    payload = build_xlsx(
        sheets=[("Sparse", [["x", None, None], [None, None, None], [None, None, "y"]])]
    )

    result = extract_xlsx_text(payload)

    assert result.structured is not None
    assert result.structured["truncated"] is True


def test_extract_xlsx_single_oversize_cell_caps_before_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex review: the byte backstop is checked WITH the candidate cell before appending, so a
    # single huge cell cannot slip past and leave truncated=false. A lone giant cell -> truncated,
    # not appended.
    monkeypatch.setattr(xlsx_module, "MAX_STRUCTURED_BYTES", 100)
    payload = build_xlsx(sheets=[("Big", [["x" * 5000]])])  # one cell, well over 100 bytes

    result = extract_xlsx_text(payload)

    assert result.structured is not None
    assert result.structured["truncated"] is True
    assert result.structured["sheets"][0]["cells"] == []  # the breaching cell was NOT appended


def test_extract_xlsx_text_over_char_cap_returns_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xlsx_module, "MAX_EXTRACTED_CHARS", 10)
    payload = build_xlsx(sheets=[("S", [["this is a long string well past ten chars"]])])

    result = extract_xlsx_text(payload)

    assert result.status == STATUS_TRUNCATED
    assert result.text is not None and len(result.text) == 10


def test_extract_xlsx_wide_sheet_caps_rendered_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xlsx_module, "RENDER_MAX_COLS", 2)
    payload = build_xlsx(sheets=[("S", [["a", "b", "c", "d"]])])

    result = extract_xlsx_text(payload)

    # All four cells captured in the grid; only the first two render.
    assert len(_cells_by_ref(result)) == 4
    assert result.text is not None
    assert "c" not in result.text.split("[sheet: S]")[1]


def test_extract_xlsx_tall_sheet_marks_more_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xlsx_module, "RENDER_MAX_ROWS", 2)
    payload = build_xlsx(sheets=[("S", [["r1"], ["r2"], ["r3"], ["r4"]])])

    result = extract_xlsx_text(payload)

    assert result.text is not None
    assert "[+more rows]" in result.text


# — empty workbook → empty —


def test_extract_xlsx_empty_workbook_returns_empty_with_null_text() -> None:
    result = extract_xlsx_text(build_xlsx(sheets=[("Blank", [])]))

    assert result.status == STATUS_EMPTY
    assert result.text is None  # honest NULL, never ''
    assert result.structured is not None  # the shape (empty sheet shell) is still recorded


# — encrypted (OLE magic) —


def test_extract_xlsx_ole_magic_returns_encrypted() -> None:
    result = extract_xlsx_text(OLE_MAGIC + b"\x00" * 64)

    assert result.status == STATUS_ENCRYPTED
    assert result.text is None
    assert result.detail == "ole-container (password-protected xlsx)"


# — corrupt (zip validation; class names only) —


def test_extract_xlsx_garbage_bytes_returns_corrupt_class_name_only() -> None:
    marker = "TOP-SECRET-BUDGET"

    result = extract_xlsx_text(f"not a zip {marker}".encode())

    assert result.status == STATUS_CORRUPT
    assert result.text is None
    assert marker not in (result.detail or "")  # payload never echoes into detail


def test_extract_xlsx_valid_zip_but_not_a_workbook_returns_corrupt() -> None:
    # A structurally valid zip that openpyxl can't read as a workbook → corrupt (class name only).
    payload = _zip_with({"hello.txt": "just a plain zip"})

    result = extract_xlsx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.detail is not None and result.detail.startswith("openpyxl:")


# — the SHARED OOXML zip-bomb guard fires for xlsx too —


def test_extract_xlsx_eocd_member_flood_rejected_by_shared_guard() -> None:
    # Proves the SHARED ooxml guard runs on the xlsx path: an EOCD declaring 50000 members on a
    # non-zip body must be rejected with 'zip-member-bound' BEFORE openpyxl (or ZipFile) parses.
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 50000, 50000, 4_000_000, 0, 0)
    payload = b"not-really-a-zip-body" + eocd

    result = extract_xlsx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.detail == "zip-member-bound"


def test_extract_xlsx_expansion_bound_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shared raw-bytes bound on the xlsx path (a real workbook expands past a tiny cap). xlsx
    # calls validate_ooxml_package with the shared defaults, so we wrap it to force a tiny bound —
    # proving the expansion gate degrades the workbook to 'zip-expansion-bound' on this path too.
    from functools import partial

    monkeypatch.setattr(
        xlsx_module,
        "validate_ooxml_package",
        partial(xlsx_module.validate_ooxml_package, max_decompressed_bytes=64),
    )
    payload = build_xlsx(sheets=[("S", [["data here"]])])

    result = extract_xlsx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.detail == "zip-expansion-bound"


# — vendor-log payload leak regression (same class as pypdf's header echo) —


def test_extract_xlsx_corrupt_inner_xml_never_leaks_payload_to_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The secret must actually REACH openpyxl/et-xmlfile's parser (a raw-bytes payload dies at the
    # zip gate and never exercises them) — so: a structurally valid workbook whose sheet XML is
    # BROKEN and carries the marker. openpyxl parse-crashes on those bytes; no log record (incl.
    # formatted tracebacks via caplog.text) and no detail may contain payload content.
    marker = "SECRET-SALARY-TABLE-DO-NOT-DISTRIBUTE"
    base = build_xlsx(sheets=[("S", [["placeholder"]])])
    import zipfile
    from io import BytesIO

    out = BytesIO()
    with zipfile.ZipFile(BytesIO(base)) as source, zipfile.ZipFile(out, "w") as target:
        for member in source.namelist():
            if member == "xl/worksheets/sheet1.xml":
                target.writestr(member, f"<broken {marker} <<not-xml".encode())
            else:
                target.writestr(member, source.read(member))

    with caplog.at_level(logging.DEBUG):
        result = extract_xlsx_text(out.getvalue())

    assert result.status == STATUS_CORRUPT
    assert result.text is None
    assert marker not in (result.detail or "")
    assert marker not in caplog.text  # full formatted records, tracebacks included


# — never raises (the seam contract) —


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"", id="empty-bytes"),
        pytest.param(b"\x00" * 64, id="nul-run"),
        pytest.param(b"PK\x03\x04 truncated local header", id="bare-zip-magic"),
        pytest.param(OLE_MAGIC + b"\x00" * 32, id="ole-magic"),
        pytest.param(_zip_with({"xl/worksheets/sheet1.xml": "<broken"}), id="broken-inner-xml"),
    ],
)
def test_extract_xlsx_mutated_payloads_never_raise(payload: bytes) -> None:
    result = extract_xlsx_text(payload)

    assert isinstance(result, ExtractionResult)
    assert result.status in EXTRACTION_STATUSES and result.status != STATUS_PENDING
