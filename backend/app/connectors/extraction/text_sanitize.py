"""
Role: The ONE source of the stored-text sanitization, charset-decode and HTML-flatten rules — the
      three pure leaf functions every text-bearing extraction path shares. sanitize_body_text
      (storable-as-UTF-8 guarantee: LF normalization + C0/lone-surrogate strip),
      decode_charset_chain (the STRICT charset-fallback chain — audits M-5/EQ-3) and html_to_text
      (the single HTML flattener — audit EQ-4). Connector-agnostic: pure functions over str/bytes,
      no email/IMAP/DB types.
Used by: app.connectors.extraction.pdf / .docx / .tnef (extracted-text sanitization, charset
         chain, HTML flatten); the IMAP connector's email_parser (email-body decode/flatten/
         sanitize — re-imports these and keeps its email-specific part decoders) and its
         attachment_extractor (text/html/rtf attachment paths). The arrow points IN — those
         connector modules import from here; this leaf imports nothing back.
Depends on: stdlib (re), html2text. NOTHING from any specific connector.
Key invariants:
  - sanitize_body_text GUARANTEES the result is STORABLE AS UTF-8: canonical LF line endings, C0
    controls stripped EXCEPT tab/LF (audit L-6), lone surrogates U+D800–U+DFFF stripped (broken PDF
    ToUnicode CMaps would otherwise crash the asyncpg flush — poison message). The SINGLE source of
    these rules; email bodies, text attachments AND extracted PDF/docx/TNEF text all pass through
    here, never a re-implementation.
  - decode_charset_chain is the SINGLE strict-first charset chain (declared → COHERENCE-ARBITRATED
    detection over utf-8/cp1252/cp1251 → cp1252 → windows-1251 → declared-replace → utf-8-replace):
    senders mislabel constantly and a strict-first chain recovers those losslessly where a
    replace-first decode stores U+FFFD. Detection (charset-normalizer, 2026-07-03 audit M2) exists
    because Cyrillic cp1251 bytes are ALL valid cp1252 — a fixed cp1252-first order can never
    recover undeclared Bulgarian text (it "succeeds" as mojibake: 'Äîñòàâ÷èê' for 'Доставчик').
  - html_to_text is the SINGLE HTML flattener (no wrapping — wrapping corrupts downstream chunking;
    images dropped; link/anchor text kept).
"""

from __future__ import annotations

import re

import html2text
from charset_normalizer import from_bytes

# Strict-decode fallbacks for mislabeled charsets (audit M-5): the corpus's Outlook bodies declare
# gb2312 but carry cp1252 bytes; Cyrillic mail often hides behind a wrong label as windows-1251.
_CHARSET_FALLBACKS: tuple[str, ...] = ("cp1252", "windows-1251")

# The candidate set detection may choose from (2026-07-03 audit M2) — the SAME family the static
# chain uses, plus utf-8, so detection can only ever pick a charset we would have trusted anyway;
# it replaces the fixed cp1252-first ORDER with per-payload coherence arbitration. Deliberately
# narrow: widening it (koi8, iso-8859-x, cp1250…) trades determinism for recall — extend only with
# a corpus-measured case.
_DETECTION_CANDIDATES: tuple[str, ...] = ("utf_8", "cp1252", "cp1251")

# Detection needs SIGNAL: below this many non-ASCII bytes, language-coherence scoring is a coin
# toss (a lone cp1252 '€' vs a lone cp1251 'Ђ' — nothing to cohere), so the corpus-validated
# static order decides instead. The M2 mojibake class (undeclared Cyrillic text) carries hundreds
# of non-ASCII bytes and clears this floor by orders of magnitude.
_DETECTION_MIN_NON_ASCII = 16
_ASCII_BYTES = bytes(range(0x80))

# Code points that must never reach a stored text column:
#   - C0 control chars EXCEPT tab (\x09), LF (\x0A), CR (\x0D) — audit L-6: BEL/VT-class garbage
#     pollutes chunking/embedding; NUL would crash Postgres. CR is exempted here only because the
#     LF-normalization pass in sanitize_body_text has already removed it.
#   - LONE SURROGATES (U+D800–U+DFFF) — pdfminer/pypdf emit them from broken PDF ToUnicode CMaps;
#     they survive in a Python str but asyncpg raises UnicodeEncodeError at flush, turning the
#     whole email into a poison message (2026-06-11 review).
_UNSTORABLE_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")


