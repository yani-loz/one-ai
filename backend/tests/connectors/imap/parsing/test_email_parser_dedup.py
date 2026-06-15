"""
Role: Unit tests for the parser's content-identity dedup_key (audit H-1) — folder independence
      across Outlook MIME-boundary regeneration and transfer-encoding re-wraps, attachment-hash
      participation, the planted-decoy / appliance-sender no-over-dedup guards, and the raw-byte
      fallback for id-less messages. Split from test_email_parser.py to respect the size cap (A2).
Used by: pytest (tests/connectors/imap/parsing). Pure — no DB, no network.
Depends on: app.connectors.imap.parsing.email_parser. Builds raw .eml bytes inline per test.
Key invariants tested:
  - The key hashes DECODED content (normalized Message-ID + From/Subject/Date + body_text + the
    text/html body candidate's digest + sorted attachment content hashes), so folder copies
    differing only in regenerated `----=_NextPart_...` boundaries or re-wrapped
    quoted-printable/base64 share ONE key.
  - Distinct content NEVER collides: a decoy or appliance sender reusing a Message-ID gets a
    distinct key — INCLUDING when the difference lives only in the unselected text/html
    alternative behind an identical plain stub (2026-06-10 review fixup); messages without a
    usable Message-ID fall back to the stable raw-byte hash. The TNEF-interior leg of the key
    (the v5 flattened body + embedded bytes) is tested in test_email_parser_dedup_tnef.py
    (A2 size split).
"""

from __future__ import annotations

import base64

from app.connectors.imap.parsing.email_parser import parse_email

MAILBOX = "me@oneai.com"


def _eml(headers: str, body: str) -> bytes:
    """Assemble raw RFC822 bytes from a header block + body (CRLF line endings, blank separator).

    Uses a bare-LF blank separator before the single `\\n`→`\\r\\n` pass so the header/body boundary
    becomes a CLEAN `\\r\\n\\r\\n` (not the `\\r\\r\\n` a literal CRLF yields under the replace).
    """
    return (headers.strip() + "\n\n" + body).replace("\n", "\r\n").encode("utf-8")


def _outlook_folder_copy(boundary: str, thread_index: str) -> bytes:
    """One Outlook re-serialization of the SAME logical email (mirrors the corpus's 9-copy "Демо"
    group): the MIME boundary recurs through the BODY bytes and Thread-Index is regenerated per
    folder copy — the exact byte-level variance the audit diffed (H-1)."""
    return (
        "From: =?utf-8?B?0K/QvdC4IExvemFub3Y=?= <yani.lozanov@ethera-tech.com>\r\n"
        "To: client@acme.com\r\n"
        "Subject: =?utf-8?B?0JTQtdC80L4=?=\r\n"
        "Date: Tue, 28 Apr 2026 10:15:00 +0300\r\n"
        "Message-ID: <082a01dcc79f$0fc0e6e0$2f42b4a0$@ethera-tech.com>\r\n"
        f"Thread-Index: {thread_index}\r\n"
        f'Content-Type: multipart/alternative; boundary="{boundary}"\r\n'
        "\r\n"
        f"--{boundary}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "the demo agenda for Tuesday\r\n"
        f"--{boundary}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>the demo agenda for Tuesday</p>\r\n"
        f"--{boundary}--\r\n"
    ).encode()


def _qp_copy(qp_body: str) -> bytes:
    """A quoted-printable text/plain message; `qp_body` is the WIRE (still-encoded) body."""
    return (
        "From: a@ethera-tech.com\r\n"
        "To: me@oneai.com\r\n"
        "Subject: Numbers\r\n"
        "Date: Mon, 02 Jun 2025 09:00:00 +0000\r\n"
        "Message-ID: <qp-wrap@ethera-tech.com>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Transfer-Encoding: quoted-printable\r\n"
        "\r\n"
        f"{qp_body}\r\n"
    ).encode()


