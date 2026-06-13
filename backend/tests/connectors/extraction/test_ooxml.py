"""
Role: Direct unit tests for the SHARED OOXML zip-validation lifted into extraction.ooxml — the
      gates docx + xlsx both inherit, tested once at the source: the OLE-magic probe, the EOCD
      pre-gate (declared member flood + zip64 sentinels, rejected BEFORE ZipFile construction), the
      decompressed-expansion raw-bytes bound, the DOM-parsed XML-parts bound (suffix + the
      [Content_Types].xml content-type disguise + the pathological manifest), and the happy-path
      pass-through. The per-format extractors re-exercise these through their own public seams
      (test_docx_guards / test_xlsx); this file pins the shared contract in isolation.
Used by: pytest (tests/connectors/extraction).
Depends on: app.connectors.extraction.ooxml, .extraction_result, the in-memory zip builders in
            test_docx (shared zip helpers) + the conftest builders.
"""

from __future__ import annotations

import struct
import zipfile
from io import BytesIO

from app.connectors.extraction.extraction_result import STATUS_CORRUPT
from app.connectors.extraction.ooxml import (
    MAX_CONTENT_TYPES_BYTES,
    MAX_DECOMPRESSED_BYTES,
    MAX_XML_PARTS_BYTES,
    is_ole_container,
    validate_ooxml_package,
)
from tests.connectors.extraction.test_docx import _document_xml, _zip_with

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# — is_ole_container —


def test_is_ole_container_true_for_ole_magic() -> None:
    assert is_ole_container(OLE_MAGIC + b"\x00" * 32) is True


def test_is_ole_container_false_for_zip_magic() -> None:
    assert is_ole_container(b"PK\x03\x04 rest of a zip") is False


# — the happy path: a sound package passes (None) —


def test_validate_ooxml_package_sound_zip_returns_none() -> None:
    payload = _zip_with({"word/document.xml": _document_xml(["hi"])})

    assert validate_ooxml_package(payload) is None


def test_validate_ooxml_package_non_zip_returns_corrupt_class_name_only() -> None:
    marker = "PAYLOAD-SECRET-MARKER"

    result = validate_ooxml_package(f"not a zip {marker}".encode())

    assert result is not None
    assert result.status == STATUS_CORRUPT
    assert result.detail == "zipfile:BadZipFile"
    assert marker not in (result.detail or "")


# — the EOCD pre-gate (fires before ZipFile construction) —


def test_validate_ooxml_package_eocd_member_flood_rejected_before_parse() -> None:
    # An EOCD declaring 50000 entries on a body that is NOT a valid zip: if construction ran first
    # it would raise BadZipFile → 'zipfile:BadZipFile'; 'zip-member-bound' proves the pre-gate won.
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 50000, 50000, 4_000_000, 0, 0)

    result = validate_ooxml_package(b"not-really-a-zip-body" + eocd)

    assert result is not None
    assert result.detail == "zip-member-bound"


def test_validate_ooxml_package_zip64_sentinel_rejected() -> None:
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0, 0)

    result = validate_ooxml_package(b"zip64-shaped-tail" + eocd)

    assert result is not None
    assert result.detail == "zip-member-bound"


# — the bounds are parameters (each format passes its own) —


def test_validate_ooxml_package_expansion_bound_is_a_parameter() -> None:
    payload = _zip_with({"word/document.xml": _document_xml(["any text here"])})

    result = validate_ooxml_package(payload, max_decompressed_bytes=8)

    assert result is not None
    assert result.detail == "zip-expansion-bound"


def test_validate_ooxml_package_member_count_bound_post_construction() -> None:
    members: dict[str, str | bytes] = {f"junk/m{i}.bin": "" for i in range(4100)}
    members["word/document.xml"] = _document_xml(["hi"])

    result = validate_ooxml_package(_zip_with(members))

    assert result is not None
    assert result.detail == "zip-member-bound"


# — the DOM-parsed XML-parts bound (suffix + disguise + manifest) —


def test_validate_ooxml_package_xml_parts_suffix_bound() -> None:
    # An *.xml member over the parts bound is rejected without parsing it.
    big_xml = "<r>" + ("a" * (200 * 1024)) + "</r>"
    payload = _zip_with({"big.xml": big_xml})

    result = validate_ooxml_package(payload, max_xml_parts_bytes=64 * 1024)

    assert result is not None
    assert result.detail == "xml-expansion-bound"


def test_validate_ooxml_package_content_type_disguised_xml_part_counted() -> None:
    # A media-named member the manifest maps to an XML content type is DOM-parsed at open — the
    # content-type pass must count its bytes even though its suffix says '.png'.
    manifest = (
        b'<?xml version="1.0"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Override PartName="/media/evil.png" ContentType="application/'
        b'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    )
    payload = _zip_with(
        {"[Content_Types].xml": manifest, "media/evil.png": b"\x00" * (200 * 1024)}
    )

    result = validate_ooxml_package(payload, max_xml_parts_bytes=64 * 1024)

    assert result is not None
    assert result.detail == "xml-expansion-bound"


def test_validate_ooxml_package_oversized_manifest_rejected() -> None:
    payload = _zip_with(
        {
            "word/document.xml": _document_xml(["small"]),
            "[Content_Types].xml": b"x" * (MAX_CONTENT_TYPES_BYTES + 1),
        }
    )

    result = validate_ooxml_package(payload)

    assert result is not None
    assert result.detail == "xml-expansion-bound"


def test_module_default_bounds_are_sane() -> None:
    # The XML-parts bound must be tighter than the decompressed-bytes bound (media headroom).
    assert MAX_XML_PARTS_BYTES < MAX_DECOMPRESSED_BYTES


def test_validate_ooxml_package_truncated_zip_local_header_corrupt() -> None:
    result = validate_ooxml_package(b"PK\x03\x04 truncated local header")

    assert result is not None
    assert result.status == STATUS_CORRUPT
    assert (result.detail or "").startswith("zipfile:")


def test_validate_ooxml_package_real_xlsx_passes() -> None:
    # A genuine openpyxl workbook (a multi-part zip) must sail through the shared gate untouched.
    from tests.connectors.extraction.conftest import build_xlsx

    payload = build_xlsx(sheets=[("S", [["a", 1]])])

    assert validate_ooxml_package(payload) is None


def test_validate_ooxml_package_zip_helper_roundtrips() -> None:
    # Sanity on the shared helper: a built zip really opens (so the gate tests above test the gate,
    # not a broken builder).
    payload = _zip_with({"word/document.xml": _document_xml(["x"])})
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        assert "word/document.xml" in archive.namelist()
