"""
Role: In-memory builders for the FID synthetic ORIGINALS — part 3: the markup, rich-text, plain
      text and container formats. Renders a `DocSpec` into real HTML (a `<table>` with `<th>`
      headers, an `<ol>` list, real `<a href>` links, an optional two-column layout), into real
      RTF (control words, `\\trowd` table rows, a `HYPERLINK` field, `\\'xx` or `\\uN` escapes for
      non-ASCII), into plain text, and assembles a minimal VALID TNEF container that carries a
      body and embedded documents.
Used by: `fid_cases_d.py` (the tnef cases), `fid_cases_e.py` (the html, rtf and plain-text
         cases), `fid_cases_f.py` (encoding cases authored in html, rtf and plain text).
Depends on: `compressed-rtf` (the TNEF compressed-RTF body property only) and stdlib `struct`;
            the siblings `fid_builders` (the shared separators) and `fid_cases_a` (the `DocSpec`
            vocabulary). Nothing from
            `backend/app/`, nothing from `backend/tests/` — the TNEF wire layout is copied from
            the extractor tests' in-memory builder rather than imported.
Key invariants:
  - Byte-determinism: every builder here is a pure function of its arguments; none stamps a clock.
  - HTML links carry the destination ONLY in `href` and the label only as element text, so a
    flattener that drops one of the two fails the pair honestly. Same for the RTF `HYPERLINK`
    field and its result text.
  - The TNEF stream is VALID (signature, key, correct per-record checksums, a valid version
    attribute) so a strict reader parses it without error recovery, and every embedded attachment
    payload is at least two bytes (a reader's object loop skips sub-minimal records).
  - HTML and RTF ORIGINALS declare their encoding truthfully: `charset` in a `<meta>` and
    `\\ansicpg` plus the matching escapes in RTF. A LYING declaration is a deliberate property of
    exactly the encoding cases that ask for it, never an accident of the builder.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from html import escape as html_escape

from compressed_rtf import compress as compress_rtf

from tools.mem01_verify.fixtures.fid_builders import BULLET_MARKER, CELL_SEPARATOR
from tools.mem01_verify.fixtures.fid_cases_a import DocSpec

# ── TNEF wire constants (mirrored, never imported from the vendor library) ──
TNEF_SIGNATURE = 0x223E9F78
_ATT_TNEF_VERSION = 0x9006
_ATT_MAPI_PROPS = 0x9003
_ATT_BODY = 0x800C
_ATT_ATTACH_REND_DATA = 0x9002
_ATT_ATTACH_TITLE = 0x8010
_ATT_ATTACH_DATA = 0x800F
_MAPI_RTF_COMPRESSED = 0x1009
_MAPI_BODY_HTML = 0x1013
_SZMAPI_BINARY = 0x0102
_VALID_TNEF_VERSION = 0x10000


def html_from_doc(
    spec: DocSpec,
    *,
    charset: str = "utf-8",
    encoding: str | None = None,
    two_column_blocks: Sequence[Sequence[str]] = (),
) -> bytes:
    """Render one ORIGINAL as real HTML and encode it.

    Args:
        spec: the ORIGINAL. Paragraphs become `<p>`, list items an `<ol>`, each table a `<table>`
            with a `<caption>`, a `<th>` header row and `<td>` data rows, links `<a href>`.
        charset: the value DECLARED in the `<meta charset>` tag.
        encoding: the codec the bytes are actually produced with; defaults to `charset`. Passing a
            different value authors a deliberately LYING declaration.
        two_column_blocks: optional reading-order columns rendered as side-by-side floated divs —
            each inner sequence is one column's paragraphs, emitted column by column.

    Returns:
        The encoded HTML bytes.
    """
    parts = [f'<!DOCTYPE html><html><head><meta charset="{charset}"></head><body>']
    for text in spec.paragraphs:
        parts.append(f"<p>{html_escape(text)}</p>")
    if spec.bullets:
        parts.append("<ol>")
        parts.extend(f"<li>{html_escape(bullet)}</li>" for bullet in spec.bullets)
        parts.append("</ol>")
    for column in two_column_blocks:
        parts.append('<div style="float:left;width:50%">')
        parts.extend(f"<p>{html_escape(text)}</p>" for text in column)
        parts.append("</div>")
    for table in spec.tables:
        parts.append(f"<table><caption>{html_escape(table.caption)}</caption><tr>")
        parts.extend(f"<th>{html_escape(header)}</th>" for header in table.headers)
        parts.append("</tr>")
        for row in table.rows:
            parts.append("<tr>")
            parts.extend(f"<td>{html_escape(value)}</td>" for value in row)
            parts.append("</tr>")
        parts.append("</table>")
    for link in spec.links:
        anchor = f'<a href="{html_escape(link.destination)}">{html_escape(link.label)}</a>'
        parts.append(f"<p>{anchor}</p>")
    for text in spec.trailing:
        parts.append(f"<p>{html_escape(text)}</p>")
    parts.append("</body></html>")
    return "".join(parts).encode(encoding or charset)


def rtf_escape(text: str, *, codepage: int) -> str:
    """Escape one literal for an RTF body: ASCII verbatim, everything else as a `\\uN` escape.

    A `\\uN?` escape carries the Unicode scalar itself plus an ASCII fallback character, which is
    how a real word processor writes text outside the declared code page.

    Args:
        text: the literal scalars.
        codepage: the declared `\\ansicpg` code page — bytes expressible in it are still written
            as `\\uN` so the ORIGINAL stays independent of the reader's code-page handling.

    Returns:
        The escaped RTF fragment.
    """
    del codepage  # declared for call-site clarity; escaping is code-page independent by design
    out: list[str] = []
    for scalar in text:
        if scalar in "\\{}":
            out.append("\\" + scalar)
        elif ord(scalar) < 128:
            out.append(scalar)
        else:
            out.append(f"\\u{ord(scalar)}?")
    return "".join(out)


def rtf_from_doc(
    spec: DocSpec, *, codepage: int = 1251, page_break_before_tables: bool = False
) -> bytes:
    """Render one ORIGINAL as real RTF: paragraphs, a list, `\\trowd` table rows and a link field.

    Args:
        spec: the ORIGINAL.
        codepage: the code page declared in the `\\ansicpg` control word.
        page_break_before_tables: emit a `\\page` break before the first table, so the case carries
            a real page boundary whose reading order must survive.

    Returns:
        The ASCII-safe RTF bytes (non-ASCII scalars ride as `\\uN` escapes).
    """
    parts = [r"{\rtf1\ansi\ansicpg", str(codepage), r"\deff0{\fonttbl{\f0 Arial;}}", "\n"]
    for text in spec.paragraphs:
        parts.append(r"\pard " + rtf_escape(text, codepage=codepage) + "\\par\n")
    for bullet in spec.bullets:
        parts.append(r"\pard " + rtf_escape(BULLET_MARKER + bullet, codepage=codepage) + "\\par\n")
    if page_break_before_tables and spec.tables:
        parts.append("\\page\n")
    for table in spec.tables:
        parts.append(r"\pard " + rtf_escape(table.caption, codepage=codepage) + "\\par\n")
        for row_values in [table.headers, *table.rows]:
            parts.append(r"\trowd\trgaph100")
            parts.extend(f"\\cellx{1500 * (index + 1)}" for index in range(len(row_values)))
            for value in row_values:
                parts.append(" " + rtf_escape(value, codepage=codepage) + r"\cell ")
            parts.append("\\row\n")
    for link in spec.links:
        destination = rtf_escape(link.destination, codepage=codepage)
        label = rtf_escape(link.label, codepage=codepage)
        parts.append(
            r"\pard {\field{\*\fldinst HYPERLINK \""
            + destination
            + r"\"}{\fldrslt "
            + label
            + "}}\\par\n"
        )
    for text in spec.trailing:
        parts.append(r"\pard " + rtf_escape(text, codepage=codepage) + "\\par\n")
    parts.append("}")
    return "".join(parts).encode("ascii")


def flat_text_lines(spec: DocSpec) -> tuple[str, ...]:
    """The frozen flat-line render of an ORIGINAL: one line per block, in the spec's order.

    Table cells are joined by the shared cell separator; a link is ONE line carrying its label
    followed by its destination in angle brackets, in the link's position in the flow.

    Args:
        spec: the ORIGINAL.

    Returns:
        The lines, without trailing newlines.
    """
    lines = list(spec.paragraphs)
    lines.extend(BULLET_MARKER + bullet for bullet in spec.bullets)
    for table in spec.tables:
        lines.append(table.caption)
        lines.append(CELL_SEPARATOR.join(table.headers))
        lines.extend(CELL_SEPARATOR.join(row) for row in table.rows)
    lines.extend(f"{link.label} <{link.destination}>" for link in spec.links)
    lines.extend(spec.trailing)
    return tuple(lines)


def text_from_doc(spec: DocSpec, *, encoding: str = "utf-8", newline: str = "\n") -> bytes:
    """Render one ORIGINAL as plain text in the specification's frozen line order.

    Args:
        spec: the ORIGINAL.
        encoding: the codec the bytes are produced with.
        newline: the line separator (`"\\r\\n"` authors a CRLF original).

    Returns:
        The encoded plain-text bytes.
    """
    return newline.join(flat_text_lines(spec)).encode(encoding)


def tnef_attribute(level: int, name: int, attribute_type: int, data: bytes) -> bytes:
    """One TNEF attribute record: level u8 + name u16 + type u16 + length u32 + data + checksum."""
    header = struct.pack("<BHHI", level, name, attribute_type, len(data))
    return header + data + struct.pack("<H", sum(data) & 0xFFFF)


def _mapi_binary_property(property_code: int, payload: bytes) -> bytes:
    """One MAPI binary property block (type, name, count, length, NUL-padded payload)."""
    padding = b"\x00" * (-len(payload) % 4)
    return (
        struct.pack("<HH", _SZMAPI_BINARY, property_code)
        + struct.pack("<I", 1)
        + struct.pack("<I", len(payload))
        + payload
        + padding
    )


def build_tnef(
    body: bytes | None = None,
    rtf_body: bytes | None = None,
    html_body: bytes | None = None,
    attachments: Sequence[tuple[bytes, bytes]] = (),
) -> bytes:
    """Assemble a minimal VALID TNEF container (signature + key + attribute records).

    Args:
        body: optional plain-text body bytes.
        rtf_body: optional RTF source, LZFu-compressed into the compressed-RTF MAPI property
            exactly as a mail client ships it.
        html_body: optional HTML body bytes (must not end in NUL — padding removal would eat it).
        attachments: `(name_bytes, payload_bytes)` embedded files, each payload at least 2 bytes.

    Returns:
        The complete TNEF stream bytes.
    """
    stream = struct.pack("<IH", TNEF_SIGNATURE, 0x1234)
    stream += tnef_attribute(1, _ATT_TNEF_VERSION, 0x6, struct.pack("<I", _VALID_TNEF_VERSION))
    properties = b""
    property_count = 0
    if rtf_body is not None:
        properties += _mapi_binary_property(_MAPI_RTF_COMPRESSED, compress_rtf(rtf_body))
        property_count += 1
    if html_body is not None:
        properties += _mapi_binary_property(_MAPI_BODY_HTML, html_body)
        property_count += 1
    if property_count:
        stream += tnef_attribute(
            1, _ATT_MAPI_PROPS, 0x6, struct.pack("<I", property_count) + properties
        )
    if body is not None:
        stream += tnef_attribute(1, _ATT_BODY, 0x2, body)
    for name, payload in attachments:
        stream += tnef_attribute(2, _ATT_ATTACH_REND_DATA, 0x6, b"\x00" * 14)
        stream += tnef_attribute(2, _ATT_ATTACH_TITLE, 0x7, name)
        stream += tnef_attribute(2, _ATT_ATTACH_DATA, 0x6, payload)
    return stream