def _mixed_with_attachments(*base64_payloads: str) -> bytes:
    """A multipart/mixed message with a fixed text body and one base64 attachment per argument."""
    attachment_parts = "".join(
        '--B\r\nContent-Type: application/octet-stream; name="data.bin"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        'Content-Disposition: attachment; filename="data.bin"\r\n'
        f"\r\n{encoded}\r\n"
        for encoded in base64_payloads
    )
    return (
        "From: a@x.com\r\nTo: me@oneai.com\r\nSubject: A\r\n"
        "Date: Mon, 02 Jun 2025 09:00:00 +0000\r\nMessage-ID: <att@x>\r\n"
        'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSee attached.\r\n"
        f"{attachment_parts}"
        "--B--\r\n"
    ).encode()


def test_parse_outlook_regenerated_boundary_copies_same_dedup_key() -> None:
    # THE H-1 headline: Outlook re-serializes each IMAP folder copy with FRESH ----=_NextPart_...
    # boundaries recurring INSIDE the body bytes (the corpus's 9-row "Демо" group is byte-identical
    # except boundaries + Thread-Index), so raw-serialization keying stored 39.3% duplicate rows.
    # The boundary strings below are the REAL ones from the two audited .eml copies.
    sent_copy = _outlook_folder_copy(
        "----=_NextPart_000_ABFD_01DCC7BF.3C983040", "AdzHv0BSDOKHbsEKQ1aPxAuLmkPq1g=="
    )
    trash_copy = _outlook_folder_copy(
        "----=_NextPart_000_F16C_01DCC7C0.0DA3A930", "AdzHwA2jqTBhQk93SLmJ0sFafA9HhQ=="
    )

    sent_parsed = parse_email(sent_copy, MAILBOX)
    trash_parsed = parse_email(trash_copy, MAILBOX)

    assert sent_copy != trash_copy  # the raw serializations genuinely differ...
    assert sent_parsed.dedup_key == trash_parsed.dedup_key  # ...but the logical identity matches


def test_parse_quoted_printable_soft_wrap_variants_same_dedup_key() -> None:
    # Audit H-1 residual (~120 groups): re-serialization re-wraps quoted-printable at different
    # soft line breaks (`=\r\n`) per folder copy; the DECODED text is identical → one key.
    copy_a = _qp_copy("the quarterly numbers are fin=\r\nal")
    copy_b = _qp_copy("the quart=\r\nerly numbers are final")

    parsed_a = parse_email(copy_a, MAILBOX)
    parsed_b = parse_email(copy_b, MAILBOX)

    assert parsed_a.body_text == "the quarterly numbers are final"
    assert parsed_a.dedup_key == parsed_b.dedup_key


def test_parse_attachment_base64_rewrap_same_dedup_key() -> None:
    # The attachment leg of folder independence: the key folds in content_hash (sha256 of DECODED
    # attachment bytes), so a re-serialization that re-wraps the base64 lines must not change it.
    encoded = base64.b64encode(b"col1,col2\n1,2\n3,4\n").decode()
    rewrapped = "\r\n".join(encoded[i : i + 8] for i in range(0, len(encoded), 8))

    one_line = parse_email(_mixed_with_attachments(encoded), MAILBOX)
    wrapped = parse_email(_mixed_with_attachments(rewrapped), MAILBOX)

    assert one_line.attachments[0].content_hash == wrapped.attachments[0].content_hash
    assert one_line.dedup_key == wrapped.dedup_key


def test_parse_attachment_order_swapped_same_dedup_key() -> None:
    # Attachment hashes enter the key SORTED — part order is a serialization detail, not identity.
    first = base64.b64encode(b"contract v1").decode()
    second = base64.b64encode(b"annex A").decode()

    forward = parse_email(_mixed_with_attachments(first, second), MAILBOX)
    swapped = parse_email(_mixed_with_attachments(second, first), MAILBOX)

    assert forward.dedup_key == swapped.dedup_key


