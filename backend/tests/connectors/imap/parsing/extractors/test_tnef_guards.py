"""
Role: Unit tests for the TNEF extractor's resource + content GUARDS (split from test_tnef.py
      to respect the A2 size cap) — the decompression-bomb bounds (compressed-RTF property
      bounded BEFORE decompressing, 'rtf-body-over-bound' detail note, embedded files still
      processed; html/plain body sources bounded before flattening), the embedded markup
      flattening (html/rtf payloads FLATTENED, never stored as raw source — the EQ-4 invariant
      inside the container), the FULL-TEXT printable guard (binary behind an ASCII preamble is
      skip-marked, never stored as soup), and the C1/NEL filename strip.
Used by: pytest (tests/connectors/imap/parsing/extractors).
Depends on: app.connectors.imap.parsing.extractors.tnef, .extraction_result, the conftest
            build_tnef builder.
"""

from __future__ import annotations

import pytest

import app.connectors.imap.parsing.extractors.tnef as tnef_module
from app.connectors.imap.parsing.extraction_result import STATUS_EMPTY, STATUS_EXTRACTED
from app.connectors.imap.parsing.extractors.tnef import extract_tnef_text
from tests.connectors.imap.parsing.extractors.conftest import build_tnef

RTF_BODY = b"{\\rtf1\\ansi\\ansicpg1252\\deff0 Quarterly \\b report\\b0  body!}"


# — decompression-bomb bounds (2026-06-12 review: LZFu measured at 8.0x expansion) —


def test_extract_tnef_text_over_bound_compressed_rtf_skipped_with_detail_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The COMPRESSED property bytes are bounded BEFORE any decompression: the over-bound body
    # is skipped (never materialized), the cascade degrades, and detail carries the fixed note.
    monkeypatch.setattr(tnef_module, "MAX_COMPRESSED_RTF_BYTES", 16)
    payload = build_tnef(body=b"plain fallback body", rtf_body=RTF_BODY)

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text == "plain fallback body"  # the rtf layer was skipped, not the container
    assert result.detail == "body=plain embedded_files=0 rtf-body-over-bound"


def test_extract_tnef_text_over_bound_rtf_embedded_files_still_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tnef_module, "MAX_COMPRESSED_RTF_BYTES", 16)
    payload = build_tnef(
        rtf_body=RTF_BODY, attachments=[(b"notes.txt", b"embedded notes survive")]
    )

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "[embedded: notes.txt]\nembedded notes survive" in result.text
    assert "Quarterly" not in result.text  # the over-bound body never decompressed into the text
    assert result.detail == "body=none embedded_files=1 rtf-body-over-bound"


def test_extract_tnef_text_over_bound_html_body_source_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The html body SOURCE is bounded before flattening — over-bound falls through to plain.
    monkeypatch.setattr(tnef_module, "MAX_BODY_SOURCE_BYTES", 64)
    big_html = b"<html><body>" + b"a" * 200 + b"</body></html>"
    payload = build_tnef(html_body=big_html, body=b"plain instead")

    result = extract_tnef_text(payload)

    assert result.text == "plain instead"
    assert result.detail == "body=plain embedded_files=0"


def test_extract_tnef_text_over_bound_plain_body_source_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tnef_module, "MAX_BODY_SOURCE_BYTES", 32)
    payload = build_tnef(body=b"p" * 100)

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EMPTY
    assert result.text is None  # honest NULL — an over-bound source is skipped, never stored
    assert result.detail == "body=none embedded_files=0"


# — embedded markup flattening + the full-text printable guard (EQ-4 inside the container) —


def test_extract_tnef_text_embedded_html_file_flattened_not_raw_markup() -> None:
    # The EQ-4 invariant applies INSIDE the container too: an embedded .html file sniffs as
    # text, but its SOURCE must flatten through the email-body flattener, never store raw.
    html_file = b"<html><body><h1>Embedded Invoice</h1><p>Total: 99 EUR</p></body></html>"
    payload = build_tnef(attachments=[(b"invoice.html", html_file)])

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "Embedded Invoice" in result.text and "Total: 99 EUR" in result.text
    assert "<h1>" not in result.text and "<body>" not in result.text


def test_extract_tnef_text_embedded_doctype_html_bom_and_case_tolerated() -> None:
    # The markup probe is whitespace/BOM-tolerant and case-insensitive — real producers prepend
    # both and shout the doctype.
    html_file = b"\xef\xbb\xbf  \r\n<!DOCTYPE HTML><html><body><p>Doctype body</p></body></html>"
    payload = build_tnef(attachments=[(b"page.htm", html_file)])

    result = extract_tnef_text(payload)

    assert result.text is not None
    assert "Doctype body" in result.text
    assert "<!DOCTYPE" not in result.text and "<p>" not in result.text


def test_extract_tnef_text_embedded_rtf_file_stripped_not_raw_markup() -> None:
    rtf_file = b"{\\rtf1\\ansi\\deff0 Embedded \\b agreement\\b0  text}"
    payload = build_tnef(attachments=[(b"agreement.rtf", rtf_file)])

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "Embedded agreement text" in result.text
    assert "\\rtf1" not in result.text and "\\deff0" not in result.text


def test_extract_tnef_text_embedded_binary_behind_ascii_preamble_skip_marked() -> None:
    # detect_payload_kind probes only the first 4KB: an ASCII preamble over a binary remainder
    # sniffs as text — the FULL-TEXT printable guard must skip-mark it, never store the soup.
    binary_soup = b"PREAMBLE " * 512 + bytes(range(1, 32)) * 800  # 4.5KB ascii, then controls
    payload = build_tnef(body=b"body", attachments=[(b"firmware.bin", binary_soup)])

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED  # the body still extracts
    assert result.text is not None
    assert "[embedded: firmware.bin — binary content misclassified as text]" in result.text
    assert "PREAMBLE" not in result.text  # not even the preamble is stored — all or nothing


# — C1-control filename strip (the SAFE_NAME forged-marker surface) —


def test_extract_tnef_text_nel_c1_control_filename_stripped() -> None:
    # U+0085 (NEL) is a C1 control some renderers honor as a line break — the same forged-line
    # surface as the C0 newline strip (2026-06-12 review): it must never survive into a marker.
    payload = build_tnef(attachments=[("evil\u0085name.txt".encode(), b"file content")])

    result = extract_tnef_text(payload)

    assert result.text is not None
    assert "[embedded: evilname.txt]\nfile content" in result.text
    assert "\u0085" not in result.text
