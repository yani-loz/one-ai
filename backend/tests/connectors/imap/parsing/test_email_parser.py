"""
Role: Unit tests for the RFC822 email parser — body extraction (plain/html/alternative), address +
      header decoding, derived flags wiring, the content-identity dedup_key, and the never-raises
      robustness contract on malformed AND pathological input.
Used by: pytest (tests/connectors/imap/parsing). Pure — no DB, no network.
Depends on: app.connectors.imap.parsing.email_parser. Builds raw .eml bytes inline per test.
Key invariants tested:
  - dedup_key is a content identity (hash of Message-ID + From/Subject/Date + body, else raw-byte
    hash): STABLE across parses, MATCHES across folder copies (differing trace headers), and DIFFERS
    for two distinct emails — even ones reusing a Message-ID (folder-stable yet injective).
  - parse_email NEVER raises — garbage degrades to a best-effort parse; a pathological deep
    multipart degrades to parse_status='failed' (stored, not dropped).
"""

from __future__ import annotations

import pytest

from app.connectors.imap.parsing.email_parser import parse_email

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


def test_parse_without_message_id_uses_stable_raw_hash_dedup_key() -> None:
    raw = _eml("From: a@x.com\nTo: me@oneai.com\nSubject: NoID", "no message id here")

    first = parse_email(raw, MAILBOX)
    second = parse_email(raw, MAILBOX)

    assert first.message_id is None
    assert first.dedup_key.startswith("sha256:")
    assert first.dedup_key == second.dedup_key  # deterministic across repeated parses


def test_parse_received_at_prefers_supplied_internal_date() -> None:
    from datetime import UTC, datetime

    internal = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    raw = _eml("From: a@x.com\nTo: me@oneai.com\nDate: Mon, 02 Jun 2025 10:00:00 +0200", "b")

    parsed = parse_email(raw, MAILBOX, internal_date=internal)

    assert parsed.received_at == internal


def test_parse_bad_charset_does_not_raise_and_returns_body() -> None:
    # Declares utf-8 but the body has invalid utf-8 bytes — must decode with replacement, not crash.
    raw = (
        b"From: a@x.com\r\nTo: me@oneai.com\r\nSubject: Bad\r\nMessage-ID: <m11@x>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nvalid \xff\xfe bytes"
    )

    parsed = parse_email(raw, MAILBOX)

    assert "valid" in parsed.body_text  # replacement chars allowed, no exception


def test_parse_garbage_input_returns_best_effort_email() -> None:
    parsed = parse_email(b"this is not a valid email at all", MAILBOX)

    # Assert the parser's DERIVED outputs on garbage (not the constant parse_status): a hash dedup
    # key (no Message-ID), no sender, and the bodyless input captured best-effort as the body.
    assert parsed.dedup_key.startswith("sha256:")
    assert parsed.from_address is None
    assert parsed.message_id is None
    assert parsed.body_text == "this is not a valid email at all"


def test_parse_message_id_with_internal_whitespace_preserved_no_collision() -> None:
    # policy.default would truncate at the first space; we read the raw header so the full id
    # survives and two distinct malformed ids do NOT collide into one dedup_key (silent data loss).
    legit = parse_email(_eml("From: a@x\nMessage-ID: <victim@host>", "b"), MAILBOX)
    malformed = parse_email(_eml("From: a@x\nMessage-ID: <victim@host extra>", "b"), MAILBOX)

    assert legit.dedup_key != malformed.dedup_key
    assert "victim@host" == legit.message_id


def test_parse_strips_nul_bytes_from_body_and_headers() -> None:
    raw = (
        b"From: a@x.com\r\nMessage-ID: <n@x>\r\nSubject: bad\x00subj\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nbad\x00body"
    )

    parsed = parse_email(raw, MAILBOX)

    assert "\x00" not in parsed.body_text and parsed.body_text == "badbody"
    assert all("\x00" not in str(v) for v in parsed.headers.values())