def test_parse_attachment_content_differs_distinct_dedup_keys() -> None:
    # Same headers + same body but a DIFFERENT attachment payload is a different logical email —
    # the attachment content hashes participating in the key must keep both stored.
    version_1 = parse_email(
        _mixed_with_attachments(base64.b64encode(b"contract v1").decode()), MAILBOX
    )
    version_2 = parse_email(
        _mixed_with_attachments(base64.b64encode(b"contract v2").decode()), MAILBOX
    )

    assert version_1.dedup_key != version_2.dedup_key


def test_parse_without_message_id_uses_stable_raw_hash_dedup_key() -> None:
    raw = _eml("From: a@x.com\nTo: me@oneai.com\nSubject: NoID", "no message id here")

    first = parse_email(raw, MAILBOX)
    second = parse_email(raw, MAILBOX)

    assert first.message_id is None
    assert first.dedup_key.startswith("sha256:")
    assert first.dedup_key == second.dedup_key  # deterministic across repeated parses


def test_parse_message_id_with_internal_whitespace_preserved_no_collision() -> None:
    # policy.default would truncate at the first space; we read the raw header so the full id
    # survives and two distinct malformed ids do NOT collide into one dedup_key (silent data loss).
    legit = parse_email(_eml("From: a@x\nMessage-ID: <victim@host>", "b"), MAILBOX)
    malformed = parse_email(_eml("From: a@x\nMessage-ID: <victim@host extra>", "b"), MAILBOX)

    assert legit.dedup_key != malformed.dedup_key
    assert "victim@host" == legit.message_id


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
    # Audit B05: two IMAP-folder copies of ONE email differ by prepended trace headers
    # (Received/X-Folder); those never enter the content-identity key, so the copies MATCH.
    base = "From: a@x\nTo: me@oneai.com\nSubject: Q3\nMessage-ID: <same@x>"
    inbox = _eml("Received: from mta1 by host1\n" + base, "the quarterly numbers")
    archive = _eml(
        "Received: from mta2 by host2\nX-Folder: Archive\n" + base, "the quarterly numbers"
    )

    assert parse_email(inbox, MAILBOX).dedup_key == parse_email(archive, MAILBOX).dedup_key

    # ...yet a DIFFERENT body reusing the same Message-ID still gets a distinct key (poison-safe).
    poison = _eml("Received: from mta3\n" + base, "WIRE 2M NOW")
    assert parse_email(poison, MAILBOX).dedup_key != parse_email(inbox, MAILBOX).dedup_key


def _alternative_copy(html_wire: str, html_cte: str | None = None) -> bytes:
    """A multipart/alternative with FIXED Message-ID/From/Subject/Date and plain stub but a
    parameterized html part — the appliance-sender shape whose real content lives ONLY in the
    HTML alternative. `html_wire` is the wire body; `html_cte` adds a transfer encoding."""
    cte_header = f"Content-Transfer-Encoding: {html_cte}\r\n" if html_cte else ""
    return (
        "From: Appliance <alerts@appliance.local>\r\n"
        "To: me@oneai.com\r\n"
        "Subject: Camera event\r\n"
        "Date: Mon, 02 Jun 2025 09:00:00 +0000\r\n"
        "Message-ID: <static@appliance.local>\r\n"
        'Content-Type: multipart/alternative; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        "View this message in an HTML capable client\r\n"
        "--B\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"{cte_header}\r\n"
        f"{html_wire}\r\n"
        "--B--\r\n"
    ).encode()


def test_parse_reused_message_id_html_only_difference_distinct_dedup_keys() -> None:
    # 2026-06-10 review fixup: identical Message-ID/From/Subject/Date + an identical static plain
    # stub, with the real event content ONLY in the HTML alternative. body_text (the selected
    # plain part) cannot see the difference and iter_attachments never yields the unselected html
    # part — the html-digest field in the key must keep the two events apart (appliance senders +
    # the TC-IM-C02 planted-decoy suppression this would otherwise reopen).
    event_1 = parse_email(_alternative_copy("<p>Motion detected at DOOR 1</p>"), MAILBOX)
    event_2 = parse_email(_alternative_copy("<p>Intruder alarm ZONE 4</p>"), MAILBOX)

    assert event_1.body_text == event_2.body_text  # the selected plain stub really is identical
    assert event_1.dedup_key != event_2.dedup_key


