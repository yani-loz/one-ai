"""
Role: TNEF (winmail.dat) attachment extraction (design §2.9) — BODY CARRIER first: 99% of the
      corpus's containers hold the email's real content as a body (compressed RTF → LZFu
      decompress via compressed-rtf → striprtf flatten; else HTML → the email_parser HTML
      flattener; else plain text → the shared charset chain). Then DEPTH-1 recursion into the
      embedded files (tnef.attachments — the SAME list the dedup-key interior digest walks, so
      the two TNEF readers share one notion of "embedded attachment"): each payload is
      magic-SNIFFED (never trusts names) and dispatched to the pdf/docx/text extractors, each
      rendered as a '[embedded: SAFE_NAME]' section; nested tnef/ole/unknown payloads are
      skip-marked, never recursed.
Used by: app.connectors.imap.parsing.attachment_extractor (dispatches application/ms-tnef);
         scripts.backfill_attachment_extraction (via the seam).
Depends on: tnefparse (LGPL-3.0 — import-only is commercially fine, never vendored/modified;
            pinned, design §2.9), compressed-rtf (MIT, invoked through tnefparse's rtfbody
            property) + striprtf (BSD-3-Clause) — verified GPL-free in the installed metadata;
            .extractors.sniff (magic dispatch), .extractors.pdf (extract_pdf_text +
            MAX_EXTRACTED_CHARS, the shared stored-text cap), .extractors.docx
            (extract_docx_text), .extraction_result (the contract), .email_parser
            (sanitize_body_text + html_to_text + decode_charset_chain — the SINGLE
            sanitization / HTML-flatten / charset-decode sources, never re-implemented),
            .dedup_key (MAX_COMPRESSED_RTF_BYTES — the shared LZFu-bomb bound, single-sourced
            there because the reverse import would cycle through email_parser).
Key invariants:
  - extract_tnef_text NEVER raises: tnefparse raises diverse internals on corrupt blobs
    (existing precedent in dedup_key.py) → `corrupt` with the class name only; a final
    catch-all guards even our own bugs.
  - VENDOR LOGGER MUTED at import: tnefparse interpolates RAW PAYLOAD BYTES into WARNINGs
    (live-reproduced 2026-06-12: "Invalid TNEF Version adde0000" — four payload bytes
    hex-formatted) and str(exc) into ERRORs (mapi.decode_mapi — exception strings can embed
    payload fragments), so the 'tnefparse' logger is muted to CRITICAL once at import — the
    same process-wide posture as pypdf/pdfminer in pdf.py. dedup_key.py duplicates the SAME
    idempotent mute (it parses the same containers for the interior digest and can be imported
    without this module). compressed-rtf and striprtf were live-checked CLEAN (zero logging
    calls in either source).
  - The body layers degrade INDEPENDENTLY (rtf → html → plain → none): a corrupt
    compressed-RTF stream never costs the html/plain candidate, and a body-less container
    still yields its embedded files.
  - DECOMPRESSION-BOMB BOUNDS: the COMPRESSED RTF property bytes are bounded at
    MAX_COMPRESSED_RTF_BYTES BEFORE any decompression (tnefparse's `rtfbody` is a LAZY property
    that LZFu-decompresses on access — never touched until the bound passes; measured 8.0x
    expansion on the corpus, so 8MB compressed ⇒ ≤ ~64MB transient + striprtf's second copy).
    Over-bound → the rtf body is skipped (detail notes 'rtf-body-over-bound'), html/plain and
    the embedded files still process. The html/plain body SOURCES are equally bounded at
    MAX_BODY_SOURCE_BYTES before flattening.
  - KEY-CONSISTENCY (mirrored in dedup_key.py): the dedup key and this extractor read the SAME
    TNEF signals — the striprtf-FLATTENED RTF body + the embedded attachments' bytes, under the
    SAME MAX_COMPRESSED_RTF_BYTES bound — so content this extractor can materialize is always
    key-distinguished (two distinct emails differing only inside winmail.dat never fold).
  - Per-embedded-file failure NEVER fails the container: every file renders as a section —
    '[embedded: SAFE_NAME]' + extracted text, or '[embedded: SAFE_NAME — STATUS]' when no text
    — and SAFE_NAME is sanitized (C0/C1/DEL controls, brackets and unicode line separators
    stripped; length-capped) so a crafted filename cannot forge section markers or break the
    layout (U+0085 NEL included — some renderers honor it as a line break). DEPTH-1 ONLY: an
    embedded tnef/ole/unknown payload is skip-marked, never recursed; zip payloads go to
    extract_docx_text (whose own pre-parse gates bound them — its `corrupt` verdict on a
    non-WordprocessingML zip renders as unsupported_format, not corrupt noise).
  - Embedded MARKUP IS FLATTENED, never stored raw (the EQ-4 invariant applies INSIDE the
    container too): a sniffed-text payload opening (whitespace/BOM-tolerant, case-insensitive)
    with '<html'/'<!doctype' flattens through the email-body HTML flattener; with '{\\rtf'
    through striprtf. And the FULL decoded text must clear EMBEDDED_TEXT_MIN_PRINTABLE_RATIO —
    the sniffer probes only the first 4KB, so a binary blob behind an ASCII preamble still
    sniffs as text; its soup is skip-marked, never stored.
  - BOUNDED: at most MAX_EMBEDDED_FILES embedded files are processed (the rest collapse into
    one '[+N more embedded files skipped]' marker) and the section loop bails as soon as the
    materialized text exceeds MAX_EXTRACTED_CHARS → `truncated`, the capped text IS stored.
  - Markers alone never masquerade as content: body and embedded EXTRACTED text both empty →
    `empty` (text=None), even when skip/status markers exist (the pdf page-marker lesson).
  - `detail` carries fixed phrases / exception class names / counts only — never payload
    content (it lands in the DB and could echo into logs).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from striprtf.striprtf import rtf_to_text
from tnefparse import TNEF
from tnefparse.tnef import TNEFAttachment

from app.connectors.imap.parsing.dedup_key import MAX_COMPRESSED_RTF_BYTES
from app.connectors.imap.parsing.email_parser import (
    decode_charset_chain,
    html_to_text,
    sanitize_body_text,
)
from app.connectors.imap.parsing.extraction_result import (
    STATUS_CORRUPT,
    STATUS_EMPTY,
    STATUS_EXTRACTED,
    STATUS_TRUNCATED,
    STATUS_UNSUPPORTED_FORMAT,
    ExtractionResult,
)
from app.connectors.imap.parsing.extractors.common import (
    MAX_EXTRACTED_CHARS,
)
from app.connectors.imap.parsing.extractors.common import (
    package_version as _package_version,
)
from app.connectors.imap.parsing.extractors.docx import extract_docx_text
from app.connectors.imap.parsing.extractors.pdf import extract_pdf_text
from app.connectors.imap.parsing.extractors.sniff import (
    KIND_PDF,
    KIND_TEXT,
    KIND_ZIP,
    detect_payload_kind,
)

logger = logging.getLogger(__name__)

# tnefparse interpolates RAW PAYLOAD BYTES into its log records (live-reproduced 2026-06-12:
# WARNING "Invalid TNEF Version adde0000" hex-formats four payload bytes; mapi.decode_mapi logs
# str(exc) at ERROR, and library exception strings can embed payload fragments) — tenant
# attachment content must never reach logs (security.md). Muted to CRITICAL once, at import:
# race-free across the to_thread extraction workers (the pypdf/pdfminer posture in pdf.py).
# dedup_key.py carries the SAME idempotent mute: it parses the same containers for the interior
# digest and can be imported without this module — neither import order leaves a window.
logging.getLogger("tnefparse").setLevel(logging.CRITICAL)

# Depth-1 recursion bound: at most this many embedded files are dispatched per container; the
# remainder collapses into one '[+N more embedded files skipped]' marker. The corpus's containers
# carry a handful of embeds (43% have any at all) — beyond this is pathological.
MAX_EMBEDDED_FILES = 64

# The html/plain body SOURCE bound: those bodies arrive already materialized inside the parsed
# container (no decompression step), but flattening a pathological source through html2text /
# the charset chain is unbounded CPU plus a second full copy — over-bound sources are skipped
# and the body cascade falls through to the next layer. (The compressed-RTF bound,
# MAX_COMPRESSED_RTF_BYTES, is imported from dedup_key — the SAME bound on BOTH TNEF readers,
# the KEY-CONSISTENCY invariant.)
MAX_BODY_SOURCE_BYTES = 16 * 1024 * 1024

# Section-marker safety: embedded file names are length-capped after the control/bracket strip
# so a crafted name can neither forge '[embedded: …]' markers nor bloat the stored text.
# \x80-\x9f covers the C1 controls (U+0085 NEL is a line break to some renderers — the same
# forged-marker surface as the C0 newline strip).
MAX_EMBEDDED_NAME_CHARS = 80
_UNSAFE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\[\]\u2028\u2029]")

# Embedded-text markup sniffing (the EQ-4 flatten invariant inside the container): a decoded
# text opening with one of these (whitespace/BOM-tolerant, case-insensitive) is FLATTENED,
# never stored as raw markup source.
_HTML_TEXT_PREFIXES = ("<html", "<!doctype")
_RTF_TEXT_PREFIX = "{\\rtf"
_LEADING_TEXT_NOISE = "\ufeff \t\r\n\x0b\x0c"  # BOM + ASCII whitespace before the markup probe

# FULL-TEXT printable floor for embedded sniffed-text payloads: detect_payload_kind probes only
# the first TEXT_PROBE_BYTES (4KB), so an ASCII preamble on a binary blob still sniffs as text —
# below this ratio over the WHOLE decoded text the payload is skip-marked, never stored as soup.
EMBEDDED_TEXT_MIN_PRINTABLE_RATIO = 0.7
# str.isprintable() is False for these, but they are normal text-file furniture (sniff.py's
# notion of printable whitespace).
_TEXT_WHITESPACE = frozenset("\t\n\r\x0b\x0c")

_TNEFPARSE = "tnefparse"


def extract_tnef_text(payload: bytes) -> ExtractionResult:
    """Extract a TNEF container's body + depth-1 embedded-file text; never raises.

    Pipeline: tnefparse.TNEF parse (corrupt blob → `corrupt`, class name only) → the body
    cascade (rtf → html → plain; design §2.9: the body IS the email's real content for Outlook
    TNEF mail; the compressed-RTF property is bounded BEFORE decompression — over-bound → body
    layer skipped, detail notes 'rtf-body-over-bound'; html/plain sources bounded at
    MAX_BODY_SOURCE_BYTES) → per-embedded-file sniff + dispatch (pdf/docx/text extractors;
    sniffed text passes the FULL-TEXT printable guard and html/rtf-opening text is FLATTENED,
    never stored raw; tnef/ole/unknown skip-marked, zip-corrupt rendered unsupported_format),
    each as a '[embedded: SAFE_NAME]' section, bounded by MAX_EMBEDDED_FILES and the
    MAX_EXTRACTED_CHARS early bail (→ `truncated`, capped text STORED). Body and embedded text
    both empty → `empty`.

    Args:
        payload: the raw TNEF (winmail.dat) bytes (the seam has already enforced the global
            size ceiling).

    Returns:
        An ExtractionResult; text is non-None only for the text-bearing statuses
        (extracted / truncated). Provenance is always tnefparse + its installed version.
    """
    try:
        return _extract(payload)
    except Exception as unexpected:  # the seam's NEVER-raise contract: even our bugs degrade
        # Class name ONLY - no exc_info: a formatted traceback ends with "Class: str(exc)",
        # and library exception strings can embed payload fragments (the pdf.py lesson).
        logger.warning("tnef extraction: unexpected failure (%s)", type(unexpected).__name__)
        return ExtractionResult(
            None, STATUS_CORRUPT, detail=f"unexpected:{type(unexpected).__name__}"
        )


def _extract(payload: bytes) -> ExtractionResult:
    """The real pipeline (see extract_tnef_text); may raise — the public wrapper degrades."""
    try:
        container = TNEF(payload)
    except Exception as tnef_error:  # tnefparse raises diverse internals on corrupt blobs
        return ExtractionResult(
            None, STATUS_CORRUPT, detail=f"tnefparse:{type(tnef_error).__name__}"
        )
    body_text, body_kind, body_note = _container_body_text(container)
    embedded = list(container.attachments)
    detail = f"body={body_kind} embedded_files={len(embedded)}"
    if body_note:
        detail = f"{detail} {body_note}"

    chunks: list[str] = []
    content_chars = 0  # body + embedded EXTRACTED text only — markers never count as content
    materialized = 0  # length of '\n\n'.join(chunks) so far — the early-bail counter
    bail_reason: str | None = None
    if body_text:
        chunks.append(body_text)
        content_chars = materialized = len(body_text)
    for index, attachment in enumerate(embedded):
        if index >= MAX_EMBEDDED_FILES:
            chunks.append(f"[+{len(embedded) - MAX_EMBEDDED_FILES} more embedded files skipped]")
            break
        section, section_content_chars = _embedded_section(attachment)
        materialized += len(section) + (2 if chunks else 0)  # + the '\n\n' join seam
        chunks.append(section)
        content_chars += section_content_chars
        if materialized > MAX_EXTRACTED_CHARS:
            bail_reason = f"capped from {materialized} chars"
            break

    if content_chars == 0:
        return _tnef_result(None, STATUS_EMPTY, detail)
    text = sanitize_body_text("\n\n".join(chunks))
    if not text:  # belt-and-braces; content_chars > 0 implies sanitized content survived
        return _tnef_result(None, STATUS_EMPTY, detail)
    if bail_reason is None and len(text) <= MAX_EXTRACTED_CHARS:
        return _tnef_result(text, STATUS_EXTRACTED, detail)
    return _tnef_result(
        text[:MAX_EXTRACTED_CHARS],
        STATUS_TRUNCATED,
        bail_reason or f"capped from {len(text)} chars",
    )


def _tnef_result(text: str | None, status: str, detail: str) -> ExtractionResult:
    """An ExtractionResult under this module's fixed tnefparse provenance."""
    return ExtractionResult(
        text,
        status,
        detail=detail,
        extractor_name=_TNEFPARSE,
        extractor_version=_package_version(_TNEFPARSE),
    )


