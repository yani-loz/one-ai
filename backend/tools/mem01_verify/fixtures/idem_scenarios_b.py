"""Synthetic ORIGINALS and expected-delta helpers for the MEM-01 IDEM fixture battery.

Role:
    Holds the fifteen synthetic RFC 5322 message specifications the IDEM scenarios replay
    (`M001` … `M013`, with `M004A`/`M004B` and `M011A`/`M011B` where a scenario needs two),
    the criterion ids their expectations are transcribed from, and the small constructors
    that turn one original into the row-delta tuple a step expects. Data and pure
    constructors only — nothing here touches a database or a measured component.
Used by:
    `tools.mem01_verify.fixtures.idem_scenarios_c` and `…_d` (the scenario catalogue),
    through them `tools.mem01_verify.fixtures.idem_scenarios` (the public re-export).
Depends on:
    `tools.mem01_verify.fixtures.idem_scenarios_a` for the record types. Nothing else
    inside the project.
Key invariants:
    - Contract R12: the numbers `creation_deltas` produces come from the ORIGINAL defined
      here (`recipient_count`, `attachment_count`) and from criterion 5's positive
      control — never from running the parser, the ingest service or the grant writer.
    - `zero_deltas` is the transcription of ruling (c): a repeat ingest under unchanged
      versions and configuration adds 0 rows to content, grant AND carrier tables.
    - `acl_grant` and `person` stay UNCONSTRAINED on creation steps: how many principals
      and grants one message materialises is a property of the measured components, so
      R12 forbids an expected value there. Their zero-delta on replay steps IS asserted,
      because that number comes from the criterion.
    - Addresses only under `example.test`, `acme.test`, `partner.test`; subjects and
      bodies are invented, bilingual where the case is about Cyrillic, and carry no
      personal data.
"""

from __future__ import annotations

from typing import Final

from tools.mem01_verify.fixtures.idem_scenarios_a import (
    TRACKED_TABLES,
    AttachmentSpec,
    EmlSpec,
    RowDelta,
    ScenarioStep,
)

REPLAY_CRITERION: Final = "idem.replay_no_change"
EXACTLY_ONCE_CRITERION: Final = "idem.exactly_once_committed"
BACKFILL_CRITERION: Final = "idem.backfill_one_new_version"

RULING_C_ORIGIN: Final = (
    "ruling (c): a repeat ingest under the same versions and configuration yields 0 new "
    "logical content/grant/carrier/chunk/result rows"
)
_POSITIVE_CONTROL: Final = (
    "criterion idem.replay_no_change: the initial creation is the positive control and must "
    "create; a no-op first ingest fails the control"
)
_ORIGINAL: Final = "counted from the synthetic ORIGINAL message defined in this module"
_DERIVATION_DEPENDENT: Final = (
    "unconstrained on creation: the number of principals one message materialises is a "
    "property of the measured entity resolver, so R12 forbids an expected value here"
)
_GRANT_FLOOR: Final = (
    "floor only, from PF-01-AC10 (a message resolved to a verified disclosed recipient or to "
    "the connection owner mints at least one per-message grant): a creation that mints ZERO "
    "grants is a deny-all, and a deny-all is never a pass. The EXACT count is deliberately "
    "not asserted — that number is a property of the measured grant writer (R12)"
)

_BODY_BILINGUAL: Final = (
    "Здравей, Анна,\n\nПрилагам обобщението за седмицата.\n\nПоздрави,\nБорис\n\n"
    "---\n\nHello Anna,\n\nAttaching the weekly summary.\n\nRegards,\nBoris\n"
)
_BODY_BULGARIAN: Final = (
    "Уважаеми колеги,\n\nПотвърждавам получаването на документите по проекта.\n"
    "Ще изпратя коментарите си до петък.\n\nС уважение,\nДимитър Георгиев\n"
)
_BODY_ENGLISH: Final = (
    "Dear colleagues,\n\nConfirming receipt of the project documents.\n"
    "Comments will follow by Friday.\n\nBest regards,\nClara Smith\n"
)

_PDF_PAYLOAD: Final = b"%PDF-1.4\n% synthetic MEM-01 IDEM fixture, not a real document\n%%EOF\n"
_CSV_PAYLOAD: Final = "период;сума\n2026-Q1;1200\n2026-Q2;1350\n".encode()
_TXT_PAYLOAD: Final = b"Synthetic note for the MEM-01 IDEM battery. No personal data.\n"
_PNG_PAYLOAD: Final = b"\x89PNG\r\n\x1a\n synthetic-inline-image-placeholder"