def test_parse_html_whitespace_only_difference_keeps_distinct_keys() -> None:
    # Never-false-dedup regression (the reverted 2026-06-14 whitespace-collapse): two appliance
    # copies with an IDENTICAL plain stub + REUSED Message-ID whose html differs ONLY in whitespace
    # (a <pre> column re-alignment) MUST keep DISTINCT keys — the html digest is the sole
    # differentiator, so collapsing whitespace would silently drop one as a dupe (mail loss). The
    # html digest must therefore NEVER whitespace-collapse / structure-flatten.
    aligned = parse_email(_alternative_copy("<pre>Long   500\r\nShort  200</pre>"), MAILBOX)
    realigned = parse_email(_alternative_copy("<pre>Long 500\r\nShort 200</pre>"), MAILBOX)

    assert aligned.body_text == realigned.body_text  # identical selected plain stub
    assert aligned.dedup_key != realigned.dedup_key  # html whitespace diff keeps them apart


def test_parse_html_alternative_qp_rewrap_same_dedup_key() -> None:
    # The html digest must not re-open H-1: two folder copies whose html alternative is
    # quoted-printable re-wrapped at different soft line breaks decode identically → ONE key.
    copy_a = _alternative_copy("<p>final numbers attach=\r\ned</p>", html_cte="quoted-printable")
    copy_b = _alternative_copy("<p>final numbers a=\r\nttached</p>", html_cte="quoted-printable")

    parsed_a = parse_email(copy_a, MAILBOX)
    parsed_b = parse_email(copy_b, MAILBOX)

    assert copy_a != copy_b  # the wire serializations genuinely differ...
    assert parsed_a.dedup_key == parsed_b.dedup_key  # ...but decode to one logical email


def test_parse_html_only_markup_difference_distinct_dedup_keys() -> None:
    # An html-ONLY email pair whose markup difference html2text flattens away (images are
    # dropped): body_text matches, yet the raw decoded html digest must split the keys.
    base = (
        "From: a@x.com\nTo: me@oneai.com\nSubject: H\n"
        "Date: Mon, 02 Jun 2025 09:00:00 +0000\nMessage-ID: <html-only@x>\n"
        "Content-Type: text/html; charset=utf-8"
    )
    variant_a = parse_email(_eml(base, '<p>Update</p><img src="https://cdn.x/a.png">'), MAILBOX)
    variant_b = parse_email(_eml(base, '<p>Update</p><img src="https://cdn.x/b.png">'), MAILBOX)

    assert variant_a.body_text == variant_b.body_text  # html2text dropped the only difference
    assert variant_a.dedup_key != variant_b.dedup_key


def test_parse_reused_message_id_same_body_different_headers_stays_distinct() -> None:
    # Audit B05 over-dedup guard: an appliance sender reusing a Message-ID + an identical templated
    # body, varying only Subject/Date per event, must get DISTINCT keys — folding the decoded
    # logical headers into the key stops the recurrences from being silently deduped away (lost).
    base = "From: monitor@corp\nTo: me@oneai.com\nMessage-ID: <const@appliance>"
    event1 = _eml(
        base + "\nSubject: ALERT 09:00\nDate: Mon, 02 Jun 2025 09:00:00 +0000", "Backup failed"
    )
    event2 = _eml(
        base + "\nSubject: ALERT 10:00\nDate: Mon, 02 Jun 2025 10:00:00 +0000", "Backup failed"
    )

    assert parse_email(event1, MAILBOX).dedup_key != parse_email(event2, MAILBOX).dedup_key


