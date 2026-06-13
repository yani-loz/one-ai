"""
Role: Shared OOXML (zip) package validation for docx + xlsx — every OOXML format is a zip and
      inherits the same bomb defenses. The pre-parse gate battery (the EOCD pre-gate, the
      member-count bound, the decompressed-expansion bound, the XML-parts/DOM-expansion bound with
      its [Content_Types].xml content-type-disguise resolution) plus the OLE-magic probe and the
      bounds constants. Lifted out of extraction/docx.py (A2 + reuse: xlsx is an OOXML zip too, so
      the bomb defenses are identical — one source, never two copies that can drift).
Used by: app.connectors.extraction.docx (the docx pipeline) and .xlsx (the xlsx/xlsm pipeline);
         their guard tests exercise these gates THROUGH each format's public extractor. The arrow
         points IN — this leaf imports nothing back from any extractor.
Depends on: app.connectors.extraction.extraction_result (STATUS_CORRUPT + the contract); stdlib
            zipfile + struct + xml.etree (the manifest parse). NOTHING from any specific connector
            (connector-agnostic), and nothing from a specific OOXML format — the bounds are passed
            IN by the caller so each format keeps its own monkeypatchable constants.
Key invariants:
  - validate_ooxml_package runs FIVE gates, ALL before any document XML is materialized, and
    returns an ExtractionResult on the first violation (None ⇒ the package may proceed):
    (0) the RAW EOCD record's declared entry count / directory size (read from the tail bytes —
        fires BEFORE ZipFile construction, whose directory parse is itself the resource burn) →
        `corrupt` 'zip-member-bound';
    (1) the payload opens as a zip at all (non-zip garbage / broken zips → `corrupt`, exception
        CLASS name only — zipfile messages can embed payload fragments);
    (2) the summed DECLARED decompressed size of every member < max_decompressed_bytes (the
        raw-bytes bound — python-docx/openpyxl materialize part blobs) → `corrupt`
        'zip-expansion-bound';
    (3) the POST-construction member count < max_zip_members (belt-and-braces truth-after-parse vs
        a lying EOCD) → `corrupt` 'zip-member-bound';
    (4) the members that will be DOM-PARSED stay under max_xml_parts_bytes (the parse-MEMORY bound
        — lxml/et-xmlfile trees cost far more than their XML bytes), resolved BOTH by member suffix
        (*.xml / *.rels) AND by [Content_Types].xml content type (the part class is picked by
        CONTENT TYPE, so an Override can disguise an XML part behind a media-looking name) →
        `corrupt` 'xml-expansion-bound'.
  - is_ole_container is the OLE compound-file magic probe (D0 CF 11 E0 …): an ECMA-376-encrypted
    (password-protected) OOXML package arrives as an OLE file, NOT a zip — the caller maps it to
    `encrypted` BEFORE any zip gate runs.
  - The bounds are PARAMETERS, not module constants read here: each format module owns its own
    MAX_* names (so a format's guard test can monkeypatch them) and passes them in. The constants
    below are the shared DEFAULTS both formats start from.
  - `detail` carries exception CLASS NAMES / fixed phrases only — never str(exc) (zipfile/lxml
    messages can embed payload fragments) and never document content. The [Content_Types].xml
    parse uses stdlib ElementTree (expat ≥2.4 has billion-laughs protection; no external entities).
"""

from __future__ import annotations

import struct
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from app.connectors.extraction.extraction_result import STATUS_CORRUPT, ExtractionResult

# OLE compound-file magic (D0 CF 11 E0 A1 B1 1A E1): an ECMA-376-encrypted (password-protected)
# OOXML package — or a mislabeled legacy OLE binary (.doc/.xls), equally unreadable here. The
# caller maps it to `encrypted` for the declared-OOXML dispatch (design §2.3/§2.4).
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Central-directory sanity bounds: a zip declaring more members than this is pathological for an
# OOXML package (real docx/xlsx carry dozens) and only exists to burn CPU/memory in directory
# scans. Enforced from the RAW EOCD record BEFORE ZipFile construction (measured: a crafted
# 18 MB / 200k-member zip costs ~12 s CPU + ~115 MB inside ZipFile.__init__ alone — the directory
# parse IS the attack, so the gate cannot run after it).
MAX_ZIP_MEMBERS = 4096
# The declared central-directory byte size is bounded too (a liar could declare few entries but a
# huge directory span): 4096 entries x ~0.5 KB of name/extra headroom each.
MAX_CENTRAL_DIR_BYTES = 2 * 1024 * 1024