def _container_body_text(container: TNEF) -> tuple[str, str, str | None]:
    """The container's best body as sanitized text + its kind ('rtf'|'html'|'plain'|'none') +
    an optional fixed-phrase note for `detail` ('rtf-body-over-bound').

    Preference order per design §2.9 (RTF is what Outlook actually writes; html/plain are the
    rarer carriers). Each layer degrades independently: a corrupt candidate falls through to
    the next, and a body-less container returns ('', 'none', note) without failing. The rtf
    layer is GATED on the COMPRESSED property size before any decompression (the LZFu-bomb
    bound — see _compressed_rtf_over_bound): an over-bound rtf body is skipped and noted, the
    html/plain candidates and the caller's embedded-file pass still run.
    """
    note: str | None = None
    if _compressed_rtf_over_bound(container):
        note = "rtf-body-over-bound"
    else:
        rtf_text = _rtf_body_text(container)
        if rtf_text:
            return rtf_text, "rtf", None
    body_layers: tuple[tuple[str, Callable[[TNEF], str]], ...] = (
        ("html", _html_body_text),
        ("plain", _plain_body_text),
    )
    for kind, read_body in body_layers:
        text = read_body(container)
        if text:
            return text, kind, note
    return "", "none", note


def _compressed_rtf_over_bound(container: TNEF) -> bool:
    """True when the COMPRESSED RTF property exceeds MAX_COMPRESSED_RTF_BYTES.

    `container._rtfbody` is tnefparse 1.4.0's PRE-DECOMPRESS handle: the raw MAPI_RTF_COMPRESSED
    property bytes stored at parse time, while `rtfbody` is a LAZY property that
    LZFu-decompresses on access — so the bound MUST be checked here, before _rtf_body_text ever
    touches that property (measured 8.0x expansion on the corpus: an unbounded 50MB container
    could materialize ~400MB + striprtf's second copy). The SAME bound gates the dedup key's
    flattened-body component (KEY-CONSISTENCY — dedup_key.py owns the constant). Guarded: an
    odd container could carry a non-sized value; the rtf layer then degrades on its own.
    """
    try:
        compressed_rtf_property = container._rtfbody  # the pre-decompress handle, see docstring
        return compressed_rtf_property is not None and (
            len(compressed_rtf_property) > MAX_COMPRESSED_RTF_BYTES
        )
    except Exception:
        return False


