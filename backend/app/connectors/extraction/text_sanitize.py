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
  - decode_charset_chain is the SINGLE strict-first charset chain (declared → cp1252 →
    windows-1251 → declared-replace → utf-8-replace): senders mislabel constantly and a
    strict-first chain recovers those losslessly where a replace-first decode stores U+FFFD.
  - html_to_text is the SINGLE HTML flattener (no wrapping — wrapping corrupts downstream chunking;
    images dropped; link/anchor text kept).
"""

from __future__ import annotations

import re

import html2text

# Strict-decode fallbacks for mislabeled charsets (audit M-5): the corpus's Outlook bodies declare
# gb2312 but carry cp1252 bytes; Cyrillic mail often hides behind a wrong label as windows-1251.
_CHARSET_FALLBACKS: tuple[str, ...] = ("cp1252", "windows-1251")

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
    """Decode bytes to str via the STRICT charset-fallback chain (audits M-5 + EQ-3).

    Order: declared charset strict (utf-8 when none is declared) → cp1252 strict →
    windows-1251 strict → declared charset with errors='replace' (U+FFFD only when every strict
    candidate fails; utf-8-replace if even the declared charset is unknown). Senders mislabel
    constantly — the corpus's Outlook gb2312-declared bodies are really cp1252, and Cyrillic mail
    often hides behind a wrong label as windows-1251 — and a strict-first chain recovers those
    losslessly where a replace-first decode stores replacement chars. The SINGLE chain shared by
    email bodies, text-shaped attachments (attachment_extractor — EQ-3 measured 9 rows / 564K
    mojibake chars under the old utf-8-only decode) and TNEF interiors (extractors.tnef).
    """
    charset = declared_charset or "utf-8"
    for candidate in (charset, *_CHARSET_FALLBACKS):
        try:
            return payload.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


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
