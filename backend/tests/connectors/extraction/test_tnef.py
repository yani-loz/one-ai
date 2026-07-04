"""
Role: Unit tests for the TNEF extractor — the body cascade (compressed-RTF decompress+strip,
      HTML flatten, plain decode; rtf preferred), depth-1 embedded-file dispatch (pdf/docx/text
      via sniffed magic; tnef/ole/unknown skip-marked; plain zips rendered unsupported, not
      corrupt), filename-injection sanitization, the 64-file and char bounds, per-file failure
      isolation, empty/corrupt verdicts, provenance, and the vendor-log payload-leak regression
      (tnefparse hex-formats payload bytes into WARNINGs — live-reproduced — so its logger is
      muted). The resource/content GUARDS (decompression-bomb bounds, embedded markup
      flattening + the printable guard, the C1/NEL filename strip) live in test_tnef_guards.py
      (A2 size split). Fixtures are HAND-ASSEMBLED minimal TNEF streams (see conftest.build_tnef
      — real tnefparse parses, checksums verified); no _FakeTnef stubbing needed.
Used by: pytest (tests/connectors/extraction).
Depends on: app.connectors.extraction.tnef, .extraction_result, the conftest
            build_tnef/build_pdf/build_docx builders.
"""

from __future__ import annotations

import logging
import struct
from importlib import metadata

import pytest

import app.connectors.extraction.tnef as tnef_module
from app.connectors.extraction.extraction_result import (
    EXTRACTION_STATUSES,
    STATUS_CORRUPT,
    STATUS_EMPTY,
    STATUS_EXTRACTED,
    STATUS_PENDING,
    STATUS_TRUNCATED,
    ExtractionResult,
)
from app.connectors.extraction.tnef import extract_tnef_text
from tests.connectors.extraction.conftest import (
    TEXT_PAGE_STREAM,
    _tnef_mapi_binary_property,
    build_docx,
    build_pdf,
    build_tnef,
    tnef_attribute,
)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
RTF_BODY = b"{\\rtf1\\ansi\\ansicpg1252\\deff0 Quarterly \\b report\\b0  body!}"


# — body cascade (design §2.9: the body IS the email's real content) —


def test_extract_tnef_text_rtf_body_decompressed_and_stripped() -> None:
    payload = build_tnef(rtf_body=RTF_BODY)

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text == "Quarterly report body!"  # markup stripped, content kept
    assert result.detail == "body=rtf embedded_files=0"


def test_extract_tnef_text_rtf_body_cp1251_hex_escapes_decoded() -> None:
    # striprtf decodes \'xx escapes per the rtf's own \ansicpg directive — Cyrillic survives.
    rtf = b"{\\rtf1\\ansi\\ansicpg1251\\deff0 \\'c4\\'ee\\'e3\\'ee\\'e2\\'ee\\'f0 2026}"

    result = extract_tnef_text(build_tnef(rtf_body=rtf))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "Договор 2026"  # Договор 2026


def test_extract_tnef_text_html_body_flattened_not_raw_markup() -> None:
    html = b"<html><body><h1>Invoice 42</h1><p>Total: 100 EUR</p></body></html>"

    result = extract_tnef_text(build_tnef(html_body=html))

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "Invoice 42" in result.text and "Total: 100 EUR" in result.text
    assert "<h1>" not in result.text  # flattened through the email-body flattener, never raw
    assert result.detail == "body=html embedded_files=0"


def test_extract_tnef_text_plain_body_decoded() -> None:
    result = extract_tnef_text(build_tnef(body=b"Plain body content."))

    assert result.status == STATUS_EXTRACTED
    assert result.text == "Plain body content."
    assert result.detail == "body=plain embedded_files=0"


def test_extract_tnef_text_rtf_body_preferred_over_plain_stub() -> None:
    payload = build_tnef(body=b"plain stub", rtf_body=b"{\\rtf1\\ansi Real content}")

    result = extract_tnef_text(payload)

    assert result.text == "Real content"
    assert "plain stub" not in (result.text or "")  # one body, best carrier wins


