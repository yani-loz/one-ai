"""
Role: Unit tests for the TNEF leg of the parser's content-identity dedup_key (split from
      test_email_parser_dedup.py to respect the A2 size cap) — the v5 INTERIOR digest:
      regenerated containers with the same interior still fold while distinct embedded files
      split (2026-06-11 cross-vendor review), and the striprtf-FLATTENED RTF body now joins
      the digest (2026-06-12 review: markup-only regeneration folds, body-text differences
      split, presence vs absence splits, and the LZFu-bomb bound degrades over-bound bodies
      to the fixed 'rtf-over-bound' token — KEY-CONSISTENCY with extractors/tnef.py).
Used by: pytest (tests/connectors/imap/parsing). Pure — no DB, no network.
Depends on: app.connectors.imap.parsing.email_parser (+ .dedup_key for monkeypatch targets),
            the extractors conftest build_tnef builder. Builds raw .eml bytes inline per test.
"""

from __future__ import annotations

import base64

import pytest

from app.connectors.imap.parsing import dedup_key as dedup_key_module
from app.connectors.imap.parsing.email_parser import parse_email
from tests.connectors.imap.parsing.extractors.conftest import build_tnef

MAILBOX = "me@oneai.com"


def _tnef_eml(payload: bytes, body: str = "see attached") -> bytes:
    """A multipart/mixed message carrying `payload` as its application/ms-tnef part."""
    encoded = base64.b64encode(payload).decode()
    return (
        "From: a@corp\r\nTo: me@oneai.com\r\nSubject: doc\r\n"
        "Date: Mon, 02 Jun 2025 09:00:00 +0000\r\n"
        "Message-ID: <tnef-interior@corp>\r\n"
        'Content-Type: multipart/mixed; boundary="B1"\r\n\r\n'
        f"--B1\r\nContent-Type: text/plain\r\n\r\n{body}\r\n"
        "--B1\r\nContent-Type: application/ms-tnef; name=winmail.dat\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        'Content-Disposition: attachment; filename="winmail.dat"\r\n\r\n'
        f"{encoded}\r\n--B1--\r\n"
    ).encode()


# ── 2026-06-11 cross-vendor (GPT) review fixups — the embedded-bytes leg ──


class _FakeTnef:
    """Stands in for tnefparse.TNEF at the vendor boundary: payload bytes -> embedded files.

    Mirrors the v5 interior surface the digest reads: `attachments` plus `_rtfbody` (the
    pre-decompress compressed-RTF property handle — None ⇒ the 'no-rtf' body component)."""

    interiors: dict[bytes, list[bytes]] = {}
    _rtfbody: bytes | None = None

    def __init__(self, payload: bytes) -> None:
        self.attachments = [
            type("Att", (), {"data": data})() for data in self.interiors[payload]
        ]


def test_parse_tnef_distinct_embedded_files_distinct_dedup_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GPT review (HIGH): a bare TNEF presence marker folded two DISTINCT emails whose only
    # difference was the files embedded INSIDE winmail.dat — silent loss of the second email's
    # unique attachment. The interior digest must split them...
    payload_a, payload_b = b"raw-serialization-A", b"raw-serialization-B"
    _FakeTnef.interiors = {payload_a: [b"contract-v1.docx-bytes"], payload_b: [b"OTHER-file-bytes"]}
    monkeypatch.setattr(dedup_key_module, "TNEF", _FakeTnef)

    key_a = parse_email(_tnef_eml(payload_a), MAILBOX).dedup_key
    key_b = parse_email(_tnef_eml(payload_b), MAILBOX).dedup_key

    assert key_a != key_b