def zero_deltas(reason: str) -> tuple[RowDelta, ...]:
    """Return an exact zero delta on every tracked table, carrying `reason` as its origin."""
    return tuple(
        RowDelta(table=table, exact=0, at_least=None, origin=reason) for table in TRACKED_TABLES
    )


def creation_deltas(
    spec: EmlSpec, *, attachments_at_least: int | None = None
) -> tuple[RowDelta, ...]:
    """Return the delta of a step that creates exactly one logical result for `spec`.

    Only `email_message` carries an EXACT value here, because only that number comes from
    the criterion ("the initial creation is the positive control and must create", one
    logical result per canonical key). Recipient and carrier rows carry FLOORS read off the
    synthetic ORIGINAL: a message that materialises fewer rows than it has addressed
    recipients or attachment parts has silently dropped content, while an exact value would
    encode assumptions about the measured components that R12 forbids — the `email_recipient`
    kind constraint admits `sender` and `reply_to` edges the ORIGINAL's To/Cc/Bcc count does
    not cover, and whether an inline part earns a carrier row is a scope-policy question.

    Every creation step in this battery ingests a message whose `To`/`Cc` addresses include
    at least one identity the harness pre-bound as verified, so the `acl_grant` floor of one
    is reachable without asserting how many grants the writer derives.

    Args:
        spec: The synthetic original being ingested; recipient and attachment counts are
            read off it, never off a parser.
        attachments_at_least: Override for the `email_attachment` floor. Defaults to the
            number of attachment parts in the original; pass a smaller value when the
            original carries an inline part the frozen scope policy may legitimately omit.

    Returns:
        One `RowDelta` per entry of `TRACKED_TABLES`, in that order.
    """
    attachment_floor = (
        spec.attachment_count if attachments_at_least is None else attachments_at_least
    )
    return (
        RowDelta(table="email_message", exact=1, at_least=None, origin=_POSITIVE_CONTROL),
        RowDelta(
            table="email_recipient",
            exact=None,
            at_least=spec.recipient_count,
            origin=(
                f"floor of the addressed recipients {_ORIGINAL}; the exact row count is not "
                "asserted because the schema's kind constraint admits sender/reply_to edges "
                "the ORIGINAL does not enumerate (R12)"
            ),
        ),
        RowDelta(
            table="email_attachment",
            exact=None,
            at_least=attachment_floor,
            origin=(
                f"floor of the attachment parts {_ORIGINAL}; dropping a part is a defect, "
                "while an exact count would encode a scope-policy assumption (R12)"
            ),
        ),
        RowDelta(table="acl_grant", exact=None, at_least=1, origin=_GRANT_FLOOR),
        RowDelta(table="person", exact=None, at_least=None, origin=_DERIVATION_DEPENDENT),
    )


def ingest_step(
    spec: EmlSpec, *, step_id: str = "s1", attachments_at_least: int | None = None
) -> ScenarioStep:
    """Build the creating first step for `spec` (the positive control of criterion 5)."""
    return ScenarioStep(
        step_id=step_id,
        action="ingest",
        payload_ref=spec.ref,
        description=f"First ingest of {spec.ref}; must create exactly one logical result.",
        deltas=creation_deltas(spec, attachments_at_least=attachments_at_least),
    )


def replay_step(spec: EmlSpec, step_id: str, description: str) -> ScenarioStep:
    """Build a byte-identical replay step whose expected delta is zero everywhere."""
    return ScenarioStep(
        step_id=step_id,
        action="replay",
        payload_ref=spec.ref,
        description=description,
        deltas=zero_deltas(RULING_C_ORIGIN),
    )


def synthetic_message(
    ref: str,
    message_id: str,
    *,
    to_addrs: tuple[str, ...],
    subject: str,
    body_text: str,
    cc_addrs: tuple[str, ...] = (),
    bcc_addrs: tuple[str, ...] = (),
    attachments: tuple[AttachmentSpec, ...] = (),
    from_addr: str = "boris.petrov@acme.test",
    date_header: str = "Tue, 03 Mar 2026 09:15:00 +0200",
) -> EmlSpec:
    """Build one synthetic original with the battery's shared defaults."""
    return EmlSpec(
        ref=ref,
        message_id=message_id,
        date_header=date_header,
        from_addr=from_addr,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        bcc_addrs=bcc_addrs,
        subject=subject,
        body_text=body_text,
        attachments=attachments,
    )


ANNA: Final = "anna.ivanova@acme.test"
BORIS: Final = "boris.petrov@acme.test"
CLARA: Final = "clara.smith@partner.test"
DIMITAR: Final = "dimitar.georgiev@example.test"
ELENA: Final = "elena.koleva@acme.test"

