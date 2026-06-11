"""SUITE C (pure leg) — Parse & data-quality adversarial checks on the IMAP parser.

Covers the PURE cases of Suite C — parse_email / extract_text are pure functions, so this script
imports them directly and asserts on the returned ParsedEmail. No DB, no network. Each check prints
[PASS]/[FAIL] where PASS means "the parser DEFENCE held" (E01 polarity) — i.e. a reproduced defect
is reported as [FAIL]/[CONCERN] in the per-case verdict, NOT as a green PASS.

Cases here (pure legs):
  C01  deep-nested (~300) multipart -> RecursionError from the bare parser (the runner-fail chain is
       proven separately in suite_c_ingest_runner.py).
  C03  From == mailbox -> direction='outbound' (no SPF/DKIM) — inbound spoof recorded as SENT.
  C04  C0 control chars except NUL survive into subject/body/headers (only NUL stripped).
  C05  no size cap anywhere — a ~10MB body + ~8MB attachment parse + sha256 without any limit.
  C06  uncapped JSONB header (1MB X- header, CONFIRMS-DOCUMENTED CA-CONN-05) + uncapped references
       (10k ids, NEW) survive verbatim.
  C07  charset lie / unknown charset / pathological RFC2047 -> decode-with-replacement, never raises.
  C08  forged future Date / fully-absent date -> received_at attacker-controlled or NULL.
  C10  NUL in subject/body -> stripped; NUL in Message-ID -> forces sha256 content-hash dedup_key.

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/suite_c_parse_quality.py

Non-destructive: pure functions only, no rows created.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from app.connectors.imap.parsing.email_parser import parse_email
from app.connectors.imap.parsing.models import ParsedAttachment
from app.connectors.imap.parsing.attachment_extractor import extract_text

MAILBOX = "owner@acme.com"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def eml(headers: str, body: str = "Body.") -> bytes:
    """Assemble RFC822 bytes from a header block + body (CRLF, blank separator)."""
    return (headers.strip() + "\r\n\r\n" + body).replace("\n", "\r\n").encode("utf-8")


# ---------------------------------------------------------------- C01 (bare parse leg)
def c01_recursion() -> None:
    depth = 300
    inner = "Content-Type: text/plain\r\n\r\nhello\r\n"
    body = inner
    for i in range(depth):
        b = f"B{i}"
        body = f'Content-Type: multipart/mixed; boundary="{b}"\r\n\r\n--{b}\r\n{body}\r\n--{b}--\r\n'
    raw = (f"From: a@x.com\r\nTo: {MAILBOX}\r\nMessage-ID: <deep@x>\r\n" + body).encode()
    raised: str | None = None
    try:
        parse_email(raw, MAILBOX)
    except RecursionError:
        raised = "RecursionError"
    except Exception as exc:  # noqa: BLE001 — we want to know WHAT escapes the never-raises contract
        raised = f"{type(exc).__name__}: {str(exc)[:60]}"
    # DEFENCE held would mean parse_email returned a best-effort ParsedEmail (raised is None).
    # We EXPECT it to raise RecursionError -> defect reproduced -> this check is a FAIL (defence broke).
    check(
        "C01_deep_multipart_raises_recursionerror",
        raised is None,
        f"raised={raised} (RecursionError EXPECTED — never-raises contract broken; runner-fail proven in ingest script)",
    )


# ---------------------------------------------------------------- C03 direction spoof
def c03_direction_spoof() -> None:
    # An attacker forges From == the owner's own mailbox. No SPF/DKIM check -> classified outbound.
    raw = eml(f"From: {MAILBOX}\nTo: victim@acme.com\nSubject: pay this invoice\nMessage-ID: <spoof@x>")
    parsed = parse_email(raw, MAILBOX)
    # DEFENCE would be direction != 'outbound' (or None). It IS 'outbound' -> spoof succeeds -> FAIL.
    check(
        "C03_forged_from_classified_outbound",
        parsed.direction != "outbound",
        f"direction={parsed.direction!r} (EXPECTED 'outbound' — inbound spoof recorded as SENT-BY-OWNER)",
    )


# ---------------------------------------------------------------- C04 control-char survival
def c04_control_chars() -> None:
    # \x01 (SOH), \x07 (BEL), \x1b (ESC -> ANSI escape). Only NUL is stripped by the parser.
    payload = "alert\x07 esc\x1b[31mRED\x1b[0m soh\x01end"
    raw = eml(
        f"From: a@x.com\nTo: {MAILBOX}\nSubject: hdr\x1b[31minj\x01ect\nMessage-ID: <ctl@x>\n"
        "Content-Type: text/plain; charset=utf-8",
        payload,
    )
    parsed = parse_email(raw, MAILBOX)
    body_has_ctl = "\x1b" in parsed.body_text and "\x07" in parsed.body_text and "\x01" in parsed.body_text
    subj_has_ctl = parsed.subject is not None and "\x1b" in parsed.subject
    # DEFENCE would strip/escape C0 controls. They SURVIVE -> log/terminal injection vector -> FAIL.
    check(
        "C04_c0_controls_survive_body",
        not body_has_ctl,
        f"body controls present={body_has_ctl} body={parsed.body_text!r}",
    )
    check(
        "C04_c0_controls_survive_subject_and_headers",
        not subj_has_ctl,
        f"subject={parsed.subject!r}",
    )


# ---------------------------------------------------------------- C05 no size cap
def c05_no_size_cap() -> None:
    big_body = "A" * (10 * 1024 * 1024)  # 10 MB plain-text body
    big_attach = b"B" * (8 * 1024 * 1024)  # 8 MB text/csv attachment
    raw = (
        b"From: a@x.com\r\nTo: " + MAILBOX.encode() + b"\r\nSubject: big\r\nMessage-ID: <big@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n" + big_body.encode() + b"\r\n"
        b'--B\r\nContent-Type: text/csv\r\nContent-Disposition: attachment; filename="huge.csv"\r\n\r\n'
        + big_attach + b"\r\n--B--\r\n"
    )
    parsed = parse_email(raw, MAILBOX)
    att = parsed.attachments[0]
    materialized = len(parsed.body_text) >= 10 * 1024 * 1024 and att.size_bytes >= 8 * 1024 * 1024
    hashed_ok = att.content_hash == sha256(big_attach).hexdigest()
    # DEFENCE would be a cap (truncation / rejection). It is fully materialized + hashed -> FAIL.
    check(
        "C05_oversized_body_and_attachment_fully_materialized",
        not (materialized and hashed_ok),
        f"body_len={len(parsed.body_text)} att_size={att.size_bytes} hash_match={hashed_ok} (no cap anywhere)",
    )


# ---------------------------------------------------------------- C06 uncapped jsonb + references
def c06_uncapped_headers_and_references() -> None:
    big_header_value = "X" * (1024 * 1024)  # 1 MB X- header value
    refs = " ".join(f"<r{i}@x>" for i in range(10000))  # 10k references
    raw = eml(
        f"From: a@x.com\nTo: {MAILBOX}\nMessage-ID: <bloat@x>\n"
        f"X-Bloat: {big_header_value}\nReferences: {refs}",
        "b",
    )
    parsed = parse_email(raw, MAILBOX)
    header_kept = len(str(parsed.headers.get("X-Bloat", ""))) >= 1024 * 1024
    refs_kept = len(parsed.references) >= 10000
    # DEFENCE would cap either. Verbatim header = CA-CONN-05 (documented); refs uncapped = NEW. FAIL.
    check(
        "C06_megabyte_header_stored_verbatim",
        not header_kept,
        f"X-Bloat len={len(str(parsed.headers.get('X-Bloat', '')))} (CA-CONN-05 verbatim retention)",
    )
    check(
        "C06_references_array_uncapped",
        not refs_kept,
        f"references count={len(parsed.references)} (NEW — unbounded ARRAY, no cap)",
    )


# ---------------------------------------------------------------- C07 charset / rfc2047 robustness
def c07_charset_robustness() -> None:
    raised: list[str] = []

    # (a) declared charset that does not exist
    try:
        p1 = parse_email(
            b"From: a@x.com\r\nTo: o@x\r\nSubject: s\r\nMessage-ID: <cs1@x>\r\n"
            b"Content-Type: text/plain; charset=not-a-real-charset\r\n\r\nhello body",
            MAILBOX,
        )
        a_ok = "hello" in p1.body_text
    except Exception as exc:  # noqa: BLE001
        a_ok = False
        raised.append(f"unknown-charset:{type(exc).__name__}")

    # (b) charset LIE: declares utf-8 but body is latin-1 / invalid utf-8 bytes
    try:
        p2 = parse_email(
            b"From: a@x.com\r\nTo: o@x\r\nSubject: s\r\nMessage-ID: <cs2@x>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nvalid \xff\xfe\x80 bytes",
            MAILBOX,
        )
        b_ok = "valid" in p2.body_text
    except Exception as exc:  # noqa: BLE001
        b_ok = False
        raised.append(f"charset-lie:{type(exc).__name__}")

    # (c) pathological RFC2047 encoded-words: bad base64, unknown charset, truncated token
    try:
        p3 = parse_email(
            b"From: a@x.com\r\nTo: o@x\r\nMessage-ID: <cs3@x>\r\n"
            b"Subject: =?utf-8?b?not_valid_base64!!?= =?bogus-cs?q?x?= =?utf-8?b?dHJ1bmM\r\n\r\nb",
            MAILBOX,
        )
        c_ok = p3.subject is not None
    except Exception as exc:  # noqa: BLE001
        c_ok = False
        raised.append(f"rfc2047-bomb:{type(exc).__name__}")

    # DEFENCE held = no exception across all three -> PASS (this is a positive/contract case).
    check(
        "C07_charset_and_rfc2047_decode_with_replacement_never_raises",
        a_ok and b_ok and c_ok and not raised,
        f"unknown_ok={a_ok} lie_ok={b_ok} rfc2047_ok={c_ok} raised={raised}",
    )


# ---------------------------------------------------------------- C08 forged / missing date
def c08_date_provenance() -> None:
    # (a) Forged FUTURE Date, no Received header, no internal_date -> received_at = attacker's Date.
    forged = parse_email(
        eml(f"From: a@x.com\nTo: {MAILBOX}\nDate: Fri, 31 Dec 2099 23:59:59 +0000\nMessage-ID: <fut@x>"),
        MAILBOX,
        internal_date=None,
    )
    attacker_controlled = (
        forged.received_at is not None and forged.received_at.year == 2099
    )
    # (b) No Date, no Received, no internal_date -> received_at is NULL (sorts NULLS LAST).
    nodate = parse_email(eml(f"From: a@x.com\nTo: {MAILBOX}\nMessage-ID: <nod@x>"), MAILBOX, internal_date=None)
    is_null = nodate.received_at is None
    # DEFENCE would clamp/ignore an implausible future Date. It does NOT -> FAIL (mis-orders list).
    check(
        "C08_forged_future_date_becomes_received_at",
        not attacker_controlled,
        f"received_at={forged.received_at} (attacker-controlled; mis-orders list_for_org received_at DESC NULLS LAST)",
    )
    check(
        "C08_absent_date_yields_null_received_at",
        is_null,
        f"received_at={nodate.received_at} (NULL — sinks to bottom under NULLS LAST)",
    )


# ---------------------------------------------------------------- C10 NUL handling (parse leg)
def c10_nul_handling() -> None:
    # NUL in subject + body -> stripped; NUL in Message-ID -> sha256 content-hash dedup fallback.
    raw = (
        b"From: a@x.com\r\nTo: " + MAILBOX.encode() + b"\r\nMessage-ID: <na\x00sty@x>\r\n"
        b"Subject: su\x00bj\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nbo\x00dy"
    )
    parsed = parse_email(raw, MAILBOX)
    no_nul_subject = parsed.subject is not None and "\x00" not in parsed.subject
    no_nul_body = "\x00" not in parsed.body_text
    hash_fallback = parsed.dedup_key.startswith("sha256:")
    # DEFENCE held = NUL stripped + hash fallback -> PASS (CONFIRMS-FIXED).
    check(
        "C10_nul_stripped_from_subject_and_body",
        no_nul_subject and no_nul_body,
        f"subject={parsed.subject!r} body={parsed.body_text!r}",
    )
    check(
        "C10_nul_message_id_forces_hash_dedup_key",
        hash_fallback,
        f"dedup_key={parsed.dedup_key[:24]}... message_id={parsed.message_id!r}",
    )


def main() -> None:
    print("=== SUITE C (pure) — parse & data quality ===")
    c01_recursion()
    c03_direction_spoof()
    c04_control_chars()
    c05_no_size_cap()
    c06_uncapped_headers_and_references()
    c07_charset_robustness()
    c08_date_provenance()
    c10_nul_handling()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} DEFENCE checks held (a non-held check = a reproduced finding)")


main()