def test_extract_tnef_text_corrupt_rtf_body_degrades_to_plain() -> None:
    # A broken LZFu stream must not fail the container — the body cascade falls through.
    broken_rtf_prop = _tnef_mapi_binary_property(0x1009, b"\x10\x00\x00\x00notLZFu!\xff\xfe")
    stream = (
        build_tnef(body=b"fallback plain body")
        + tnef_attribute(1, 0x9003, 0x6, struct.pack("<I", 1) + broken_rtf_prop)
    )

    result = extract_tnef_text(stream)

    assert result.status == STATUS_EXTRACTED
    assert result.text == "fallback plain body"
    assert result.detail == "body=plain embedded_files=0"


# — embedded files (depth-1 recursion, sniffed dispatch) —


def test_extract_tnef_text_embedded_pdf_dispatched_and_sectioned() -> None:
    payload = build_tnef(
        body=b"see attached", attachments=[(b"report.pdf", build_pdf([TEXT_PAGE_STREAM]))]
    )

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert result.text.startswith("see attached")
    assert "[embedded: report.pdf]\n[page 1]\nHello World" in result.text
    assert result.detail == "body=plain embedded_files=1"


def test_extract_tnef_text_embedded_docx_dispatched_through_zip_sniff() -> None:
    payload = build_tnef(attachments=[(b"summary.docx", build_docx(["Embedded doc text"]))])

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "[embedded: summary.docx]\nEmbedded doc text" in result.text


def test_extract_tnef_text_embedded_plain_zip_marked_unsupported_not_corrupt() -> None:
    # A generic zip is simply not a format the depth-1 dispatch supports — the docx gates
    # reject it, and that verdict must render as unsupported, never as corrupt noise.
    plain_zip = b"PK\x03\x04 not a wordprocessing package"
    payload = build_tnef(body=b"body", attachments=[(b"archive.zip", plain_zip)])

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "[embedded: archive.zip — unsupported_format]" in result.text
    assert "corrupt" not in result.text


def test_extract_tnef_text_embedded_text_file_decoded() -> None:
    payload = build_tnef(attachments=[(b"notes.txt", b"meeting notes line one\nline two")])

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "[embedded: notes.txt]\nmeeting notes line one\nline two" in result.text


@pytest.mark.parametrize(
    ("name", "data", "expected_marker"),
    [
        pytest.param(
            b"nested.dat",
            b"\x78\x9f\x3e\x22 inner tnef bytes",
            "[embedded: nested.dat — skipped tnef]",
            id="nested-tnef",
        ),
        pytest.param(
            b"legacy.doc",
            OLE_MAGIC + b"\x00" * 32,
            "[embedded: legacy.doc — skipped ole]",
            id="ole",
        ),
        pytest.param(
            b"blob.bin",
            bytes(range(32)) * 4,
            "[embedded: blob.bin — skipped unknown]",
            id="unknown-binary",
        ),
    ],
)
def test_extract_tnef_text_nonrecursable_kinds_skip_marked(
    name: bytes, data: bytes, expected_marker: str
) -> None:
    # Depth-1 ONLY: tnef/ole/unknown payloads are named skips — never recursed into.
    result = extract_tnef_text(build_tnef(body=b"body", attachments=[(name, data)]))

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert expected_marker in result.text


def test_extract_tnef_text_embedded_empty_data_marked_empty() -> None:
    result = extract_tnef_text(build_tnef(body=b"body", attachments=[(b"void.bin", b"")]))

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert "[embedded: void.bin — empty]" in result.text


# — filename-injection sanitization —


def test_extract_tnef_text_crafted_filename_cannot_forge_section_markers() -> None:
    # Brackets + newlines stripped: a crafted name must not mint a fake '[embedded: …]' marker
    # or break the section layout.
    evil_name = b"evil]\n[embedded: fake-passwords.txt"
    payload = build_tnef(attachments=[(evil_name, b"real file content")])

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert result.text.count("[embedded:") == 1  # exactly the ONE real section marker
    assert "[embedded: fake-passwords.txt" not in result.text
    assert "\n[embedded" not in result.text.replace("[embedded: evil", "")  # no forged line


def test_extract_tnef_text_overlong_filename_capped() -> None:
    payload = build_tnef(attachments=[(b"n" * 200, b"file content here")])

    result = extract_tnef_text(payload)

    assert result.text is not None
    assert f"[embedded: {'n' * tnef_module.MAX_EMBEDDED_NAME_CHARS}]" in result.text
    assert "n" * (tnef_module.MAX_EMBEDDED_NAME_CHARS + 1) not in result.text


