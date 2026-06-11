"""Suite D — Attachments (pure mode). Adversarial QA of the attachment text extractor.

Source under test:
  - app.connectors.imap.parsing.attachment_extractor.extract_text  (text/* + a few text-shaped
    application/* decode inline; binary -> None, bytes dropped).
  - app.connectors.imap.parsing.email_parser.parse_email           (D05: attachment filename path
    sanitization — NUL/length only, never used as a filesystem path today).

The headline binary attacks (zip-bomb, billion-laughs/XXE, PDF/Office macro) are LATENT because binary
extraction is DEFERRED (docs/FIX_BEFORE_PROD.md, CA-CONN-04). This suite proves they are INERT TODAY
with DISCRIMINATING checks — each check would actually FAIL if the dangerous behavior were present
(entity expansion / file read / decompression / filesystem write), not merely assert dispatch==None.

Run (testing/ is NOT volume-mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/attachment_suite.py

Pure mode: no DB, no network, no run-stamped orgs, no cleanup needed (touches nothing persistent).
"""
from __future__ import annotations

import zlib
from email.message import EmailMessage

from app.connectors.imap.parsing.attachment_extractor import extract_text
from app.connectors.imap.parsing.email_parser import parse_email
from app.connectors.imap.parsing.models import ParsedAttachment

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def make_attachment(content_type: str, payload: bytes, filename: str | None = "f") -> ParsedAttachment:
    """A ParsedAttachment carrying transient payload bytes for the extractor."""
    return ParsedAttachment(
        filename=filename,
        content_type=content_type,
        size_bytes=len(payload),
        content_hash="x" * 64,
        is_inline=False,
        content_id=None,
        payload=payload,
    )


# ---------------------------------------------------------------------------------------------------
# TC-IM-D01 — zip-bomb / decompression-bomb. INERT: application/zip is NOT text-like -> None at
# dispatch (bytes never decompressed). Adversarial twist: also feed the SAME bomb MISLABELED as
# text/plain — it then hits _decode_text, which byte-decodes (NO zlib.decompress) -> still inert.
# ---------------------------------------------------------------------------------------------------
def tc_d01_zip_bomb() -> None:
    # A real deflate stream that expands ~1000x — if anyone decompressed it, it would balloon.
    inner = b"\x00" * (1024 * 1024)  # 1 MiB of zeros
    bomb = zlib.compress(inner, level=9)  # tiny compressed blob
    print(f"D01: 1 MiB-of-zeros compressed to {len(bomb)} bytes (expansion ~{len(inner)//len(bomb)}x)")

    # Path 1: honestly-labeled application/zip -> not text-like -> None, bytes never touched.
    out_zip = extract_text(make_attachment("application/zip", bomb))
    check("d01_zip_dispatch_returns_none", out_zip is None, f"extract_text(application/zip)={out_zip!r}")

    # Path 2: bomb MISLABELED text/plain -> goes through _decode_text (raw bytes.decode, NO
    # decompression). Output must be small (~len of the compressed blob, NOT the 1 MiB payload).
    out_mislabel = extract_text(make_attachment("text/plain", bomb))
    decompressed_leaked = out_mislabel is not None and len(out_mislabel) >= len(inner) // 2
    check(
        "d01_mislabeled_text_not_decompressed",
        not decompressed_leaked,
        f"output_len={len(out_mislabel) if out_mislabel else 0} (compressed_in={len(bomb)}, "
        f"would-be-decompressed={len(inner)}) -> no decompression",
    )

    # Defense-in-depth: confirm the module imports no decompression machinery.
    import app.connectors.imap.parsing.attachment_extractor as ax  # noqa: PLC0415

    src = open(ax.__file__, encoding="utf-8").read()
    no_decompress = "zlib" not in src and "gzip" not in src and "zipfile" not in src and "tarfile" not in src
    check("d01_extractor_imports_no_decompressor", no_decompress,
          "attachment_extractor.py references no zlib/gzip/zipfile/tarfile")


