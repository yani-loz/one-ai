"""Record shapes and synthetic `.eml` builders for the MEM-01 IDEM fixture battery.

Role:
    Defines the frozen record types of the IDEM (idempotence) conformance battery — the
    synthetic RFC 5322 message specification, the per-table expected row delta, the replay
    script step, and the scenario itself — together with the deterministic builders that turn
    a specification into `.eml` bytes. Data and builders only: this module measures nothing
    and imports no product code.
Used by:
    `tools.mem01_verify.fixtures.idem_scenarios_b` (the synthetic originals and the
    expected-delta constructors), `…_c` and `…_d` (the scenario catalogue),
    `tools.mem01_verify.fixtures.idem_scenarios` (the public re-export),
    `tools.mem01_verify.fixtures.digest` (fixture battery digest),
    the IDEM gate evaluator (`tools.mem01_verify.gates.gate_idem`).
Depends on:
    Nothing inside the project. Standard library only (`base64`, `dataclasses`, `typing`,
    `urllib.parse`).
Key invariants:
    - Contract R12: every number and string in a `RowDelta` / `ScenarioExpectation` comes
      from the IDEM criterion, from ruling (c), or from the synthetic ORIGINAL built here —
      never from running the ingest service, the parser or the grant writer.
    - `build_eml_bytes` is a pure function of its `EmlSpec`: same spec in, byte-identical
      `.eml` out, on every platform. Replay steps depend on that byte identity.
    - All addresses live under the reserved test domains `example.test`, `acme.test`,
      `partner.test`. No real person, address, subject or body text ever appears here.
    - Line endings inside generated messages are CRLF, as RFC 5322 requires.
    - A `RowDelta` with both `exact` and `at_least` set to `None` means DELIBERATELY
      UNCONSTRAINED: the IDEM criterion says nothing about that table for that step, and
      the evaluator must not invent a bound. An `exact` value appears only where the
      number comes from the criterion itself (the creation positive control, and the
      zero-delta of every replay-family step); everything else is a floor.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import quote

CRLF: Final = "\r\n"
"""RFC 5322 line separator used by every generated message."""

TRACKED_TABLES: Final[tuple[str, ...]] = (
    "email_message",
    "email_recipient",
    "email_attachment",
    "acl_grant",
    "person",
)
"""The five tables whose row counts an IDEM step compares before and after."""

StepAction = Literal["ingest", "replay", "retry_after_failure", "concurrent_duplicate", "backfill"]
"""The script verbs. `backfill` is stage C (criterion `idem.backfill_one_new_version`)."""


@dataclass(frozen=True)
class AttachmentSpec:
    """One synthetic attachment part of a fixture message.

    Attributes:
        filename: Part filename. Non-ASCII names are emitted as RFC 2231 `filename*`.
        content_type: Declared MIME type of the part (a header property, per contract 4.6).
        payload: The exact bytes of the part before base64 transfer encoding.
        is_inline: True emits `Content-Disposition: inline` plus a `Content-ID`.
        content_id: The `Content-ID` value (without angle brackets); used when inline.
    """

    filename: str
    content_type: str
    payload: bytes
    is_inline: bool = False
    content_id: str | None = None


@dataclass(frozen=True)
class EmlSpec:
    """The synthetic ORIGINAL of one fixture message — the sole source of its expected units.

    Attributes:
        ref: Stable in-scenario handle a step points at (e.g. `"m1"`).
        message_id: RFC 5322 `Message-ID` value, without angle brackets.
        date_header: Verbatim `Date` header value.
        from_addr: Single `From` address.
        to_addrs: `To` addresses, in header order.
        cc_addrs: `Cc` addresses, in header order.
        bcc_addrs: `Bcc` addresses, in header order (present in the stored `.eml` bytes).
        subject: Subject text; non-ASCII is emitted as an RFC 2047 base64 encoded-word.
        body_text: The plain-text body, emitted as base64 UTF-8.
        attachments: Attachment parts, in order. Empty means a single-part message.
        boundary: MIME boundary for multipart messages; fixed so bytes stay reproducible.
    """

    ref: str
    message_id: str
    date_header: str
    from_addr: str
    to_addrs: tuple[str, ...]
    cc_addrs: tuple[str, ...] = ()
    bcc_addrs: tuple[str, ...] = ()
    subject: str = "Test"
    body_text: str = "Test body."
    attachments: tuple[AttachmentSpec, ...] = ()
    boundary: str = "mem01-idem-boundary"

    @property
    def recipient_count(self) -> int:
        """Number of addressed recipients across To, Cc and Bcc, counted from the ORIGINAL."""
        return len(self.to_addrs) + len(self.cc_addrs) + len(self.bcc_addrs)

    @property
    def attachment_count(self) -> int:
        """Number of attachment parts, counted from the ORIGINAL."""
        return len(self.attachments)


@dataclass(frozen=True)
class RowDelta:
    """The expected change in one table's row count across one script step.

    Attributes:
        table: One of `TRACKED_TABLES`.
        exact: The exact expected delta, or `None` when no exact value is asserted.
        at_least: A floor on the delta, or `None` when no floor is asserted.
        origin: Why this bound is what it is — the rule it transcribes.
    """

    table: str
    exact: int | None
    at_least: int | None
    origin: str

    @property
    def unconstrained(self) -> bool:
        """True when the criterion asserts nothing about this table for this step."""
        return self.exact is None and self.at_least is None


@dataclass(frozen=True)
class ScenarioStep:
    """One verb of a replay script, with the row deltas expected after it.

    Attributes:
        step_id: Unique within its scenario, `s1`, `s2`, ….
        action: The verb to perform.
        payload_ref: The `EmlSpec.ref` this step feeds to the ingest path.
        description: What the step does and why, in prose.
        deltas: One `RowDelta` per entry of `TRACKED_TABLES`, in that order.
        failure_injection: For `retry_after_failure`, where the simulated failure fires.
        concurrency: For `concurrent_duplicate`, how many ingests run simultaneously.
        must_remain_unchanged: Column identifiers whose stored values this step may not
            rewrite (ruling (c): inline reuse keeps provenance and version untouched).
        envelope: Delivery-envelope facts the harness must arrange around byte-identical
            payload bytes (mailbox folder, IMAP UID, connection owner), as name→value
            pairs. Empty means "arrange whatever the previous step used".
        result_version_expectation: For steps whose requirement is about published RESULT
            versions rather than row counts (the stage-C backfill), the requirement stated
            in words. Empty for every step whose deltas already carry the whole expectation.
    """

    step_id: str
    action: StepAction
    payload_ref: str
    description: str
    deltas: tuple[RowDelta, ...]
    failure_injection: str | None = None
    concurrency: int = 1
    must_remain_unchanged: tuple[str, ...] = ()
    envelope: tuple[tuple[str, str], ...] = ()
    result_version_expectation: str = ""


@dataclass(frozen=True)
class ScenarioExpectation:
    """The independently specified expectation for a whole scenario.

    Attributes:
        summary: The expectation in one sentence, phrased from the criterion.
        canonical_keys: The `(canonical input, component version, configuration)` keys the
            scenario requires, named symbolically.
        counts_toward_numerator: Whether a conforming system puts this scenario in the
            criterion's numerator. Always False: every fixture here describes conformance.
        committed_results_per_key: How many committed logical results each key must end
            with. One, per criterion 5 ("точно веднъж" = one committed logical result).
    """

    summary: str
    canonical_keys: tuple[str, ...]
    counts_toward_numerator: bool = False
    committed_results_per_key: int = 1


@dataclass(frozen=True)
class IdemScenario:
    """One IDEM conformance scenario: synthetic messages plus a script plus expectations.

    Attributes:
        case_id: Unique `idem-NNN`.
        criterion_id: The criterion this case primarily pins.
        origin: The rule the case exists to pin.
        expected: The independently specified expectation.
        messages: The synthetic originals the steps refer to, by `ref`.
        steps: The script, in order. The first step always creates (positive control).
        also_pins: Further criterion ids whose denominator this case also joins.
        stage_available: Earliest stage at which the case can run (`A` now, `C` backfill).
        prebound_identities: Addresses the harness binds as verified identities BEFORE the
            script runs. Arrangement, not expectation.
        notes: Anything a reader of the fixture needs that the other fields do not carry.
    """

    case_id: str
    criterion_id: str
    origin: str
    expected: ScenarioExpectation
    messages: tuple[EmlSpec, ...]
    steps: tuple[ScenarioStep, ...]
    also_pins: tuple[str, ...] = ()
    stage_available: Literal["A", "B", "C", "D", "E", "F"] = "A"
    prebound_identities: tuple[str, ...] = ()
    notes: str = ""


def _encode_base64_body(raw: bytes) -> str:
    """Base64-encode `raw` and wrap it at 76 characters with CRLF, as RFC 2045 requires."""
    encoded = base64.b64encode(raw).decode("ascii")
    lines = [encoded[index : index + 76] for index in range(0, len(encoded), 76)] or [""]
    return CRLF.join(lines)


def _encode_header_text(value: str) -> str:
    """Return `value` verbatim when ASCII, else as an RFC 2047 base64 encoded-word."""
    if value.isascii():
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?utf-8?B?{encoded}?="


def _render_filename_headers(attachment: AttachmentSpec) -> list[str]:
    """Return the content-type and content-disposition header lines for one attachment."""
    disposition = "inline" if attachment.is_inline else "attachment"
    if attachment.filename.isascii():
        name = f'"{attachment.filename}"'
        return [
            f"Content-Type: {attachment.content_type}; name={name}",
            f"Content-Disposition: {disposition}; filename={name}",
        ]
    percent = quote(attachment.filename, safe="")
    return [
        f"Content-Type: {attachment.content_type}; name*=UTF-8''{percent}",
        f"Content-Disposition: {disposition}; filename*=UTF-8''{percent}",
    ]


def _render_common_headers(spec: EmlSpec) -> list[str]:
    """Return the RFC 5322 headers every fixture message carries, in a fixed order."""
    headers = [
        f"Message-ID: <{spec.message_id}>",
        f"Date: {spec.date_header}",
        f"From: {spec.from_addr}",
        f"To: {', '.join(spec.to_addrs)}",
    ]
    if spec.cc_addrs:
        headers.append(f"Cc: {', '.join(spec.cc_addrs)}")
    if spec.bcc_addrs:
        headers.append(f"Bcc: {', '.join(spec.bcc_addrs)}")
    headers.append(f"Subject: {_encode_header_text(spec.subject)}")
    headers.append("MIME-Version: 1.0")
    return headers


def _render_text_part(body_text: str) -> list[str]:
    """Return the header lines and base64 payload of a UTF-8 text/plain part."""
    return [
        'Content-Type: text/plain; charset="utf-8"',
        "Content-Transfer-Encoding: base64",
        "",
        _encode_base64_body(body_text.encode("utf-8")),
    ]


def _render_attachment_part(attachment: AttachmentSpec) -> list[str]:
    """Return the header lines and base64 payload of one attachment part."""
    lines = _render_filename_headers(attachment)
    if attachment.is_inline and attachment.content_id:
        lines.append(f"Content-ID: <{attachment.content_id}>")
    lines.append("Content-Transfer-Encoding: base64")
    lines.append("")
    lines.append(_encode_base64_body(attachment.payload))
    return lines


def build_eml_bytes(spec: EmlSpec) -> bytes:
    """Build the deterministic RFC 5322 `.eml` bytes of one synthetic fixture message.

    Single-part when the specification carries no attachments, `multipart/mixed` otherwise.
    The body and every attachment travel base64 so that Cyrillic text and binary payloads
    survive byte-for-byte; headers use RFC 2047 encoded-words and RFC 2231 parameters.

    Args:
        spec: The synthetic original to render.

    Returns:
        The complete message as ASCII-armoured bytes with CRLF line endings.

    Edge cases:
        A message with no attachments emits no MIME boundary at all. An inline part without
        a `content_id` simply omits the `Content-ID` header.
    """
    lines = _render_common_headers(spec)
    if not spec.attachments:
        lines.extend(_render_text_part(spec.body_text))
        return (CRLF.join(lines) + CRLF).encode("utf-8")

    lines.append(f'Content-Type: multipart/mixed; boundary="{spec.boundary}"')
    lines.append("")
    lines.append(f"--{spec.boundary}")
    lines.extend(_render_text_part(spec.body_text))
    for attachment in spec.attachments:
        lines.append(f"--{spec.boundary}")
        lines.extend(_render_attachment_part(attachment))
    lines.append(f"--{spec.boundary}--")
    return (CRLF.join(lines) + CRLF).encode("utf-8")


def build_scenario_payloads(scenario: IdemScenario) -> dict[str, bytes]:
    """Render every message of one scenario to `.eml` bytes, keyed by `EmlSpec.ref`.

    Args:
        scenario: The scenario whose messages should be materialised.

    Returns:
        A mapping from message ref to the exact bytes a step feeds to the ingest path.
    """
    return {spec.ref: build_eml_bytes(spec) for spec in scenario.messages}