def test_parse_headers_only_messages_reusing_message_id_stay_distinct() -> None:
    # 2026-06-11 review fixup: a broken appliance mailer emits EMPTY-body notifications reusing
    # one Message-ID with no Date header — only the trace headers differ per event. Content
    # keying has nothing to key on, so these must take the injective raw-byte fallback; the old
    # content path silently skipped every event after the first.
    base = "From: scanner@corp\nTo: me@oneai.com\nMessage-ID: <static@scanner>\nSubject: scan"
    event1 = _eml(base + "\nReceived: from relay-a.corp by mx.corp; id aaa111", "")
    event2 = _eml(base + "\nReceived: from relay-b.corp by mx.corp; id bbb222", "")

    assert parse_email(event1, MAILBOX).dedup_key != parse_email(event2, MAILBOX).dedup_key


def test_parse_raw_eight_bit_cyrillic_subjects_stay_distinct() -> None:
    # 2026-06-11 review fixup: non-RFC2047 raw 8-bit (cp1251) Subjects surface as lone
    # surrogates; encoding the hash input with errors='replace' collapsed EVERY non-ASCII byte
    # to '?', folding two distinct Cyrillic alerts into one key (silent drop of the second).
    # surrogateescape round-trips the original bytes, keeping the keys distinct.
    def _cyrillic_event(subject_cp1251: bytes) -> bytes:
        return (
            b"From: appliance@corp.bg\r\nTo: me@oneai.com\r\n"
            b"Message-ID: <static@appliance.bg>\r\n"
            b"Date: Mon, 02 Jun 2025 09:00:00 +0000\r\n"
            b"Subject: " + subject_cp1251 + b"\r\n\r\nsee subject\r\n"
        )

    disk_alert = _cyrillic_event("Грешка диск".encode("cp1251"))
    memory_alert = _cyrillic_event("Грешка памет".encode("cp1251"))

    key_disk = parse_email(disk_alert, MAILBOX).dedup_key
    key_memory = parse_email(memory_alert, MAILBOX).dedup_key
    assert key_disk != key_memory
    # And the SAME raw copy re-fetched still keys identically (stability not sacrificed).
    assert parse_email(disk_alert, MAILBOX).dedup_key == key_disk


def test_parse_timezone_rerendered_date_copies_same_dedup_key() -> None:
    # 2026-06-11 verification finding: Outlook re-renders the Date header per folder copy in a
    # different zone — '10:43:53 +0200' vs '11:43:53 +0300' are the SAME instant (649 duplicate
    # groups survived on this). The key must use the UTC-normalized instant, not the rendering.
    base = (
        "From: yani.lozanov@ethera-tech.com\nTo: client@acme.com\nSubject: RE: Info\n"
        "Message-ID: <000501db6729$9e25d080$da717180$@ethera-tech.com>"
    )
    copy_eet = _eml(base + "\nDate: Wed, 15 Jan 2025 10:43:53 +0200", "agreed, see notes")
    copy_eest = _eml(base + "\nDate: Wed, 15 Jan 2025 11:43:53 +0300", "agreed, see notes")
    distinct_instant = _eml(base + "\nDate: Wed, 15 Jan 2025 10:44:53 +0200", "agreed, see notes")

    key_eet = parse_email(copy_eet, MAILBOX).dedup_key
    assert key_eet == parse_email(copy_eest, MAILBOX).dedup_key
    # A genuinely different send moment still splits the key (appliance/decoy guard intact).
    assert key_eet != parse_email(distinct_instant, MAILBOX).dedup_key


