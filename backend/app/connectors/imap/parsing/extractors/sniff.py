"""
Role: Content sniffing for attachment payloads — detect_payload_kind classifies raw bytes by
      MAGIC (pdf / zip / ole / tnef) or a bounded text-decode heuristic ('text'), 'unknown'
      otherwise. Declared content types are sender-controlled; dispatch decisions over container
      interiors (TNEF embedded files, design §2.9) and the later §2.10 octet-stream rescue trust
      BYTES, never labels.
Used by: app.connectors.imap.parsing.extractors.tnef (embedded-file dispatch — this slice);
         the application/octet-stream sniff dispatch (design §2.10) reuses it in a later slice
         (deliberately NOT wired in this slice).
Depends on: stdlib only.
Key invariants:
  - PURE + BOUNDED: no I/O and no library construction — magics read a fixed prefix and the text
    heuristic decodes at most the first TEXT_PROBE_BYTES bytes, so sniffing is safe on any blob
    BEFORE any expensive parser is built (the docx EOCD pre-gate lesson: bounds/dispatch checks
    must fire before construction-time resource burn).
  - Returns EXACTLY one of KIND_PDF / KIND_ZIP / KIND_OLE / KIND_TNEF / KIND_TEXT / KIND_UNKNOWN.
  - 'pdf' tolerates a leading UTF-8 BOM and ASCII whitespace before '%PDF-' (real producers
    prepend both); the zip/ole/tnef magics must sit at offset 0 (their parsers require it).
  - 'text' = the probe window decodes utf-8-strict or cp1252-strict with ≥ TEXT_PRINTABLE_RATIO
    printable characters (whitespace counted as printable); a multi-byte utf-8 sequence SPLIT by
    the window boundary is not treated as corruption. Empty payload → 'unknown'.
"""

from __future__ import annotations

KIND_PDF = "pdf"
KIND_ZIP = "zip"
KIND_OLE = "ole"
KIND_TNEF = "tnef"
KIND_TEXT = "text"
KIND_UNKNOWN = "unknown"

# How many leading bytes the text heuristic (and the pdf whitespace skip) may examine.
TEXT_PROBE_BYTES = 4096
# Minimum fraction of printable (or whitespace) characters for the 'text' verdict.
TEXT_PRINTABLE_RATIO = 0.9

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# The TNEF stream signature 0x223E9F78, little-endian on the wire.
_TNEF_MAGIC = (0x223E9F78).to_bytes(4, "little")
_UTF8_BOM = b"\xef\xbb\xbf"
_ASCII_WHITESPACE = b" \t\r\n\x0b\x0c"
# str.isprintable() is False for these, but they are normal text-file furniture.
_TEXT_WHITESPACE = frozenset("\t\n\r\x0b\x0c")


def detect_payload_kind(data: bytes) -> str:
    """Classify a payload by its bytes: 'pdf' | 'zip' | 'ole' | 'tnef' | 'text' | 'unknown'.

    Magic checks first (zip/ole/tnef exact at offset 0; pdf tolerates a leading UTF-8 BOM and
    ASCII whitespace), then the bounded text heuristic over the first TEXT_PROBE_BYTES bytes.
    Pure, never raises, reads no more than the probe window.

    Args:
        data: the raw payload bytes (any size; only a bounded prefix is examined).

    Returns:
        One of the KIND_* constants; 'unknown' for empty or unclassifiable payloads.
    """
    if data.startswith(_ZIP_MAGIC):
        return KIND_ZIP
    if data.startswith(_OLE_MAGIC):
        return KIND_OLE
    if data.startswith(_TNEF_MAGIC):
        return KIND_TNEF
    if _looks_like_pdf(data):
        return KIND_PDF
    if _decodes_as_text(data[:TEXT_PROBE_BYTES], len(data)):
        return KIND_TEXT
    return KIND_UNKNOWN


def _looks_like_pdf(data: bytes) -> bool:
    """'%PDF-' after an optional UTF-8 BOM and optional ASCII whitespace (bounded window)."""
    head = data[:TEXT_PROBE_BYTES]
    head = head.removeprefix(_UTF8_BOM)
    return head.lstrip(_ASCII_WHITESPACE).startswith(_PDF_MAGIC)


def _decodes_as_text(probe: bytes, total_length: int) -> bool:
    """True when the probe window decodes strictly and is ≥ TEXT_PRINTABLE_RATIO printable."""
    if not probe:
        return False
    decoded = _strict_probe_decode(probe, total_length)
    if not decoded:
        return False
    printable = sum(1 for ch in decoded if ch.isprintable() or ch in _TEXT_WHITESPACE)
    return printable / len(decoded) >= TEXT_PRINTABLE_RATIO


def _strict_probe_decode(probe: bytes, total_length: int) -> str | None:
    """probe decoded utf-8-strict, else cp1252-strict, else None (genuinely not text).

    A multi-byte utf-8 sequence SPLIT by the probe window (the failure starts within the last
    3 bytes and the payload continues past the window) is not corruption — the part before the
    split is retried so a large clean utf-8 file never misclassifies on the cut.
    """
    try:
        return probe.decode("utf-8")
    except UnicodeDecodeError as utf8_error:
        if total_length > len(probe) and utf8_error.start >= len(probe) - 3:
            try:
                return probe[: utf8_error.start].decode("utf-8")
            except UnicodeDecodeError:
                pass
    try:
        return probe.decode("cp1252")
    except UnicodeDecodeError:
        return None
