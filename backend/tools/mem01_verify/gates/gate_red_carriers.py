"""
Role: The RED gate's degraded-parse carrier (contract §16.17(b)) — it wraps one case's COMPLETE
      text in a multipart nesting deep enough to overflow the real IMAP parser and pushes it
      through `app.connectors.imap.parsing.email_parser.parse_email`, so the `logging` surface of
      the §10.3 matrix is EXERCISED by a real production handoff instead of assumed empty.
Used by: `tools.mem01_verify.gates.gate_red` (`_surface_outputs` calls it inside its
      `_captured_logs` block: the records this handoff emits ARE that surface's output).
Depends on: the standard library (`base64`, `datetime`) and, at call time only, the measured
      component `app.connectors.imap.parsing.email_parser.parse_email` — imported inside the call
      so the parser is resolved through its own module on every call (R12: this module states no
      expectation and reads no product behaviour back).
Key invariants:
  - `CARRIER_NESTING_DEPTH` lies inside the 400-900 range §16.17(b) fixes: 400 overflows
    `parse_email` deterministically at the default recursion limit (measured: 200 parses,
    300 and deeper degrade), and the standard library still parses a carrier below ~900, which
    the seal's own MIME decoder depends on.
  - The case text is the innermost `text/plain` body and appears NOWHERE else in the carrier —
    no preamble and no header carries it — so a decoder that descends to the innermost body reads
    exactly the case text; base64 transfer encoding keeps every scalar intact.
  - The carrier is assembled as bytes by concatenation, never through the stdlib generator:
    flattening a 400-deep message would overflow inside the instrument itself.
  - Nothing here opens a database or a socket, and nothing prints (R5).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

#: Multipart nesting levels of the carrier — inside the 400-900 band of contract §16.17(b).
CARRIER_NESTING_DEPTH = 400
#: The synthetic mailbox the carrier is parsed for; PII-free, under the reserved test domain.
CARRIER_MAILBOX = "carrier@example.test"
#: A fixed aware instant, so the carrier never depends on the wall clock (R6 determinism).
CARRIER_RECEIVED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

_CARRIER_HEADERS = (
    "MIME-Version: 1.0\n"
    "Subject: mem01 red degraded-parse carrier\n"
    f"From: {CARRIER_MAILBOX}\n"
    f"To: {CARRIER_MAILBOX}\n"
)
_INNER_HEADERS = 'Content-Type: text/plain; charset="utf-8"\nContent-Transfer-Encoding: base64\n\n'


def build_degraded_parse_carrier(
    case_id: str, text: str, depth: int = CARRIER_NESTING_DEPTH
) -> bytes:
    """Wrap one case's whole text as the innermost `text/plain` body of a deep multipart nesting.

    Args:
        case_id: The fixture case id; it names the MIME boundaries and nothing else.
        text: The COMPLETE case text — the only place it occurs in the message (§16.17(b)).
        depth: Multipart nesting levels; the §16.17(b) band is 400-900 inclusive.

    Returns:
        The raw RFC822 bytes of the carrier, ready for `parse_email`.
    """
    opens: list[str] = []
    closes: list[str] = []
    for level in range(depth):
        boundary = f"mem01-{case_id}-{level}"
        opens.append(f'Content-Type: multipart/mixed; boundary="{boundary}"\n\n--{boundary}\n')
        closes.append(f"\n--{boundary}--\n")
    body = base64.b64encode(text.encode("utf-8")).decode("ascii")
    message = (
        _CARRIER_HEADERS + "".join(opens) + _INNER_HEADERS + body + "\n" + "".join(reversed(closes))
    )
    return message.encode("utf-8")


def push_through_degraded_parse(case_id: str, text: str) -> None:
    """Run the REAL parser over the carrier so its degraded-parse handoff logs (§16.17(b)).

    The parse is expected to end in the parser's own `parse_status="failed"` branch, which logs
    one WARNING with a traceback; the caller collects that record as the `logging` surface's
    output. The return value is deliberately dropped: what this handoff PRODUCED is measured on
    the surface, never asserted here (R12).
    """
    from app.connectors.imap.parsing.email_parser import parse_email

    parse_email(build_degraded_parse_carrier(case_id, text), CARRIER_MAILBOX, CARRIER_RECEIVED_AT)


__all__ = [
    "CARRIER_MAILBOX",
    "CARRIER_NESTING_DEPTH",
    "CARRIER_RECEIVED_AT",
    "build_degraded_parse_carrier",
    "push_through_degraded_parse",
]
