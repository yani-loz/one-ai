"""
Role: Unit tests for the payload sniffer — every magic (pdf with BOM/whitespace tolerance,
      zip/ole/tnef at offset 0), the bounded text heuristic (utf-8 + cp1252 strict decode,
      the ≥90% printable ratio, the probe-window utf-8 split), and the boundary/empty cases.
      Pure, no I/O.
Used by: pytest (tests/connectors/extraction).
Depends on: app.connectors.extraction.sniff; the conftest builders for real
            pdf/docx/tnef payloads.
"""

from __future__ import annotations

import pytest

from app.connectors.extraction.sniff import (
    KIND_OLE,
    KIND_PDF,
    KIND_TEXT,
    KIND_TNEF,
    KIND_UNKNOWN,
    KIND_ZIP,
    TEXT_PROBE_BYTES,
    detect_payload_kind,
)
from tests.connectors.extraction.conftest import (
    TEXT_PAGE_STREAM,
    build_docx,
    build_pdf,
    build_tnef,
)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# — magics —


def test_detect_payload_kind_real_pdf_returns_pdf() -> None:
    assert detect_payload_kind(build_pdf([TEXT_PAGE_STREAM])) == KIND_PDF


def test_detect_payload_kind_pdf_with_leading_whitespace_returns_pdf() -> None:
    # Real producers prepend whitespace/newlines before the header — still a PDF.
    assert detect_payload_kind(b"\n  \r\n\t%PDF-1.7 rest of file") == KIND_PDF


def test_detect_payload_kind_pdf_with_utf8_bom_and_whitespace_returns_pdf() -> None:
    assert detect_payload_kind(b"\xef\xbb\xbf \n%PDF-1.4 ...") == KIND_PDF


def test_detect_payload_kind_pdf_mention_mid_payload_is_not_pdf() -> None:
    # '%PDF-' after NON-whitespace content is a mention, not a header (sniff stays strict).
    assert detect_payload_kind(b"see the attached %PDF-1.4 file") != KIND_PDF


def test_detect_payload_kind_real_docx_zip_returns_zip() -> None:
    assert detect_payload_kind(build_docx(["hello"])) == KIND_ZIP


def test_detect_payload_kind_bare_zip_magic_returns_zip() -> None:
    assert detect_payload_kind(b"PK\x03\x04 truncated zip") == KIND_ZIP


def test_detect_payload_kind_ole_magic_returns_ole() -> None:
    assert detect_payload_kind(OLE_MAGIC + b"\x00" * 64) == KIND_OLE


def test_detect_payload_kind_real_tnef_returns_tnef() -> None:
    assert detect_payload_kind(build_tnef(body=b"hi there")) == KIND_TNEF


def test_detect_payload_kind_tnef_magic_only_returns_tnef() -> None:
    # The 0x223E9F78 signature little-endian on the wire.
    assert detect_payload_kind(b"\x78\x9f\x3e\x22 rest") == KIND_TNEF


def test_detect_payload_kind_magic_not_at_offset_zero_is_not_container() -> None:
    # zip/ole/tnef magics are positional — their parsers require offset 0.
    assert detect_payload_kind(b" PK\x03\x04") != KIND_ZIP
    assert detect_payload_kind(b"\x00" + OLE_MAGIC) != KIND_OLE


# — text heuristic —


def test_detect_payload_kind_plain_ascii_returns_text() -> None:
    assert detect_payload_kind(b"hello world, a perfectly normal note.\n") == KIND_TEXT


def test_detect_payload_kind_utf8_cyrillic_returns_text() -> None:
    payload = "Договор за наем — финална версия.\n".encode()

    assert detect_payload_kind(payload) == KIND_TEXT


def test_detect_payload_kind_cp1252_accented_returns_text() -> None:
    payload = "café résumé naïve – déjà vu\n".encode("cp1252")  # invalid utf-8, valid cp1252

    assert detect_payload_kind(payload) == KIND_TEXT


def test_detect_payload_kind_binary_garbage_returns_unknown() -> None:
    # Decodes as utf-8 (NUL runs are valid code points) but is nowhere near 90% printable.
    assert detect_payload_kind(bytes(range(32)) * 8) == KIND_UNKNOWN


def test_detect_payload_kind_undecodable_bytes_return_unknown() -> None:
    # 0x90 is undefined in cp1252 and the run is invalid utf-8 — no strict decode succeeds.
    assert detect_payload_kind(b"\x90\xff\xfe\x90" * 16) == KIND_UNKNOWN


def test_detect_payload_kind_empty_payload_returns_unknown() -> None:
    assert detect_payload_kind(b"") == KIND_UNKNOWN


def test_detect_payload_kind_printable_ratio_boundary() -> None:
    # Exactly 90% printable → text; just under → unknown (the >= 0.9 boundary).
    at_boundary = b"a" * 90 + b"\x00" * 10
    below_boundary = b"a" * 89 + b"\x00" * 11

    assert detect_payload_kind(at_boundary) == KIND_TEXT
    assert detect_payload_kind(below_boundary) == KIND_UNKNOWN


def test_detect_payload_kind_probe_split_multibyte_utf8_still_text() -> None:
    # A clean utf-8 file whose multi-byte char straddles the 4KB probe boundary must not
    # misclassify: the split is a window artifact, not corruption.
    payload = b"a" * (TEXT_PROBE_BYTES - 1) + "я".encode() + b" tail far beyond the window"
    assert len(payload) > TEXT_PROBE_BYTES

    assert detect_payload_kind(payload) == KIND_TEXT


def test_detect_payload_kind_whitespace_counts_as_printable() -> None:
    # Tabs/newlines/CR are not str.isprintable() but ARE normal text furniture.
    assert detect_payload_kind(b"col1\tcol2\r\nrow\t2\r\n" * 20) == KIND_TEXT


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(b"%PDF-1.4", KIND_PDF, id="bare-pdf-header"),
        pytest.param(b"PK\x03\x04", KIND_ZIP, id="bare-zip-magic"),
        pytest.param(OLE_MAGIC, KIND_OLE, id="bare-ole-magic"),
        pytest.param(b"\x78\x9f\x3e\x22", KIND_TNEF, id="bare-tnef-magic"),
        pytest.param(b"{}", KIND_TEXT, id="two-char-json"),
        pytest.param(b"\x00", KIND_UNKNOWN, id="single-nul"),
    ],
)
def test_detect_payload_kind_minimal_payloads(payload: bytes, expected: str) -> None:
    assert detect_payload_kind(payload) == expected
