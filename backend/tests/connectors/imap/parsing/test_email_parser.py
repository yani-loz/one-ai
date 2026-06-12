"""
Role: Unit tests for the RFC822 email parser — body extraction (plain/html/alternative), the
      strict charset-fallback decode chain (audit M-5), address + header decoding, within-message
      recipient dedup (M-6), body C0 sanitization (L-6), addr-spec gating of from_address (L-7),
      attachment filename hygiene (L-8), derived-flags wiring, and the never-raises robustness
      contract. Dedup-key behavior lives in test_email_parser_dedup.py (split for the A2 size cap).
Used by: pytest (tests/connectors/imap/parsing). Pure — no DB, no network.
Depends on: app.connectors.imap.parsing.email_parser. Builds raw .eml bytes inline per test.
Key invariants tested:
  - parse_email NEVER raises — garbage degrades to a best-effort parse; a pathological deep
    multipart degrades to parse_status='failed' (stored, not dropped).
  - Body decode walks declared charset → cp1252 → windows-1251 STRICTLY before errors='replace',
    so mislabeled charsets recover real text instead of storing U+FFFD.
"""

from __future__ import annotations

import pytest

from app.connectors.imap.parsing.email_parser import parse_email, sanitize_body_text

MAILBOX = "me@oneai.com"


def _eml(headers: str, body: str) -> bytes:
    """Assemble raw RFC822 bytes from a header block + body (CRLF line endings, blank separator).

    Uses a bare-LF blank separator before the single `\\n`→`\\r\\n` pass so the header/body boundary
    becomes a CLEAN `\\r\\n\\r\\n` (not the `\\r\\r\\n` a literal CRLF yields under the replace).
    """
    return (headers.strip() + "\n\n" + body).replace("\n", "\r\n").encode("utf-8")


def test_parse_plain_text_extracts_body_and_message_id() -> None:
    raw = _eml(
        "From: Boyan <boyan@acme.com>\nTo: me@oneai.com\n"
        "Subject: Hi\nMessage-ID: <m1@acme.com>\nContent-Type: text/plain; charset=utf-8",
        "Hello there.\nLine two.",
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.message_id == "m1@acme.com"
    assert parsed.dedup_key.startswith("sha256:")  # dedup is content-hashed, never the Message-ID
    assert parsed.body_text == "Hello there.\nLine two."
    assert parsed.word_count == 4


def test_parse_decodes_rfc2047_encoded_subject() -> None:
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nSubject: =?utf-8?b?R3LDvMOfZQ==?=\n"
        "Message-ID: <m2@x.com>",
        "body",
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.subject == "Grüße"


def test_parse_html_only_body_is_flattened_to_text() -> None:
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nSubject: H\nMessage-ID: <m3@x.com>\n"
        "Content-Type: text/html; charset=utf-8",
        "<html><body><p>Hello <b>bold</b></p><p>Second</p></body></html>",
    )

    parsed = parse_email(raw, MAILBOX)

    assert "<p>" not in parsed.body_text and "<b>" not in parsed.body_text
    assert "Hello" in parsed.body_text and "bold" in parsed.body_text