def _rtf_body_text(container: TNEF) -> str:
    """The compressed-RTF body flattened to text, or '' when absent/corrupt.

    container.rtfbody LZFu-decompresses via compressed-rtf internally — the caller has already
    passed the MAX_COMPRESSED_RTF_BYTES gate before this property is touched; striprtf then
    strips the markup (errors='replace' — a broken \\'xx escape must not lose the body). RTF
    source is ASCII-shaped by spec, so the bytes are read latin-1 (lossless) and striprtf
    decodes the \\ansicpg hex escapes itself.
    """
    try:
        rtf_bytes = container.rtfbody
        if not rtf_bytes:
            return ""
        rtf_source = rtf_bytes.decode("latin-1") if isinstance(rtf_bytes, bytes) else str(rtf_bytes)
        return sanitize_body_text(rtf_to_text(rtf_source, errors="replace"))
    except Exception:  # decompress/strip raise diverse internals — degrade to the next layer
        return ""


def _html_body_text(container: TNEF) -> str:
    """The HTML body flattened to text, or '' when absent/over-bound/corrupt (tnefparse hands
    back str when the container declared an internet codepage, raw bytes otherwise —
    chain-decoded). The SOURCE is bounded at MAX_BODY_SOURCE_BYTES before flattening: html2text
    over a pathological body is unbounded CPU plus a second full copy."""
    try:
        html = container.htmlbody
        if not html or len(html) > MAX_BODY_SOURCE_BYTES:
            return ""
        html_text = html if isinstance(html, str) else decode_charset_chain(html)
        return sanitize_body_text(html_to_text(html_text))
    except Exception:
        return ""