M001: Final = synthetic_message(
    "m1",
    "idem-001-a@acme.test",
    to_addrs=(ANNA,),
    subject="Weekly summary / Седмично обобщение",
    body_text=_BODY_BILINGUAL,
)
M002: Final = synthetic_message(
    "m1",
    "idem-002-a@acme.test",
    to_addrs=(ANNA, CLARA),
    subject="Q1 report",
    body_text=_BODY_ENGLISH,
    attachments=(
        AttachmentSpec("report.pdf", "application/pdf", _PDF_PAYLOAD),
        AttachmentSpec("figures.csv", "text/csv", _CSV_PAYLOAD),
    ),
)
M003: Final = synthetic_message(
    "m1",
    "idem-003-a@acme.test",
    to_addrs=(DIMITAR,),
    subject="Retry probe",
    body_text=_BODY_ENGLISH,
    attachments=(AttachmentSpec("notes.txt", "text/plain", _TXT_PAYLOAD),),
)
M004A: Final = synthetic_message(
    "m1",
    "idem-004-a@acme.test",
    to_addrs=(ANNA,),
    subject="Baseline message",
    body_text=_BODY_ENGLISH,
)
M004B: Final = synthetic_message(
    "m2",
    "idem-004-b@acme.test",
    to_addrs=(ANNA, BORIS),
    subject="Concurrent first ingest",
    body_text=_BODY_BILINGUAL,
    from_addr=CLARA,
)
M005: Final = synthetic_message(
    "m1",
    "idem-005-a@acme.test",
    to_addrs=(CLARA,),
    subject="Concurrent replay probe",
    body_text=_BODY_ENGLISH,
)
M006: Final = synthetic_message(
    "m1",
    "idem-006-a@acme.test",
    to_addrs=(ELENA,),
    subject="Отчет за проекта — март",
    body_text=_BODY_BULGARIAN,
    attachments=(AttachmentSpec("отчет-март.pdf", "application/pdf", _PDF_PAYLOAD),),
)
M007: Final = synthetic_message(
    "m1",
    "idem-007-a@acme.test",
    to_addrs=(ANNA,),
    cc_addrs=(ELENA,),
    bcc_addrs=(CLARA,),
    subject="Blind copy probe / Скрито копие",
    body_text=_BODY_BILINGUAL,
)
M008: Final = synthetic_message(
    "m1",
    "idem-008-a@acme.test",
    to_addrs=(DIMITAR, ELENA),
    subject="Envelope variation probe",
    body_text=_BODY_BILINGUAL,
)
M009: Final = synthetic_message(
    "m1",
    "idem-009-a@acme.test",
    to_addrs=(ANNA,),
    subject="Inline reuse probe",
    body_text=_BODY_ENGLISH,
    attachments=(AttachmentSpec("contract.pdf", "application/pdf", _PDF_PAYLOAD),),
)
M010: Final = synthetic_message(
    "m1",
    "idem-010-a@acme.test",
    to_addrs=(ANNA, DIMITAR),
    subject="Triple replay probe",
    body_text=_BODY_BULGARIAN,
)
M011A: Final = synthetic_message(
    "m1",
    "idem-011-a@acme.test",
    to_addrs=(ELENA,),
    subject="Atomicity baseline",
    body_text=_BODY_ENGLISH,
)
M011B: Final = synthetic_message(
    "m2",
    "idem-011-b@acme.test",
    to_addrs=(ANNA, CLARA),
    cc_addrs=(ELENA,),
    subject="Atomicity probe / Атомарност",
    body_text=_BODY_BILINGUAL,
    attachments=(AttachmentSpec("annex.csv", "text/csv", _CSV_PAYLOAD),),
)
M012: Final = synthetic_message(
    "m1",
    "idem-012-a@acme.test",
    to_addrs=(ANNA, BORIS, CLARA, DIMITAR, ELENA),
    cc_addrs=("finance@acme.test", "legal@acme.test", "pmo@partner.test"),
    bcc_addrs=("archive@example.test", "audit@example.test"),
    subject="Fan-out probe / Много получатели",
    body_text=_BODY_BILINGUAL,
    attachments=(
        AttachmentSpec("deck.pdf", "application/pdf", _PDF_PAYLOAD),
        AttachmentSpec("данни.csv", "text/csv", _CSV_PAYLOAD),
        AttachmentSpec(
            "logo.png", "image/png", _PNG_PAYLOAD, is_inline=True, content_id="logo-001@acme.test"
        ),
    ),
)
M013: Final = synthetic_message(
    "m1",
    "idem-013-a@acme.test",
    to_addrs=(ANNA,),
    subject="Backfill probe",
    body_text=_BODY_ENGLISH,
    attachments=(AttachmentSpec("spec.pdf", "application/pdf", _PDF_PAYLOAD),),
)