def sanitize_body_text(text: str) -> str:
    """Apply the canonical stored-text sanitization to a decoded text block.

    The guarantee is: the result is STORABLE AS UTF-8, period. Canonical LF line endings (wire
    CRLF must not leak), THEN strip the remaining C0 controls except tab/LF (audit L-6: NUL would
    crash Postgres; BEL/VT-class garbage pollutes chunking) AND lone surrogates U+D800–U+DFFF
    (pdfminer/pypdf emit them from broken ToUnicode CMaps; asyncpg raises UnicodeEncodeError at
    flush — a poison message), then trim surrounding whitespace. The SINGLE source of these rules:
    email bodies, text attachments (attachment_extractor) AND extracted PDF text (extractors.pdf)
    all pass through here, never a re-implementation.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _UNSTORABLE_CHARS.sub("", text).strip()


def decode_charset_chain(payload: bytes, declared_charset: str | None = None) -> str:
    """Decode bytes to str via the STRICT charset-fallback chain (audits M-5 + EQ-3 + M2).

    Order: declared charset strict (utf-8 when none is declared) → coherence-arbitrated DETECTION
    over utf-8/cp1252/cp1251 → cp1252 strict → windows-1251 strict → declared charset with
    errors='replace' (U+FFFD only when every prior step fails; utf-8-replace if even the declared
    charset is unknown). Senders mislabel constantly — the corpus's Outlook gb2312-declared bodies
    are really cp1252, and Cyrillic mail often hides behind a wrong label — and a strict-first
    chain recovers those losslessly where a replace-first decode stores replacement chars.

    The detection step (2026-07-03 audit M2) exists because a fixed cp1252-first ORDER cannot
    recover undeclared cp1251 text: every Cyrillic cp1251 byte (0xC0–0xFF) is a valid cp1252 code
    point, so the cp1252 link "succeeds" as mojibake and windows-1251 is never reached. Detection
    arbitrates the SAME candidate family by language coherence instead of position, and runs only
    after the declared-strict decode fails (the common path never pays for it). The SINGLE chain
    shared by email bodies, text-shaped attachments (attachment_extractor) and TNEF interiors
    (extractors.tnef).
    """
    charset = declared_charset or "utf-8"
    try:
        return payload.decode(charset)
    except (LookupError, UnicodeDecodeError):
        pass
    detected = _decode_by_coherence(payload)
    if detected is not None:
        return detected
    for candidate in _CHARSET_FALLBACKS:
        try:
            return payload.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decode_by_coherence(payload: bytes) -> str | None:
    """Best-cohering decode among _DETECTION_CANDIDATES, or None when detection has no basis.

    charset-normalizer scores each candidate by decode validity + language coherence (character
    frequency), which is exactly the arbitration a fixed fallback ORDER cannot provide when two
    candidates both decode without error (the cp1252/cp1251 ambiguity — audit M2). Returns None —
    falling through to the static chain unchanged — when the payload carries too little non-ASCII
    signal to cohere on (the threshold guard) or no candidate survives (binary garbage).
    """
    if len(payload.translate(None, _ASCII_BYTES)) < _DETECTION_MIN_NON_ASCII:
        return None
    match = from_bytes(payload, cp_isolation=list(_DETECTION_CANDIDATES)).best()
    return None if match is None else str(match)


def redecode_single_byte_text(text: str) -> str:
    """Repair a str that some upstream decoder produced under a wrong single-byte codepage.

    Two producers hand us pre-decoded text we cannot intercept at the byte layer: tnefparse
    (decodes TNEF bodies with the container's declared codepage) and striprtf (decodes RTF
    `\\'xx` hex escapes with the RTF's declared `\\ansicpg`). A LYING declaration — cp1251
    content declared cp1252, the 2026-07-04 corpus case `Zapoved_zaKomandirovka.rtf` — yields
    mojibake ('Ïðè ïúòóâàíå' for 'При пътуване') from a byte stream that is itself pure ASCII,
    invisible to decode_charset_chain. Single-byte decodes are losslessly reversible: re-encode
    (cp1252 first, latin-1 for its 0x80–0x9F holes) and re-run the coherence-arbitrated chain.
    Correctly-decoded text round-trips to ITSELF, so this is safe to apply unconditionally at
    every markup-flatten seam.

    MIXED-PLANE fallback: Word RTF doubles characters as `\\uN` escapes (decoded to REAL wide
    Cyrillic by striprtf) next to `\\'xx` runs (mojibaked under the lie) — one genuine wide char
    would fail the whole-string re-encode and strand the rest. When the whole string cannot
    round-trip, each LINE is repaired independently (scripts practically never mix planes within
    one line): mojibake-only lines re-decode, genuine-Unicode lines are untouched.
    """
    repaired = _redecode_if_single_byte(text)
    if repaired is not None:
        return repaired
    lines = text.split("\n")
    repaired_lines = [
        line if (fixed := _redecode_if_single_byte(line)) is None else fixed for line in lines
    ]
    return "\n".join(repaired_lines)


def _redecode_if_single_byte(text: str) -> str | None:
    """Re-decode `text` through the chain iff it round-trips to single-byte bytes, else None."""
    try:
        raw = text.encode("cp1252")
    except UnicodeEncodeError:
        try:
            raw = text.encode("latin-1")
        except UnicodeEncodeError:
            return None  # contains genuine wide Unicode — not a single-byte decode
    return decode_charset_chain(raw)


def html_to_text(html: str) -> str:
    """Flatten HTML to readable text (no wrapping, drop images, keep link/anchor text).

    The SINGLE HTML flattener: email bodies, text/html attachments (attachment_extractor —
    audit EQ-4 found 44 rows storing raw markup source as 'extracted') and TNEF html bodies
    (extractors.tnef) all flatten through here, never a re-implementation.
    """
    converter = html2text.HTML2Text()
    converter.body_width = 0  # never hard-wrap — wrapping corrupts downstream chunking
    converter.ignore_images = True
    converter.unicode_snob = True
    return converter.handle(html)