def _plain_body_text(container: TNEF) -> str:
    """The plain-text body, or '' when absent/over-bound/corrupt (str or chain-decoded bytes;
    the SOURCE is bounded at MAX_BODY_SOURCE_BYTES, same as the html layer)."""
    try:
        body = container.body
        if not body or len(body) > MAX_BODY_SOURCE_BYTES:
            return ""
        return sanitize_body_text(body if isinstance(body, str) else decode_charset_chain(body))
    except Exception:
        return ""


def _embedded_section(attachment: TNEFAttachment) -> tuple[str, int]:
    """One embedded file as a rendered section + its CONTENT char count (markers count 0).

    A failure here never fails the container: the section then carries the exception class
    name as its status marker.
    """
    name = _safe_embedded_name(attachment)
    try:
        data = attachment.data or b""
        if not data:
            return f"[embedded: {name} — {STATUS_EMPTY}]", 0
        inner_text, marker_status = _dispatch_embedded(data)
    except Exception as embedded_error:  # per-file isolation — class name only
        return f"[embedded: {name} — failed:{type(embedded_error).__name__}]", 0
    if inner_text:
        return f"[embedded: {name}]\n{inner_text}", len(inner_text)
    return f"[embedded: {name} — {marker_status}]", 0


def _dispatch_embedded(data: bytes) -> tuple[str | None, str]:
    """(extracted text, marker status) for ONE embedded payload, dispatched by SNIFFED kind.

    The marker status is rendered into the section marker only — never stored in
    extraction_status (the container row keeps its own single status). Depth-1 only:
    tnef/ole/unknown payloads are named skips, never recursed. Sniffed-text payloads route
    through _embedded_text_payload (printable guard + markup flatten).
    """
    kind = detect_payload_kind(data)
    if kind == KIND_PDF:
        inner = extract_pdf_text(data)
        return inner.text, inner.status
    if kind == KIND_ZIP:
        inner = extract_docx_text(data)
        if inner.status == STATUS_CORRUPT:
            # A generic zip archive is not CORRUPT — it is a format this depth-1 dispatch does
            # not support (archives stay un-recursed at MVP, design §2.13): honest marker, not
            # corrupt noise.
            return None, STATUS_UNSUPPORTED_FORMAT
        return inner.text, inner.status
    if kind == KIND_TEXT:
        return _embedded_text_payload(data)
    return None, f"skipped {kind}"  # tnef / ole / unknown — depth-1 only, named skip