def test_parse_overlong_address_capped_to_column_width() -> None:
    raw = _eml(f"From: {'x' * 400}@host.com\nMessage-ID: <o@x>", "b")

    parsed = parse_email(raw, MAILBOX)

    assert parsed.from_address is not None and len(parsed.from_address) == 320


def test_parse_naive_date_pinned_to_utc() -> None:
    from datetime import UTC

    no_tz = parse_email(_eml("From: a@x\nDate: Mon, 02 Jun 2025 10:00:00", "b"), MAILBOX)
    minus_zero = parse_email(_eml("From: a@x\nDate: Mon, 02 Jun 2025 10:00:00 -0000", "b"), MAILBOX)

    assert no_tz.sent_at is not None and no_tz.sent_at.tzinfo == UTC
    assert minus_zero.sent_at is not None and minus_zero.sent_at.utcoffset().total_seconds() == 0


def test_parse_reused_message_id_different_content_gets_distinct_dedup_keys() -> None:
    # Dedup poisoning (audit C02): a decoy planting <reused@x> must NOT let a later, genuinely
    # different email reusing that id collapse onto it — distinct content ⇒ distinct dedup_key.
    decoy = _eml("From: a@x\nTo: me@oneai.com\nMessage-ID: <reused@x>", "hello, benign")
    genuine = _eml("From: a@x\nTo: me@oneai.com\nMessage-ID: <reused@x>", "WIRE THE 2M NOW")

    decoy_key = parse_email(decoy, MAILBOX).dedup_key
    genuine_key = parse_email(genuine, MAILBOX).dedup_key

    assert decoy_key != genuine_key
    assert decoy_key.startswith("sha256:") and genuine_key.startswith("sha256:")
    # Idempotent: identical wire bytes still collide (re-fetch).
    assert parse_email(decoy, MAILBOX).dedup_key == decoy_key


def test_parse_same_email_across_folders_dedups_despite_trace_headers() -> None:
    # Audit B05: two IMAP-folder copies of ONE email differ only by prepended trace headers
    # (Received/X-Folder); the content-identity key (Message-ID + body hash) must MATCH so they
    # dedup to one row — raw-byte keying stored them twice (~40% duplication on the real corpus).
    base = "From: a@x\nTo: me@oneai.com\nSubject: Q3\nMessage-ID: <same@x>"
    inbox = _eml("Received: from mta1 by host1\n" + base, "the quarterly numbers")
    archive = _eml(
        "Received: from mta2 by host2\nX-Folder: Archive\n" + base, "the quarterly numbers"
    )

    assert parse_email(inbox, MAILBOX).dedup_key == parse_email(archive, MAILBOX).dedup_key

    # ...yet a DIFFERENT body reusing the same Message-ID still gets a distinct key (poison-safe).
    poison = _eml("Received: from mta3\n" + base, "WIRE 2M NOW")
    assert parse_email(poison, MAILBOX).dedup_key != parse_email(inbox, MAILBOX).dedup_key


def test_parse_reused_message_id_same_body_different_headers_stays_distinct() -> None:
    # Audit B05 over-dedup guard: an appliance sender reusing a Message-ID + an identical templated
    # body, varying only Subject/Date per event, must get DISTINCT keys — folding the folder-stable
    # logical headers into the key stops the recurrences from being silently deduped away (lost).
    base = "From: monitor@corp\nTo: me@oneai.com\nMessage-ID: <const@appliance>"
    event1 = _eml(
        base + "\nSubject: ALERT 09:00\nDate: Mon, 02 Jun 2025 09:00:00 +0000", "Backup failed"
    )
    event2 = _eml(
        base + "\nSubject: ALERT 10:00\nDate: Mon, 02 Jun 2025 10:00:00 +0000", "Backup failed"
    )

    assert parse_email(event1, MAILBOX).dedup_key != parse_email(event2, MAILBOX).dedup_key


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
    assert parsed.dedup_key.startswith("sha256:")  # stable content key so the stub is idempotent
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