def test_extract_tnef_text_control_only_filename_becomes_unnamed() -> None:
    payload = build_tnef(attachments=[(b"\x01\x02[]\x1f", b"orphan content")])

    result = extract_tnef_text(payload)

    assert result.text is not None
    assert "[embedded: unnamed]\norphan content" in result.text



# — bounds (64 embedded files; the MAX_EXTRACTED_CHARS early bail) —


def test_extract_tnef_text_embedded_file_count_bounded_with_skip_marker() -> None:
    files = [(f"f{i}.txt".encode(), f"d{i}".encode()) for i in range(70)]

    result = extract_tnef_text(build_tnef(attachments=files))

    assert result.status == STATUS_EXTRACTED
    assert result.text is not None
    assert result.text.count("[embedded:") == tnef_module.MAX_EMBEDDED_FILES
    assert "[+6 more embedded files skipped]" in result.text


def test_extract_tnef_text_char_cap_bails_and_stores_truncated_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tnef_module, "MAX_EXTRACTED_CHARS", 40)
    files = [(b"a.txt", b"x" * 30), (b"b.txt", b"y" * 30), (b"c.txt", b"z" * 30)]

    result = extract_tnef_text(build_tnef(body=b"0123456789", attachments=files))

    assert result.status == STATUS_TRUNCATED
    assert result.text is not None and len(result.text) == 40  # capped text IS stored
    assert result.detail is not None and result.detail.startswith("capped from")
    assert "c.txt" not in result.text  # the loop bailed — later files never dispatched


# — per-file failure isolation —


def test_extract_tnef_text_embedded_extractor_crash_never_fails_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_data: bytes) -> ExtractionResult:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(tnef_module, "extract_pdf_text", _boom)
    payload = build_tnef(
        body=b"body survives", attachments=[(b"doc.pdf", build_pdf([TEXT_PAGE_STREAM]))]
    )

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EXTRACTED  # the container never fails on one file
    assert result.text is not None
    assert result.text.startswith("body survives")
    assert "[embedded: doc.pdf — failed:RuntimeError]" in result.text


# — empty / corrupt —


def test_extract_tnef_text_empty_container_returns_empty_with_null_text() -> None:
    result = extract_tnef_text(build_tnef())

    assert result.status == STATUS_EMPTY
    assert result.text is None  # honest NULL, never ''
    assert result.detail == "body=none embedded_files=0"


def test_extract_tnef_text_markers_alone_never_masquerade_as_content() -> None:
    # No body + only skip/status markers → empty (the pdf page-marker lesson): a row whose
    # whole "text" would be skip markers stores honest NULL instead.
    payload = build_tnef(attachments=[(b"blob.bin", bytes(range(32)) * 4)])

    result = extract_tnef_text(payload)

    assert result.status == STATUS_EMPTY
    assert result.text is None
    assert result.detail == "body=none embedded_files=1"


def test_extract_tnef_text_garbage_bytes_return_corrupt_with_class_name_only() -> None:
    marker = "TOP-SECRET-CONTRACT-TERMS"

    result = extract_tnef_text(f"\x00\x00\x00\x00 {marker}".encode())

    assert result.status == STATUS_CORRUPT
    assert result.text is None
    assert result.detail == "tnefparse:ValueError"  # wrong signature — class name only
    assert marker not in (result.detail or "")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"", id="empty-bytes"),
        pytest.param(b"\x00" * 64, id="nul-run"),
        pytest.param(b"\x78\x9f\x3e\x22", id="bare-tnef-magic"),
        pytest.param(build_tnef(body=b"truncate me")[:12], id="truncated-stream"),
        pytest.param(
            build_tnef() + tnef_attribute(1, 0x9003, 0x6, b"\xff\xfe\xfd"), id="garbage-mapi"
        ),
    ],
)
def test_extract_tnef_text_mutated_payloads_never_raise(payload: bytes) -> None:
    result = extract_tnef_text(payload)

    assert isinstance(result, ExtractionResult)
    assert result.status in EXTRACTION_STATUSES and result.status != STATUS_PENDING


# — provenance —


def test_extract_tnef_text_success_records_tnefparse_provenance() -> None:
    result = extract_tnef_text(build_tnef(body=b"any text"))

    assert result.extractor_name == "tnefparse"
    assert result.extractor_version == metadata.version("tnefparse")