def _embedded_text_payload(data: bytes) -> tuple[str | None, str]:
    """One sniffed-text embedded payload: charset chain → FULL-TEXT printable guard → markup
    flatten → sanitize; returns (extracted text, marker status) like _dispatch_embedded.

    The printable guard runs over the WHOLE decoded text, BEFORE sanitization (sanitize's C0
    strip would erase exactly the unprintable evidence) and before any flatten:
    detect_payload_kind probes only the first TEXT_PROBE_BYTES bytes, so a binary blob behind
    an ASCII preamble sniffs as text — its mojibake soup must skip-mark, never pour into the
    container text. Markup is then FLATTENED, never stored raw (the EQ-4 invariant inside the
    container): a decoded text opening (whitespace/BOM-tolerant, case-insensitive) with
    '<html'/'<!doctype' flattens through the email-body HTML flattener; with '{\\rtf' through
    striprtf (errors='replace').
    """
    decoded = decode_charset_chain(data)
    if _printable_ratio(decoded) < EMBEDDED_TEXT_MIN_PRINTABLE_RATIO:
        return None, "binary content misclassified as text"
    text = sanitize_body_text(_flatten_embedded_markup(decoded))
    return (text or None), (STATUS_EXTRACTED if text else STATUS_EMPTY)


def _printable_ratio(text: str) -> float:
    """Fraction of printable (or whitespace-furniture) characters over the whole text."""
    if not text:
        return 0.0
    printable = sum(1 for ch in text if ch.isprintable() or ch in _TEXT_WHITESPACE)
    return printable / len(text)


