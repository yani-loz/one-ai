"""
Role: Seals fix-registry row A28 / contract §16.17(b) on the RED gate's degraded-parse carrier —
      every positive canary is pushed through the REAL `parse_email`: the innermost `text/plain`
      body of the carrier, decoded from whatever transfer encoding the carrier chose, is the
      COMPLETE case text; the parse returns `parse_status == "failed"`; and at least one record
      the real parser itself emitted during that call appears verbatim in `outputs["logging"]`.
      The collector, the surface matrix and the vacuity guard are sealed by
      `test_review_round_5_red.py`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.gates.gate_red and .fixtures.red_cases (imported through the
      `instrument` loader); app.connectors.imap.parsing.email_parser (spied on, never asserted
      against); pytest monkeypatch.
Key invariants:
  - The spy wraps the real `parse_email` ONLY where a name still references the original
    function (the source module always; `gate_red` and any imported `tools.mem01_verify` module
    only when bound to it), so a gate adapter that never calls the parser is observed, never
    replaced.
  - The parser's own records are captured by a handler attached to the parser's logger INSIDE
    the wrapped call, so a carrier that detaches descendant handlers on entry cannot hide them.
  - The case text is bound ONLY by equality with the decoded innermost `text/plain` body
    (inline, quoted-printable or base64 alike); there is no whole-message substring fallback,
    so the case text sitting in a preamble with only the marker in the body cannot bind.
    Contract §16.17(b) bounds the carrier's nesting depth to 400-900 inclusive, the range the
    standard library parses (measured: 900 parses, 1000 does not).
  - No raw bytes, decoded body or captured record is ever placed in an assertion message.
"""

from __future__ import annotations

import email
import importlib
import logging
import sys
from dataclasses import dataclass, field

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader

PARSER_MODULE = "app.connectors.imap.parsing.email_parser"
INSTRUMENT_PREFIX = "tools.mem01_verify"
LOGGING_SURFACE = "logging"
TEXT_PLAIN = "text/plain"


@dataclass
class _ParserCall:
    raw_bytes: bytes
    result: object
    messages: list[str] = field(default_factory=list)


class _RecordingHandler(logging.Handler):
    """Collects the message text of every record the parser's logger emits."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__(level=logging.DEBUG)
        self._messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self._messages.append(record.getMessage())


def _spy_on_parse_email(
    monkeypatch: pytest.MonkeyPatch, instrument: InstrumentLoader
) -> list[_ParserCall]:
    """Wrap the real `parse_email` where it is still bound; record bytes, result and records."""
    parser_module = importlib.import_module(PARSER_MODULE)
    original = parser_module.parse_email
    parser_logger = logging.getLogger(PARSER_MODULE)
    calls: list[_ParserCall] = []

    def spy(raw_bytes: bytes, *args: object, **kwargs: object) -> object:
        messages: list[str] = []
        handler = _RecordingHandler(messages)
        parser_logger.addHandler(handler)
        try:
            result = original(raw_bytes, *args, **kwargs)
        finally:
            parser_logger.removeHandler(handler)
        calls.append(_ParserCall(bytes(raw_bytes), result, messages))
        return result

    monkeypatch.setattr(parser_module, "parse_email", spy)
    instrument_modules = [
        module for name, module in list(sys.modules.items()) if name.startswith(INSTRUMENT_PREFIX)
    ]
    for module in [instrument("gates.gate_red"), *instrument_modules]:
        if getattr(module, "parse_email", None) is original:
            monkeypatch.setattr(module, "parse_email", spy)
    return calls


def _normalised(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def _innermost_text(raw_bytes: bytes) -> str | None:
    """The decoded innermost `text/plain` body, descending iteratively; None when unparsable.

    Contract §16.17(b) bounds the carrier's nesting depth to 400-900 INCLUSIVE, which is the
    range `email.message_from_bytes` parses at the default recursion limit (measured: 900
    parses, 1000 does not); a carrier nested beyond the bound is out of contract, decodes to
    None here, and the seal fails it.
    """
    try:
        part = email.message_from_bytes(raw_bytes)
    except RecursionError:  # beyond the §16.17(b) bound: out of contract
        return None
    while part.is_multipart():
        parts = part.get_payload()
        if not parts:
            return None
        part = next(
            (p for p in parts if p.is_multipart() or p.get_content_type() == TEXT_PLAIN),
            parts[0],
        )
    if part.get_content_type() != TEXT_PLAIN:
        return None
    payload = part.get_payload(decode=True)
    if payload is None:
        return None
    try:
        return payload.decode(part.get_content_charset() or "utf-8")
    except UnicodeDecodeError:
        return None


def _carries_case_text(raw_bytes: bytes, text: str) -> bool:
    """True when the message's decoded innermost `text/plain` body IS the whole case text."""
    decoded = _innermost_text(raw_bytes)
    return decoded is not None and _normalised(decoded) == _normalised(text)


def _pick(positives: tuple, pick: str) -> object:
    if pick == "first":
        return positives[0]
    if pick == "last":
        return positives[-1]
    return next(canary for canary in positives if canary.placement == "beyond_cap")


@pytest.mark.parametrize("pick", ["first", "beyond_cap", "last"])
def test_the_degraded_parse_carrier_runs_the_real_parser_on_the_case_text(
    instrument: InstrumentLoader, monkeypatch: pytest.MonkeyPatch, pick: str
) -> None:
    gate_red = instrument("gates.gate_red")
    canary = _pick(instrument("fixtures.red_cases").RED_POSITIVES, pick)
    text = canary.text_builder()
    calls = _spy_on_parse_email(monkeypatch, instrument)

    outputs = gate_red._surface_outputs(canary.case_id, text, canary.canary_text)

    carrying = [call for call in calls if _carries_case_text(call.raw_bytes, text)]
    assert carrying, "parse_email never saw the whole case text as the innermost body"
    assert any(getattr(call.result, "parse_status", None) == "failed" for call in carrying), (
        "the carrier did not degrade the parse"
    )
    emitted = [message for call in carrying for message in call.messages]
    assert emitted, "the real parser emitted no record during the carrier call"
    logging_output = outputs[LOGGING_SURFACE]
    assert isinstance(logging_output, str) and logging_output, "the logging output is empty"
    assert any(message in logging_output for message in emitted), (
        "no record the real parser emitted appears verbatim in the logging output"
    )