def test_parse_tnef_regenerated_blob_same_interior_same_dedup_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ...while two folder copies (different raw blobs, SAME embedded files — the corpus's 253
    # regeneration groups, interior verified stable in all 271 multi-copy groups) still fold.
    payload_a, payload_b = b"raw-serialization-A", b"raw-serialization-B"
    same = [b"contract-v1.docx-bytes"]
    _FakeTnef.interiors = {payload_a: same, payload_b: same}
    monkeypatch.setattr(dedup_key_module, "TNEF", _FakeTnef)

    assert (
        parse_email(_tnef_eml(payload_a), MAILBOX).dedup_key
        == parse_email(_tnef_eml(payload_b), MAILBOX).dedup_key
    )


# ── 2026-06-12 review fixups — key v5: the FLATTENED TNEF body joins the interior digest ──


def test_parse_tnef_different_rtf_body_text_distinct_dedup_keys() -> None:
    # Key v5 headline (2026-06-12 review): the extractor reads the TNEF body but key v4 did not
    # — two DISTINCT emails differing ONLY in the winmail.dat body folded onto one key (silent
    # loss). The flattened-body component of the interior digest must split them.
    body_a = build_tnef(rtf_body=b"{\\rtf1\\ansi Quarterly report body!}")
    body_b = build_tnef(rtf_body=b"{\\rtf1\\ansi WIRE THE 2M NOW}")

    key_a = parse_email(_tnef_eml(body_a), MAILBOX).dedup_key
    key_b = parse_email(_tnef_eml(body_b), MAILBOX).dedup_key

    assert key_a != key_b


def test_parse_tnef_same_flattened_rtf_body_different_markup_same_dedup_key() -> None:
    # ...while the body participates FLATTENED, never raw: the raw RTF stream's instability
    # (4/40 sampled groups) was pure re-serialization markup, which striprtf erases — the
    # flattened text is byte-stable across ALL 271 multi-copy TNEF groups in the corpus. Two
    # folder copies whose regenerated containers differ ONLY in RTF markup must still fold.
    markup_a = build_tnef(
        rtf_body=b"{\\rtf1\\ansi\\ansicpg1252\\deff0 Quarterly \\b report\\b0  body!}"
    )
    markup_b = build_tnef(rtf_body=b"{\\rtf1\\ansi Quarterly report body!}")

    key_a = parse_email(_tnef_eml(markup_a), MAILBOX).dedup_key
    key_b = parse_email(_tnef_eml(markup_b), MAILBOX).dedup_key

    assert markup_a != markup_b  # the containers genuinely differ on the wire...
    assert key_a == key_b  # ...but flatten to ONE logical body → one key


def test_parse_tnef_rtf_body_presence_vs_absence_distinct_dedup_keys() -> None:
    # A body-bearing container and a body-less one are different content even with identical
    # (zero) embedded files: the 'no-rtf' token keeps the empty side distinct.
    with_body = build_tnef(rtf_body=b"{\\rtf1\\ansi Quarterly report body!}")
    without_body = build_tnef()

    key_with = parse_email(_tnef_eml(with_body), MAILBOX).dedup_key
    key_without = parse_email(_tnef_eml(without_body), MAILBOX).dedup_key

    assert key_with != key_without


def test_parse_tnef_over_bound_rtf_bodies_fold_to_one_token_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The LZFu-bomb bound applies on the identity path too (KEY-CONSISTENCY): an over-bound
    # compressed-RTF property is NEVER decompressed — the body component degrades to the fixed
    # 'rtf-over-bound' token, so two over-bound bodies fold (the same body the extractor skips;
    # documented residual, mirroring the extractor's 'rtf-body-over-bound' skip).
    monkeypatch.setattr(dedup_key_module, "MAX_COMPRESSED_RTF_BYTES", 16)
    over_a = build_tnef(rtf_body=b"{\\rtf1\\ansi over-bound body variant A}")
    over_b = build_tnef(rtf_body=b"{\\rtf1\\ansi over-bound body variant B}")

    key_a = parse_email(_tnef_eml(over_a), MAILBOX).dedup_key
    key_b = parse_email(_tnef_eml(over_b), MAILBOX).dedup_key

    assert key_a == key_b
