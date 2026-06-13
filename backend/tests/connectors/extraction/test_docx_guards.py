"""
Role: Crafted-package GUARD tests for the docx extractor — the zip/EOCD bomb battery: member
      flood (EOCD pre-gate fires BEFORE ZipFile construction — ordering test-pinned), zip64
      sentinels, decompressed-expansion bound, the tiny-paragraph DOM bomb (measured ~15x lxml
      amplification), content-type-disguised XML parts, the pathological manifest. Split from
      test_docx.py (A2 size ceiling) — extraction-behavior tests live there; what attacks the
      PACKAGE lives here (mirrors test_tnef_guards.py).
Used by: pytest (tests/connectors/extraction).
Depends on: app.connectors.extraction.docx, .extraction_result, the shared
            test helpers in test_docx (zip builders) + conftest.
"""

from __future__ import annotations

import struct
import zipfile
from io import BytesIO

import pytest

import app.connectors.extraction.docx as docx_module
from app.connectors.extraction.docx import extract_docx_text
from app.connectors.extraction.extraction_result import STATUS_CORRUPT, STATUS_EXTRACTED
from tests.connectors.extraction.conftest import build_docx
from tests.connectors.extraction.test_docx import (
    WORDML_NS,
    _document_xml,
    _rezip_docx_with,
    _zip_with,
)


def test_extract_docx_text_zip_member_count_over_bound_returns_corrupt() -> None:
    # Central-directory sanity (2026-06-12 review): a zip declaring thousands of members only
    # exists to burn CPU/memory in directory scans — bounded before any parse.
    members: dict[str, str | bytes] = {f"junk/m{i}.bin": "" for i in range(4100)}
    members["word/document.xml"] = _document_xml(["hi"])
    payload = _zip_with(members)

    result = extract_docx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.detail == "zip-member-bound"

def test_extract_docx_text_eocd_declared_member_flood_rejected_before_parse() -> None:
    # 2026-06-12 Codex review (measured: a crafted 18 MB / 200k-member zip costs ~12 s CPU +
    # ~115 MB inside ZipFile.__init__ ALONE): the member bound must fire from the RAW EOCD
    # record before any directory parse. ORDERING PROOF: this payload is not a valid zip beyond
    # its EOCD tail — if construction ran first it would raise BadZipFile and the status would
    # be 'zipfile:BadZipFile'; 'zip-member-bound' proves the pre-gate fired first.

    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 50000, 50000, 4_000_000, 0, 0)
    payload = b"not-really-a-zip-body" + eocd

    result = extract_docx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.detail == "zip-member-bound"

def test_extract_docx_text_zip64_sentinel_eocd_rejected() -> None:
    # The zip64 sentinels (0xFFFF entries / 0xFFFFFFFF size) mean "at least this large" —
    # already over both bounds; rejected without parsing the zip64 record.

    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0, 0)
    payload = b"zip64-shaped-tail" + eocd

    result = extract_docx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.detail == "zip-member-bound"

def test_extract_docx_text_zip_expansion_over_bound_returns_corrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The zip-bomb guard: the seam's 50 MB ceiling bounds COMPRESSED bytes only; the summed
    # DECLARED decompressed size of all members must stay under MAX_DECOMPRESSED_BYTES or the
    # package is never parsed (python-docx/lxml materialize every part at open).
    monkeypatch.setattr(docx_module, "MAX_DECOMPRESSED_BYTES", 64)
    payload = build_docx(["any text"])  # default template expands well past 64 bytes

    result = extract_docx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.text is None
    assert result.detail == "zip-expansion-bound"

def test_extract_docx_text_tiny_paragraph_xml_bomb_returns_corrupt_xml_expansion_bound() -> None:
    # The DOM-expansion bomb the raw-bytes bound misses: tiny-paragraph WordprocessingML costs
    # ~15x its byte size as an lxml tree (measured: 32 MB of empty runs → ~490 MB RSS), so a
    # <1 MB compressed docx with ~100 MB of such XML would OOM-kill the ingest worker — a
    # SIGKILL the never-raise contract cannot catch. The XML-parts bound must refuse it BEFORE
    # any parse. This is the real payload, no monkeypatched limits.
    tiny_paragraphs = "<w:p><w:r><w:t>a</w:t></w:r></w:p>" * 340_000  # ~11 MB > 10 MB bound
    document_xml = (
        f'<?xml version="1.0"?><w:document xmlns:w="{WORDML_NS}">'
        f"<w:body>{tiny_paragraphs}</w:body></w:document>"
    )
    payload = _zip_with({"word/document.xml": document_xml}, compress=True)
    assert len(payload) < 1024 * 1024  # sails under the seam's 50 MB compressed ceiling

    result = extract_docx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.text is None
    assert result.detail == "xml-expansion-bound"

def test_extract_docx_text_large_media_member_not_counted_toward_xml_bound() -> None:
    # MAX_DECOMPRESSED_BYTES' headroom exists FOR media: a 15 MB image (over the 10 MB XML
    # bound, under the 100 MB bytes bound) is held as raw bytes, never DOM-parsed, and must not
    # trip the XML-parts gate — the document still extracts.
    base = build_docx(["Survives the media headroom"])
    payload = _rezip_docx_with(base, extra={"word/media/photo.png": b"\x00" * (15 * 1024 * 1024)})

    result = extract_docx_text(payload)

    assert result.status == STATUS_EXTRACTED
    assert result.text == "Survives the media headroom"

def test_extract_docx_text_content_type_override_disguised_xml_bomb_returns_corrupt() -> None:
    # python-docx selects the part CLASS by [Content_Types].xml CONTENT TYPE, not by suffix — an
    # Override mapping a media-named member to an XML part type gets it lxml-parsed at open. A
    # suffix-only gate would wave this 11 MB 'png' through; the content-type pass must count it.
    base = build_docx(["decoy body"])
    with zipfile.ZipFile(BytesIO(base)) as archive:
        manifest = archive.read("[Content_Types].xml")
    disguise = (
        b'<Override PartName="/word/media/evil.png" ContentType="application/vnd.'
        b'openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    )
    payload = _rezip_docx_with(
        base,
        extra={"word/media/evil.png": b"\x00" * (11 * 1024 * 1024)},
        replace={"[Content_Types].xml": manifest.replace(b"</Types>", disguise)},
    )

    result = extract_docx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.text is None
    assert result.detail == "xml-expansion-bound"

def test_extract_docx_text_oversized_content_types_manifest_returns_corrupt() -> None:
    # [Content_Types].xml is a KB-scale manifest the guard itself must parse to resolve content
    # types — one declaring over MAX_CONTENT_TYPES_BYTES is pathological and is refused BEFORE
    # the guard would DOM-parse it.
    payload = _zip_with(
        {
            "word/document.xml": _document_xml(["small body"]),
            "[Content_Types].xml": b"x" * (1024 * 1024 + 1),
        },
        compress=True,
    )

    result = extract_docx_text(payload)

    assert result.status == STATUS_CORRUPT
    assert result.detail == "xml-expansion-bound"