# Decompressed-expansion ceiling across ALL zip members (declared file_size sum) — the raw-BYTES
# bound. The seam's 50 MB guard bounds COMPRESSED bytes only; a crafted bomb compresses far beyond
# 10:1, and the OOXML reader materializes every part's blob at open. The headroom over
# MAX_XML_PARTS_BYTES exists for media members (stored ~1:1, never DOM-parsed).
MAX_DECOMPRESSED_BYTES = 100 * 1024 * 1024

# Declared-size ceiling for the members that get DOM-PARSED (lxml for docx, et-xmlfile for xlsx) —
# the parse-MEMORY bound. Tiny-element OOXML XML inflates many times its byte size as a parse tree
# (measured ~15x for WordprocessingML on this repo's lxml), so 10 MB of XML caps the transient tree
# near ~150 MB. Legitimate OOXML XML is low-MB even for huge documents.
MAX_XML_PARTS_BYTES = 10 * 1024 * 1024

# [Content_Types].xml is a KB-scale manifest; the guard parses it (bounded) to resolve which
# members the reader will DOM-parse. A manifest declaring more than this is itself pathological.
MAX_CONTENT_TYPES_BYTES = 1 * 1024 * 1024

# End-of-Central-Directory record: signature + the offsets of the fields we read.
# Layout: sig(4) disk(2) cd_disk(2) entries_this_disk(2) TOTAL_ENTRIES(2) CD_SIZE(4) ...
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_MIN_BYTES = 22
_EOCD_MAX_COMMENT = 65535

# OPC content-types manifest: name, pre-braced namespace, and the member suffixes DOM-parsed
# regardless of what the manifest claims.
_CONTENT_TYPES_XML = "[Content_Types].xml"
_CONTENT_TYPES_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_XML_MEMBER_SUFFIXES = (".xml", ".rels")


def is_ole_container(payload: bytes) -> bool:
    """True when the payload starts with the OLE compound-file magic (password-protected OOXML)."""
    return payload.startswith(OLE_MAGIC)


def validate_ooxml_package(
    payload: bytes,
    *,
    max_zip_members: int = MAX_ZIP_MEMBERS,
    max_central_dir_bytes: int = MAX_CENTRAL_DIR_BYTES,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
    max_xml_parts_bytes: int = MAX_XML_PARTS_BYTES,
    max_content_types_bytes: int = MAX_CONTENT_TYPES_BYTES,
) -> ExtractionResult | None:
    """Pre-parse OOXML-zip validation; None when the package may proceed to extraction.

    The five-gate bomb battery (see the module docstring's invariants) shared by docx + xlsx. All
    gates run BEFORE any document XML is materialized. Each bound is a parameter so a format module
    can pass its own (monkeypatchable) MAX_* constant; the defaults are the shared module constants.

    Args:
        payload: the raw OOXML (zip) bytes — the caller has already enforced the global size
            ceiling AND mapped an OLE container to `encrypted` (is_ole_container) before this runs.
        max_zip_members: member-count ceiling (EOCD pre-gate + post-construction check).
        max_central_dir_bytes: declared central-directory byte-span ceiling (EOCD pre-gate).
        max_decompressed_bytes: summed declared-decompressed-size ceiling (raw-bytes bound).
        max_xml_parts_bytes: ceiling on the members that will be DOM-parsed (parse-memory bound).
        max_content_types_bytes: ceiling on the [Content_Types].xml manifest the guard parses.

    Returns:
        An ExtractionResult (STATUS_CORRUPT with a fixed-phrase / class-name detail) on the first
        violated gate, or None when the package is structurally sound enough to hand to the reader.
    """
    if _zip_directory_over_bound(payload, max_zip_members, max_central_dir_bytes):
        return ExtractionResult(None, STATUS_CORRUPT, detail="zip-member-bound")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            declared_expansion = sum(member.file_size for member in archive.infolist())
            if declared_expansion > max_decompressed_bytes:
                return ExtractionResult(None, STATUS_CORRUPT, detail="zip-expansion-bound")
            if len(archive.infolist()) > max_zip_members:
                return ExtractionResult(None, STATUS_CORRUPT, detail="zip-member-bound")
            if _xml_parts_over_bound(archive, max_xml_parts_bytes, max_content_types_bytes):
                return ExtractionResult(None, STATUS_CORRUPT, detail="xml-expansion-bound")
    except Exception as zip_error:  # BadZipFile and friends — class name only, never str(exc)
        return ExtractionResult(None, STATUS_CORRUPT, detail=f"zipfile:{type(zip_error).__name__}")
    return None