# ---------------------------------------------------------------------------------------------------
# TC-IM-D02 — billion-laughs / XXE in an application/xml attachment. THE TEETH OF THIS SUITE.
# application/xml IS in _TEXT_EXACT, so it DOES get decoded — but by a raw bytes.decode, with NO XML
# parser instantiated. Prove: the entity declarations survive VERBATIM (no expansion), output length
# ~= input length (no exponential blow-up), and an XXE file-read target ('root:') never appears.
# ---------------------------------------------------------------------------------------------------
def tc_d02_billion_laughs_xxe() -> None:
    billion_laughs = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE lolz [\n'
        b'  <!ENTITY lol "lol">\n'
        b'  <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        b'  <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">\n'
        b'  <!ENTITY lol9 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
        b']>\n'
        b'<lolz>&lol9;</lolz>'
    )
    out = extract_text(make_attachment("application/xml", billion_laughs))

    # 1. The literal entity reference survives verbatim — proves NO expansion happened.
    entity_verbatim = out is not None and "&lol9;" in out and "<!ENTITY lol1" in out
    check("d02_billion_laughs_entities_verbatim", entity_verbatim,
          f"'&lol9;' present verbatim & not expanded (out_len={len(out) if out else 0})")

    # 2. Output length == input length (no entity expansion at all). A real XML parser would expand
    #    &lol9; (classic billion-laughs pattern; a full lol1..lol9 ladder reaches ~10^9 chars).
    length_sane = out is not None and len(out) <= len(billion_laughs) + 16
    check("d02_no_exponential_blowup", length_sane,
          f"in={len(billion_laughs)} out={len(out) if out else 0} (== input -> zero expansion)")

    # 3. XXE: a SYSTEM external-entity pointing at /etc/passwd. A real XML parser with external
    #    entities would inject the file contents ('root:...'). Raw decode leaves the declaration inert.
    xxe = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
        b'<foo>&xxe;</foo>'
    )
    out_xxe = extract_text(make_attachment("application/xml", xxe))
    no_file_read = out_xxe is not None and "&xxe;" in out_xxe and "root:" not in out_xxe
    check("d02_xxe_no_file_read", no_file_read,
          f"'&xxe;' verbatim, 'root:' absent -> no external-entity file read (out_len={len(out_xxe) if out_xxe else 0})")

    # 4. Defense-in-depth: the extractor imports no XML parser.
    import app.connectors.imap.parsing.attachment_extractor as ax  # noqa: PLC0415

    src = open(ax.__file__, encoding="utf-8").read()
    no_xml_parser = "xml." not in src and "lxml" not in src and "etree" not in src and "expat" not in src
    check("d02_extractor_imports_no_xml_parser", no_xml_parser,
          "attachment_extractor.py references no xml/lxml/etree/expat")


# ---------------------------------------------------------------------------------------------------
# TC-IM-D03 — PDF/Office with a macro/exploit. INERT: application/pdf + the OOXML/msword types are
# NOT text-like -> None (bytes never opened/parsed). Positive control: a benign text/plain DOES
# extract, proving the None is a dispatch decision, not a broken function.
# ---------------------------------------------------------------------------------------------------
def tc_d03_pdf_office_macro() -> None:
    # A fake PDF with an embedded /OpenAction + JavaScript (what a malicious PDF carries).
    malicious_pdf = (
        b"%PDF-1.7\n"
        b"1 0 obj<</Type/Catalog/OpenAction<</S/JavaScript/JS(app.alert('pwned');"
        b"this.exportDataObject({cName:'x',nLaunch:0}))>>>>endobj\n"
        b"%%EOF"
    )
    binary_types = (
        ("application/pdf", malicious_pdf),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK\x03\x04macro-vbaProject.bin"),
        ("application/vnd.ms-excel", b"\xd0\xcf\x11\xe0Auto_Open macro"),
        ("application/msword", b"\xd0\xcf\x11\xe0AutoOpen macro"),
        ("application/ms-tnef", b"\x78\x9f\x3e\x22winmail.dat"),  # the 261-count Outlook format
    )
    all_none = True
    detail_parts: list[str] = []
    for ctype, payload in binary_types:
        out = extract_text(make_attachment(ctype, payload))
        all_none = all_none and out is None
        detail_parts.append(f"{ctype.split('/')[-1][:12]}={out!r}")
    check("d03_binary_office_pdf_all_none", all_none, "; ".join(detail_parts))

    # Positive control: benign text DOES extract -> the None above is a real dispatch decision.
    control = extract_text(make_attachment("text/plain", b"benign control text"))
    check("d03_positive_control_text_extracts", control == "benign control text",
          f"control={control!r}")

    # Defense-in-depth: extractor imports no PDF/office library.
    import app.connectors.imap.parsing.attachment_extractor as ax  # noqa: PLC0415

    src = open(ax.__file__, encoding="utf-8").read()
    no_doc_lib = all(lib not in src for lib in ("pypdf", "PyPDF", "fitz", "docx", "openpyxl", "pptx", "olefile", "tnef"))
    check("d03_extractor_imports_no_doc_lib", no_doc_lib,
          "attachment_extractor.py references no PDF/docx/xlsx/pptx/ole/tnef parser")