# — vendor-log payload leak (tnefparse hex-formats payload bytes; live-reproduced 2026-06-12) —


def test_extract_tnef_text_payload_bytes_never_reach_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Three parse paths that make tnefparse LOG before the muting: (1) an invalid version
    # attribute (WARNING hex-formats its 4 payload bytes — live-reproduced "adde0000"); (2) a
    # corrupt MAPI properties block (decode_mapi logs str(exc) at ERROR + a DEBUG traceback);
    # (3) a valid compressed-RTF body driven through compressed_rtf + striprtf (live-checked
    # clean — pinned here). No log record may carry the secret or the version-byte hex.
    secret = "SECRET-PAYROLL-DO-NOT-LOG"
    invalid_version = build_tnef(body=secret.encode(), version=0xDEADBEEF)
    corrupt_mapi = build_tnef(body=b"x") + tnef_attribute(
        1, 0x9003, 0x6, struct.pack("<I", 3) + secret.encode()
    )
    rtf_secret = build_tnef(rtf_body=b"{\\rtf1\\ansi " + secret.encode() + b"}")

    with caplog.at_level(logging.DEBUG):
        results = [
            extract_tnef_text(invalid_version),
            extract_tnef_text(corrupt_mapi),
            extract_tnef_text(rtf_secret),
        ]

    assert results[2].text == secret  # the content itself extracts fine — only LOGS are clean
    assert secret not in caplog.text  # full formatted records, tracebacks included
    # The version warning hex-formats the 4 raw LE bytes — 0xDEADBEEF renders as 'efbeadde'.
    assert "efbeadde" not in caplog.text.lower()
    assert "Invalid TNEF Version" not in caplog.text  # the muted WARNING never materializes
    for result in results:
        assert secret not in (result.detail or "")


# — M2 residual (2026-07-03): tnefparse pre-decoded str bodies re-chained through detection —


def test_redecode_single_byte_text_repairs_wrong_codepage_mojibake() -> None:
    # tnefparse decodes with the container's DECLARED codepage; cp1251 bytes declared Latin
    # arrive as mojibake ('Ïðè ïúòóâàíå...'). The re-chain reverses the single-byte decode and
    # lets coherence detection recover the Cyrillic.
    from app.connectors.extraction.text_sanitize import redecode_single_byte_text

    original = "При пътуване с ЛМПС до Варна се признават разходи"
    mojibaked = original.encode("windows-1251").decode("latin-1")

    assert redecode_single_byte_text(mojibaked) == original


def test_redecode_single_byte_text_identity_on_correct_latin_text() -> None:
    # Correctly-decoded Latin text must round-trip to itself — the repair is a no-op.
    from app.connectors.extraction.text_sanitize import redecode_single_byte_text

    original = "Städte wie München prüfen die Verträge — café résumé naïve über alles"

    assert redecode_single_byte_text(original) == original


def test_redecode_single_byte_text_wide_unicode_untouched() -> None:
    # Genuine wide Unicode (correctly decoded UTF-16 TNEF body) fails the strict single-byte
    # re-encode and must be returned untouched.
    from app.connectors.extraction.text_sanitize import redecode_single_byte_text

    original = "Договор № 5 — одобрено ✓ (виж прикачения файл)"

    assert redecode_single_byte_text(original) == original


def test_redecode_single_byte_text_mixed_planes_repairs_per_line() -> None:
    # Word RTF doubles chars as \uN (real wide Cyrillic) next to \'xx runs (mojibaked under a
    # lying \ansicpg): one genuine wide char must not strand the mojibake lines — the per-line
    # fallback repairs the broken line and leaves the correct one untouched.
    from app.connectors.extraction.text_sanitize import redecode_single_byte_text

    correct_line = "Командировката е с право на дневни"  # genuine wide Cyrillic (from \uN)
    mojibake_line = "При пътуване с ЛМПС до Варна се признават".encode("windows-1251").decode(
        "latin-1"
    )
    mixed = correct_line + "\n" + mojibake_line

    repaired = redecode_single_byte_text(mixed)

    assert repaired.split("\n")[0] == correct_line
    assert repaired.split("\n")[1] == "При пътуване с ЛМПС до Варна се признават"