def _zip_directory_over_bound(
    payload: bytes, max_zip_members: int, max_central_dir_bytes: int
) -> bool:
    """EOCD pre-gate: True when the zip DECLARES a pathological central directory.

    Reads the End-of-Central-Directory record straight from the raw tail bytes — total entry count
    (le16 at +10) and central-directory size (le32 at +12) — BEFORE zipfile.ZipFile ever parses the
    directory (construction builds a ZipInfo per entry; the parse itself is the resource burn the
    member bound exists to prevent). The zip64 sentinels (0xFFFF entries / 0xFFFFFFFF size) mean
    "real value elsewhere but at least this large", which already exceeds both bounds — rejected
    without parsing the zip64 record. No EOCD found → False: ZipFile will raise BadZipFile and the
    package degrades to `corrupt` through the normal path.
    """
    tail = payload[-(_EOCD_MIN_BYTES + _EOCD_MAX_COMMENT) :]
    position = tail.rfind(_EOCD_SIGNATURE)
    if position == -1 or len(tail) - position < _EOCD_MIN_BYTES:
        return False
    total_entries = struct.unpack_from("<H", tail, position + 10)[0]
    directory_size = struct.unpack_from("<I", tail, position + 12)[0]
    return total_entries > max_zip_members or directory_size > max_central_dir_bytes


def _xml_parts_over_bound(
    archive: zipfile.ZipFile, max_xml_parts_bytes: int, max_content_types_bytes: int
) -> bool:
    """True when the members that will be DOM-parsed declare more than max_xml_parts_bytes.

    Two passes, cheapest first. Pass 1 sums the declared file_size of every *.xml / *.rels member
    straight off the zip directory — nothing is decompressed — and also rejects a pathological
    [Content_Types].xml (> max_content_types_bytes) before pass 2 would parse it. Pass 2 adds
    members the manifest maps to an XML content type under a NON-xml name: the OOXML reader selects
    the part class by CONTENT TYPE, so an Override like '/word/media/evil.png → …styles+xml' is
    DOM-parsed at open despite its media suffix. Pass 1's bound makes pass 2's manifest parse safe.
    """
    declared_sizes = {member.filename: member.file_size for member in archive.infolist()}
    xml_named = {name for name in declared_sizes if name.lower().endswith(_XML_MEMBER_SUFFIXES)}
    xml_named_total = sum(declared_sizes[name] for name in xml_named)
    if xml_named_total > max_xml_parts_bytes:
        return True
    if declared_sizes.get(_CONTENT_TYPES_XML, 0) > max_content_types_bytes:
        return True
    disguised = _xml_typed_member_names(archive) - xml_named
    disguised_total = sum(declared_sizes.get(name, 0) for name in disguised)
    return xml_named_total + disguised_total > max_xml_parts_bytes


def _xml_typed_member_names(archive: zipfile.ZipFile) -> set[str]:
    """Member names [Content_Types].xml maps to an XML content type (Override + Default rules).

    Absent/unparseable manifest → empty set: the reader then rejects the whole package and the
    suffix pass already bounds the parts it would touch. The caller has already bounded the
    manifest's declared size before this parse.
    """
    try:
        manifest_root = ElementTree.fromstring(archive.read(_CONTENT_TYPES_XML))
    except Exception:
        return set()
    xml_extensions = {
        (entry.get("Extension") or "").lower()
        for entry in manifest_root.iter(f"{_CONTENT_TYPES_NS}Default")
        if _is_xml_content_type(entry.get("ContentType"))
    }
    xml_typed = {
        (entry.get("PartName") or "").lstrip("/")
        for entry in manifest_root.iter(f"{_CONTENT_TYPES_NS}Override")
        if _is_xml_content_type(entry.get("ContentType"))
    }
    for member in archive.namelist():
        extension = member.rsplit(".", 1)[-1].lower() if "." in member else ""
        if extension in xml_extensions:
            xml_typed.add(member)
    return xml_typed


def _is_xml_content_type(content_type: str | None) -> bool:
    """OPC content types the reader parses into a DOM: '+xml' suffix or a bare XML media type."""
    if not content_type:
        return False
    normalized = content_type.strip().lower()
    return normalized.endswith("+xml") or normalized in ("application/xml", "text/xml")