def _flatten_embedded_markup(decoded: str) -> str:
    """html/rtf-opening text flattened (markup source is never stored); anything else as-is."""
    head = decoded.lstrip(_LEADING_TEXT_NOISE)[:16].lower()
    if head.startswith(_HTML_TEXT_PREFIXES):
        return html_to_text(decoded)
    if head.startswith(_RTF_TEXT_PREFIX):
        return rtf_to_text(decoded, errors="replace")
    return decoded


def _safe_embedded_name(attachment: TNEFAttachment) -> str:
    """The embedded file's name, SAFE for section markers.

    C0/C1/DEL controls (newlines included — U+0085 NEL is a C1 line break some renderers
    honor), square brackets and unicode line separators are stripped so a crafted filename
    cannot forge '[embedded: …]' markers or break the section layout; the survivor is
    length-capped; nothing left → 'unnamed'. Name access is guarded — tnefparse decodes names
    lazily and odd containers can raise here.
    """
    try:
        raw_name = attachment.long_filename() or attachment.name or ""
    except Exception:
        raw_name = ""
    if isinstance(raw_name, bytes):  # odd containers hand back undecoded bytes
        raw_name = raw_name.decode("utf-8", errors="replace")
    safe = _UNSAFE_NAME_CHARS.sub("", str(raw_name)).strip()
    return safe[:MAX_EMBEDDED_NAME_CHARS] or "unnamed"