def test_parse_multipart_alternative_prefers_plain_text() -> None:
    raw = (
        b"From: a@x.com\r\nTo: me@oneai.com\r\nSubject: M\r\nMessage-ID: <m4@x.com>\r\n"
        b'Content-Type: multipart/alternative; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nPLAIN body\r\n"
        b"--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>HTML body</p>\r\n"
        b"--B--\r\n"
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text == "PLAIN body"


def test_parse_direction_outbound_when_from_is_mailbox() -> None:
    raw = _eml("From: me@oneai.com\nTo: client@acme.com\nMessage-ID: <m5@x>", "sent")

    parsed = parse_email(raw, MAILBOX)

    assert parsed.direction == "outbound"


def test_parse_direction_none_when_no_sender() -> None:
    raw = _eml("To: me@oneai.com\nMessage-ID: <m6@x>", "no from header")

    parsed = parse_email(raw, MAILBOX)

    assert parsed.direction is None


def test_parse_is_reply_from_subject_prefix_without_in_reply_to() -> None:
    raw = _eml("From: a@x.com\nTo: me@oneai.com\nSubject: Re: earlier\nMessage-ID: <m7@x>", "b")

    parsed = parse_email(raw, MAILBOX)

    assert parsed.is_reply is True
    assert parsed.in_reply_to is None


def test_parse_references_bracket_stripped_to_match_message_id() -> None:
    raw = _eml(
        "From: a@x.com\nTo: me@oneai.com\nMessage-ID: <m8@x>\n"
        "References: <r1@x> <r2@x>\n <r3@x>\nIn-Reply-To: <r3@x>",  # folded continuation
        "b",
    )

    parsed = parse_email(raw, MAILBOX)

    # Bracket-stripped, same form as in_reply_to/message_id — so the ancestor chain actually joins.
    assert parsed.references == ["r1@x", "r2@x", "r3@x"]
    assert parsed.in_reply_to in parsed.references


def test_parse_collects_to_cc_bcc_reply_to_recipients() -> None:
    raw = _eml(
        "From: a@x.com\nTo: t1@x.com, t2@x.com\nCc: c@x.com\nBcc: b@x.com\n"
        "Reply-To: r@x.com\nMessage-ID: <m9@x>",
        "b",
    )

    parsed = parse_email(raw, MAILBOX)

    kinds = sorted((r.kind, r.address) for r in parsed.recipients)
    assert kinds == [
        ("bcc", "b@x.com"),
        ("cc", "c@x.com"),
        ("reply_to", "r@x.com"),
        ("to", "t1@x.com"),
        ("to", "t2@x.com"),
    ]


def test_parse_duplicate_recipients_within_message_deduped_keeps_first() -> None:
    # Audit M-6: clients repeat one mailbox inside a header (199 redundant edges stored). Dedup is
    # per (kind, case-folded address): the FIRST occurrence keeps its display name and spelling;
    # the same address under a DIFFERENT kind is a distinct role and stays.
    raw = _eml(
        "From: a@x.com\nTo: Ann Smith <ann@x.com>, ann@x.com, ANN@X.COM, bob@x.com\n"
        "Cc: ann@x.com\nMessage-ID: <m-dup@x>",
        "b",
    )

    parsed = parse_email(raw, MAILBOX)

    to_rows = [(r.address, r.name) for r in parsed.recipients if r.kind == "to"]
    cc_rows = [(r.address, r.name) for r in parsed.recipients if r.kind == "cc"]
    assert to_rows == [("ann@x.com", "Ann Smith"), ("bob@x.com", None)]
    assert cc_rows == [("ann@x.com", None)]


def test_parse_attachment_captures_metadata_and_hash() -> None:
    raw = (
        b"From: a@x.com\r\nTo: me@oneai.com\r\nSubject: A\r\nMessage-ID: <m10@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSee attached.\r\n"
        b'--B\r\nContent-Type: text/csv; name="data.csv"\r\n'
        b'Content-Disposition: attachment; filename="data.csv"\r\n\r\n'
        b"a,b\r\n1,2\r\n"
        b"--B--\r\n"
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.has_attachments is True
    assert len(parsed.attachments) == 1
    attachment = parsed.attachments[0]
    assert attachment.filename == "data.csv"
    assert attachment.content_type == "text/csv"
    assert attachment.size_bytes > 0
    assert len(attachment.content_hash) == 64  # sha256 hex
    assert attachment.payload  # transient bytes present for the extractor


def test_parse_attachment_empty_filename_coalesced_to_none() -> None:
    # Audit L-8: `filename=""` must store None — one absent-value encoding, never '' vs NULL split.
    raw = (
        b"From: a@x.com\r\nTo: me@oneai.com\r\nMessage-ID: <m-ef@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSee attached.\r\n"
        b"--B\r\nContent-Type: application/octet-stream\r\n"
        b'Content-Disposition: attachment; filename=""\r\n\r\n'
        b"DATA\r\n"
        b"--B--\r\n"
    )

    parsed = parse_email(raw, MAILBOX)

    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename is None


def test_parse_received_at_prefers_supplied_internal_date() -> None:
    from datetime import UTC, datetime

    internal = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    raw = _eml("From: a@x.com\nTo: me@oneai.com\nDate: Mon, 02 Jun 2025 10:00:00 +0200", "b")

    parsed = parse_email(raw, MAILBOX, internal_date=internal)

    assert parsed.received_at == internal


def test_parse_declared_utf8_invalid_bytes_recovered_via_cp1252_fallback() -> None:
    # Declares utf-8 but the body has invalid utf-8 bytes — the strict chain falls through to a
    # cp1252 decode, recovering real characters instead of storing U+FFFD (and never raising).
    raw = (
        b"From: a@x.com\r\nTo: me@oneai.com\r\nSubject: Bad\r\nMessage-ID: <m11@x>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nvalid \xff\xfe bytes"
    )

    parsed = parse_email(raw, MAILBOX)

    assert "valid" in parsed.body_text
    assert "�" not in parsed.body_text  # recovered, not replaced


def test_parse_gb2312_mislabeled_cp1252_byte_recovered_as_euro_sign() -> None:
    # Audit M-5's exact corpus case: Outlook declares gb2312 but the body carries the cp1252 euro
    # byte 0x80 (sender-side mislabeling). gb2312 strict fails → cp1252 strict recovers '€'.
    raw = (
        b"From: kambourov@x.com\r\nTo: me@oneai.com\r\nMessage-ID: <m-gb@x>\r\n"
        b"Content-Type: text/plain; charset=gb2312\r\n\r\nprice is \x80100"
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text == "price is €100"


def test_parse_mislabeled_cyrillic_recovered_via_windows1251_fallback() -> None:
    # Cyrillic bytes behind a wrong utf-8 label, including 0x90 (undefined in cp1252) — the chain
    # must reach windows-1251 and recover the text losslessly.
    raw = (
        b"From: a@x.com\r\nTo: me@oneai.com\r\nMessage-ID: <m-cyr@x>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n\x90 \xcf\xf0\xe8\xe2\xe5\xf2"
    )

    parsed = parse_email(raw, MAILBOX)

    assert "Привет" in parsed.body_text
    assert "�" not in parsed.body_text


def test_parse_unknown_charset_decodes_without_raising() -> None:
    # An unregistered charset name (LookupError) must fall through the chain, not crash the parse.
    raw = (
        b"From: a@x.com\r\nTo: me@oneai.com\r\nMessage-ID: <m-unk@x>\r\n"
        b"Content-Type: text/plain; charset=x-no-such-charset\r\n\r\nplain ascii survives"
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text == "plain ascii survives"


def test_parse_undecodable_bytes_degrade_to_replacement_chars() -> None:
    # The robustness floor: bytes no candidate decodes strictly (0x90 kills cp1252, 0x98 kills
    # windows-1251, both kill utf-8) still parse — degraded to U+FFFD, never an exception.
    raw = (
        b"From: a@x.com\r\nTo: me@oneai.com\r\nMessage-ID: <m-und@x>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nx \x90\x98 y"
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text.startswith("x ") and parsed.body_text.endswith(" y")
    assert "�" in parsed.body_text


def test_parse_garbage_input_returns_best_effort_email() -> None:
    parsed = parse_email(b"this is not a valid email at all", MAILBOX)

    # Assert the parser's DERIVED outputs on garbage (not the constant parse_status): a hash dedup
    # key (no Message-ID), no sender, and the bodyless input captured best-effort as the body.
    assert parsed.dedup_key.startswith("sha256:")
    assert parsed.from_address is None
    assert parsed.message_id is None
    assert parsed.body_text == "this is not a valid email at all"


def test_parse_strips_nul_bytes_from_body_and_headers() -> None:
    raw = (
        b"From: a@x.com\r\nMessage-ID: <n@x>\r\nSubject: bad\x00subj\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nbad\x00body"
    )

    parsed = parse_email(raw, MAILBOX)

    assert "\x00" not in parsed.body_text and parsed.body_text == "badbody"
    assert all("\x00" not in str(v) for v in parsed.headers.values())


def test_sanitize_body_text_strips_lone_surrogates_keeping_utf8_storability() -> None:
    # pdfminer/pypdf surface lone surrogates (broken ToUnicode CMaps) through the SAME sanitizer
    # email bodies use; asyncpg raises UnicodeEncodeError at flush on any survivor, so the
    # guarantee is: the result is storable as UTF-8, period.
    polluted = "ok \ud800 mid \udfff end"

    sanitized = sanitize_body_text(polluted)

    assert sanitized == "ok  mid  end"
    sanitized.encode("utf-8")  # must not raise — the storability guarantee


def test_parse_strips_c0_control_chars_from_body_keeps_tab_and_newline() -> None:
    # Audit L-6: BEL/VT-class C0 chars survived into 19 stored bodies. They must be stripped from
    # body_text while tab and newline (legitimate layout) survive.
    raw = (
        b"From: a@x.com\r\nMessage-ID: <m-ctl@x>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"a\x07b\x0bc\td\ne"
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.body_text == "abc\td\ne"


def test_parse_overlong_address_capped_to_column_width_keeps_addr_spec() -> None:
    # Cap to the 320-char column, and the capped value must still be addr-spec shaped to be kept.
    raw = _eml(f"From: {'x' * 200}@{'y' * 195}.com\nMessage-ID: <o@x>", "b")

    parsed = parse_email(raw, MAILBOX)

    assert parsed.from_address is not None and len(parsed.from_address) == 320
    assert "@" in parsed.from_address


def test_parse_overlong_localpart_truncation_without_at_sign_stores_none() -> None:
    # When the 320-char cap cuts the '@' off entirely, the capped value is no longer addr-spec
    # shaped — store None (audit L-7's shape contract), never a 320-char garbage token.
    raw = _eml(f"From: {'x' * 400}@host.com\nMessage-ID: <o2@x>", "b")

    parsed = parse_email(raw, MAILBOX)

    assert parsed.from_address is None


def test_parse_from_without_addr_spec_stores_null_from_address() -> None:
    # Audit L-7: Exchange NDRs put a bare display token in From; getaddresses surfaces it in the
    # ADDRESS slot. It must NOT be stored as from_address — but the raw token still reaches the
    # .flags classifiers (→ automated) and survives verbatim in the stored headers.
    raw = _eml(
        "From: System Administrator\nTo: me@oneai.com\n"
        "Subject: Undeliverable: status update\nMessage-ID: <m-ndr@x>",
        "Your message did not reach the recipient.",
    )

    parsed = parse_email(raw, MAILBOX)

    assert parsed.from_address is None
    assert parsed.is_automated is True  # the raw token reached the flags classifier
    # The token survives in the stored headers (policy.default re-quotes the bare display token).
    assert "System Administrator" in str(parsed.headers["From"])


def test_parse_naive_date_pinned_to_utc() -> None:
    from datetime import UTC

    no_tz = parse_email(_eml("From: a@x\nDate: Mon, 02 Jun 2025 10:00:00", "b"), MAILBOX)
    minus_zero = parse_email(_eml("From: a@x\nDate: Mon, 02 Jun 2025 10:00:00 -0000", "b"), MAILBOX)

    assert no_tz.sent_at is not None and no_tz.sent_at.tzinfo == UTC
    assert minus_zero.sent_at is not None and minus_zero.sent_at.utcoffset().total_seconds() == 0


def test_parse_pathological_recursion_degrades_not_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Audit C01: a ~300-deep multipart raises RecursionError in the strict parse; parse_email MUST
    # catch it and return a degraded parse_status='failed' stub (stored, never dropped), not raise.
    import app.connectors.imap.parsing.email_parser as parser_mod

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(parser_mod, "_parse_email_strict", _boom)
    raw = _eml("From: a@x\nTo: me@oneai.com\nMessage-ID: <deep@x>", "body")

    parsed = parse_email(raw, MAILBOX)  # must not raise

    assert parsed.parse_status == "failed"
    assert parsed.dedup_key.startswith("sha256:")  # stable raw-byte key so the stub is idempotent
    assert parsed.message_id is None and parsed.body_text == ""


def test_parse_non_recursion_failure_propagates_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only a DETERMINISTIC RecursionError degrades-and-stores; any OTHER (maybe transient) failure
    # must PROPAGATE so the runner retries it next run — never frozen as a permanent empty stub.
    import app.connectors.imap.parsing.email_parser as parser_mod

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("a transient-ish parse blip")

    monkeypatch.setattr(parser_mod, "_parse_email_strict", _boom)

    with pytest.raises(ValueError):
        parse_email(_eml("From: a@x\nMessage-ID: <x@x>", "b"), MAILBOX)


def test_parse_genuinely_deep_multipart_never_raises() -> None:
    # End-to-end on real bytes: a deeply nested multipart must not escape as an exception, whether
    # it parses or degrades. Built deep enough to stress the parser; assert only that none flies.
    payload = b"Content-Type: text/plain\r\n\r\nhi\r\n"
    for index in range(400):
        boundary = f"b{index}".encode()
        payload = (
            b'Content-Type: multipart/mixed; boundary="' + boundary + b'"\r\n\r\n'
            b"--" + boundary + b"\r\n" + payload + b"\r\n--" + boundary + b"--\r\n"
        )
    raw = b"From: a@x.com\r\nTo: me@oneai.com\r\nMessage-ID: <nested@x>\r\n" + payload

    parsed = parse_email(raw, MAILBOX)  # never raises (parsed or degraded)

    assert parsed.dedup_key.startswith("sha256:")