# ---------------------------------------------------------------------------------------------------
# TC-IM-D04 — encoding-lie: a binary blob mislabeled text/plain. Content-Type is TRUSTED (no content
# sniffing) -> _decode_text byte-decodes utf-8/replace -> replacement-char soup is RETURNED (the
# runner would store it in extracted_text). No crash (contract holds). Data-quality only.
# ---------------------------------------------------------------------------------------------------
def tc_d04_encoding_lie() -> None:
    # High-entropy non-utf8 bytes (a PNG header + random binary) labeled text/plain.
    binary_blob = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    out = extract_text(make_attachment("text/plain", binary_blob))

    # 1. No crash, returns a str (the "never raises" contract holds — this is the PASS dimension).
    returned_str = out is not None and isinstance(out, str)
    check("d04_mislabeled_binary_no_crash", returned_str,
          f"returned str of len {len(out) if out else 0} (no exception)")

    # 2. Observation: the output is replacement-char soup (U+FFFD), i.e. corrupt text the runner
    #    WOULD store because the type was trusted and not sniffed. NEW Info data-quality finding.
    soup = out is not None and "�" in out
    check("d04_output_is_replacement_soup", soup,
          f"U+FFFD replacement chars present: {out.count(chr(0xFFFD)) if out else 0} "
          "(no content sniffing -> trusted Content-Type)")


# ---------------------------------------------------------------------------------------------------
# TC-IM-D05 — filename path-traversal '../../etc/passwd'. The filename never reaches extract_text;
# drive it through parse_email. sanitize() applies NUL-strip + length-cap ONLY (no '../' stripping),
# so the traversal string is retained VERBATIM on ParsedAttachment.filename. LATENT: it is stored as
# a plain column string and NEVER used as a filesystem path (grep of app/connectors == 0 fs ops).
# ---------------------------------------------------------------------------------------------------
def tc_d05_filename_path_traversal() -> None:
    traversal = "../../../../etc/passwd"
    msg = EmailMessage()
    msg["From"] = "attacker@evil.test"
    msg["To"] = "victim@corp.test"
    msg["Subject"] = "d05"
    msg.set_content("body")
    msg.add_attachment(
        b"payload-bytes",
        maintype="application",
        subtype="octet-stream",
        filename=traversal,
    )
    parsed = parse_email(msg.as_bytes(), mailbox_address="victim@corp.test")

    att = parsed.attachments[0] if parsed.attachments else None
    # 1. The traversal sequence survives verbatim — sanitize() does NOT strip '../'.
    verbatim = att is not None and att.filename == traversal
    check("d05_traversal_retained_verbatim", verbatim,
          f"stored filename={att.filename!r} (sanitize = NUL+length only, no '../' stripping)")

    # 2. A NUL-laced + over-length filename: NUL stripped, capped to MSGID_MAX (998), no crash.
    from app.connectors.imap.parsing.headers import MSGID_MAX  # noqa: PLC0415

    nasty = "../" * 500 + "etc/pa\x00sswd" + "A" * 2000
    msg2 = EmailMessage()
    msg2["From"] = "a@b.test"; msg2["To"] = "c@d.test"; msg2["Subject"] = "d05b"
    msg2.set_content("body")
    msg2.add_attachment(b"x", maintype="application", subtype="octet-stream", filename=nasty)
    parsed2 = parse_email(msg2.as_bytes(), mailbox_address="c@d.test")
    att2 = parsed2.attachments[0] if parsed2.attachments else None
    nul_capped = (
        att2 is not None
        and att2.filename is not None
        and "\x00" not in att2.filename
        and len(att2.filename) <= MSGID_MAX
    )
    check("d05_nul_stripped_and_length_capped", nul_capped,
          f"NUL absent & len={len(att2.filename) if att2 and att2.filename else 'n/a'} <= {MSGID_MAX}")


def main() -> None:
    print("=== Suite D — Attachments (pure) ===\n")
    for label, fn in (
        ("TC-IM-D01 zip-bomb", tc_d01_zip_bomb),
        ("TC-IM-D02 billion-laughs/XXE", tc_d02_billion_laughs_xxe),
        ("TC-IM-D03 PDF/Office macro", tc_d03_pdf_office_macro),
        ("TC-IM-D04 encoding-lie", tc_d04_encoding_lie),
        ("TC-IM-D05 filename traversal", tc_d05_filename_path_traversal),
    ):
        print(f"--- {label} ---")
        fn()
        print()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} checks passed")
    print("VERDICT:", "ALL INERT — binary attacks dropped at the text-only seam (CA-CONN-04 gating risk)"
          if passed == total else "UNEXPECTED — a safety check FAILED, investigate")


main()