def test_parse_regenerated_tnef_copies_same_dedup_key() -> None:
    # 2026-06-11 verification finding: winmail.dat is a volatile container — Outlook regenerates
    # it per folder copy (ALL 253 attachment-divergent duplicate groups involved TNEF). The TNEF
    # attachment contributes a stable presence marker, not its per-copy bytes.
    def _tnef_copy(tnef_payload: bytes) -> bytes:
        encoded = base64.b64encode(tnef_payload).decode()
        return (
            "From: a@corp\r\nTo: me@oneai.com\r\nSubject: doc\r\n"
            "Date: Mon, 02 Jun 2025 09:00:00 +0000\r\n"
            "Message-ID: <tnef-test@corp>\r\n"
            'Content-Type: multipart/mixed; boundary="B1"\r\n\r\n'
            "--B1\r\nContent-Type: text/plain\r\n\r\nsee attached\r\n"
            "--B1\r\nContent-Type: application/ms-tnef; name=winmail.dat\r\n"
            "Content-Transfer-Encoding: base64\r\n"
            'Content-Disposition: attachment; filename="winmail.dat"\r\n\r\n'
            f"{encoded}\r\n--B1--\r\n"
        ).encode()

    copy_a = _tnef_copy(b"\x78\x9f\x3e\x22TNEF-rendering-one")
    copy_b = _tnef_copy(b"\x78\x9f\x3e\x22TNEF-rendering-two")

    assert parse_email(copy_a, MAILBOX).dedup_key == parse_email(copy_b, MAILBOX).dedup_key
    # A NON-TNEF attachment difference must still split the key (real content participates).
    pdf_a = _mixed_with_attachments(base64.b64encode(b"pdf-bytes-one").decode())
    pdf_b = _mixed_with_attachments(base64.b64encode(b"pdf-bytes-two").decode())
    assert parse_email(pdf_a, MAILBOX).dedup_key != parse_email(pdf_b, MAILBOX).dedup_key


# ── 2026-06-11 cross-vendor (GPT) review fixups ──
# (The TNEF-interior leg — embedded files + the v5 flattened body — lives in
# test_email_parser_dedup_tnef.py, split per the A2 size cap.)


def test_parse_reused_message_id_different_recipients_distinct_dedup_keys() -> None:
    # GPT review (MEDIUM): identical content + reused Message-ID + same instant but a DIFFERENT
    # audience is a different email — the canonical To/Cc envelope must split the key.
    base = (
        "From: hr@corp\nSubject: offer\nMessage-ID: <const@corp>\n"
        "Date: Mon, 02 Jun 2025 09:00:00 +0000"
    )
    to_anna = _eml(base + "\nTo: anna@x.com", "your offer is attached")
    to_boris = _eml(base + "\nTo: boris@x.com", "your offer is attached")

    assert parse_email(to_anna, MAILBOX).dedup_key != parse_email(to_boris, MAILBOX).dedup_key


def test_parse_bcc_only_difference_same_dedup_key() -> None:
    # The Sent-folder copy carries Bcc; the received copy of the SAME logical email does not.
    # Bcc must NOT participate, or every Bcc'd email stores twice.
    base = (
        "From: me@oneai.com\nTo: client@x.com\nSubject: s\nMessage-ID: <bcc@corp>\n"
        "Date: Mon, 02 Jun 2025 09:00:00 +0000"
    )
    sent_copy = _eml(base + "\nBcc: archive@oneai.com", "hello")
    received_copy = _eml(base, "hello")

    sent_key = parse_email(sent_copy, MAILBOX).dedup_key
    assert sent_key == parse_email(received_copy, MAILBOX).dedup_key


def test_parse_same_attachment_bytes_different_filename_distinct_dedup_keys() -> None:
    # GPT review (MEDIUM): same payload re-sent under a different NAME is a distinct email
    # (the name is content a human acts on); folder copies never rename, so no under-dedup risk.
    payload = base64.b64encode(b"identical-bytes").decode()

    def _named(filename: str) -> bytes:
        return (
            "From: a@corp\r\nTo: me@oneai.com\r\nSubject: s\r\n"
            "Date: Mon, 02 Jun 2025 09:00:00 +0000\r\nMessage-ID: <name@corp>\r\n"
            'Content-Type: multipart/mixed; boundary="B1"\r\n\r\n'
            "--B1\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
            f"--B1\r\nContent-Type: application/pdf; name={filename}\r\n"
            "Content-Transfer-Encoding: base64\r\n"
            f'Content-Disposition: attachment; filename="{filename}"\r\n\r\n'
            f"{payload}\r\n--B1--\r\n"
        ).encode()

    assert (
        parse_email(_named("contract_v1.pdf"), MAILBOX).dedup_key
        != parse_email(_named("contract_final.pdf"), MAILBOX).dedup_key
    )
