"""RED battery, part B — record types, the surface matrix, and the hard-negative controls.

Role
    Declares the three frozen record types of the RED (secret-redaction) conformance battery,
    the `RED_SURFACES` matrix naming every production handoff a secret can travel in Stage A,
    and `RED_NEGATIVES` — the protected non-secret control spans that redaction must leave
    byte-identical. Public F evidence: synthetic, PII-free, no real corpus text, addresses only
    under `example.test` / `acme.test` / `partner.test`.

Used by
    `tools.mem01_verify.fixtures.red_cases` (re-exports every public name declared here),
    `tools.mem01_verify.fixtures.digest`, and the RED gate evaluator
    (`tools.mem01_verify.gates.gate_red`).

Depends on
    Standard library only (`collections.abc`, `dataclasses`, `typing`). It imports nothing from
    the product code: an expectation authored here is never produced by running a measured
    component (contract R12) — every value comes from the criterion sheet, the RFC, or the
    synthetic original built in this file.

Key invariants
    - Every record carries `case_id` (unique across the whole RED battery), `criterion_id`
      (one of `red.no_under_redaction` / `red.no_over_redaction`), `origin` (the rule the case
      pins) and `expected` (the independently specified expectation).
    - `RED_NEGATIVES` expectations are `"unchanged"`: the control span must survive redaction
      exactly, in place, together with its surrounding context. Masking one of them FAILS
      `red.no_over_redaction` (criterion 14: "one ordinary invoice number masked -> FAIL").
    - A control travels `TEXT_SURFACE_NAMES` only — the four Stage-A surfaces that carry the case
      text. The `logging` surface echoes no control, so "unchanged" cannot be scored there
      (contract 16.17(b)); positives still travel every Stage-A surface.
    - No control text is shaped like any secret class in `RED_POSITIVES`; no control carries a
      real credential, a real IBAN, a real EIK, or a real phone number.
    - A surface whose `stage_available` is later than "A" expects `"incomplete"`, never PASS
      (contract 10.3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

CRITERION_UNDER_REDACTION = "red.no_under_redaction"
CRITERION_OVER_REDACTION = "red.no_over_redaction"

SecretClass = Literal[
    "aws",
    "openai",
    "google",
    "github",
    "slack",
    "stripe",
    "jwt",
    "pem",
    "keyed_value",
    "connstring",
]
Placement = Literal["start", "beyond_cap", "straddling_cap"]


@dataclass(frozen=True, slots=True)
class RedSurface:
    """One production handoff a secret can travel through, and how a canary reaches it."""

    case_id: str
    criterion_id: str
    origin: str
    name: str
    stage_available: Literal["A", "B", "C", "D", "E", "F"]
    presentation: str
    expected: Literal["no_canary_survives", "incomplete"]


@dataclass(frozen=True, slots=True)
class RedPositive:
    """A synthetic secret-shaped canary that must be fully masked on every declared surface."""

    case_id: str
    criterion_id: str
    origin: str
    secret_class: SecretClass
    canary_text: str
    text_builder: Callable[[], str]
    canary_span: tuple[int, int]
    placement: Placement
    surfaces: tuple[str, ...]
    expected: Literal["fully_redacted"]


@dataclass(frozen=True, slots=True)
class RedNegative:
    """A protected non-secret span that redaction must leave untouched."""

    case_id: str
    criterion_id: str
    origin: str
    control_kind: str
    control_text: str
    text: str
    control_span: tuple[int, int]
    surfaces: tuple[str, ...]
    expected: Literal["unchanged"]


RED_SURFACES: tuple[RedSurface, ...] = (
    RedSurface(
        case_id="red-001",
        criterion_id=CRITERION_UNDER_REDACTION,
        origin="contract 10.3 surface matrix: the parser's body path is the first handoff.",
        name="email_body",
        stage_available="A",
        presentation="body text of a synthetic .eml message carrying the canary inline",
        expected="no_canary_survives",
    ),
    RedSurface(
        case_id="red-002",
        criterion_id=CRITERION_UNDER_REDACTION,
        origin="contract 10.3: the extractor's text path stores attachment text for readers.",
        name="attachment_text",
        stage_available="A",
        presentation="extracted text of a synthetic attachment carrying the canary inline",
        expected="no_canary_survives",
    ),
    RedSurface(
        case_id="red-003",
        criterion_id=CRITERION_UNDER_REDACTION,
        origin=(
            "criterion 14 names structured attachment payloads explicitly; contract 11 records "
            "this surface as unmasked today, so the expectation below is the RIGHT answer and "
            "the case is meant to FAIL until that is fixed."
        ),
        name="attachment_structured_payload",
        stage_available="A",
        presentation="one cell value inside the structured `extracted_data` payload",
        expected="no_canary_survives",
    ),
    RedSurface(
        case_id="red-004",
        criterion_id=CRITERION_UNDER_REDACTION,
        origin="contract 10.3: extraction detail strings are stored and reach readers.",
        name="extraction_detail",
        stage_available="A",
        presentation="the extraction detail / failure string recorded for the attachment",
        expected="no_canary_survives",
    ),
    RedSurface(
        case_id="red-005",
        criterion_id=CRITERION_UNDER_REDACTION,
        origin=(
            "criterion 14 names generated embedding headers; the embedding path does not exist "
            "before stage D, so this surface is recorded `incomplete`, never PASS."
        ),
        name="embedding_header",
        stage_available="D",
        presentation="the generated metadata header prefixed to the embedding input",
        expected="incomplete",
    ),
    RedSurface(
        case_id="red-006",
        criterion_id=CRITERION_UNDER_REDACTION,
        origin="contract 10.3: the ingest log output for a message carrying a canary.",
        name="logging",
        stage_available="A",
        presentation="an ingest log line whose context echoes the message or attachment text",
        expected="no_canary_survives",
    ),
)

STAGE_A_SURFACE_NAMES: tuple[str, ...] = tuple(
    surface.name for surface in RED_SURFACES if surface.stage_available == "A"
)

#: The Stage-A surface that carries no case text of its own: it is the ingest LOG output, reached
#: through the parser's degraded-parse handoff, and a log line never echoes a hard-negative
#: control — so "unchanged" is meaningless there (contract §16.17(b)).
LOGGING_SURFACE_NAME = "logging"

#: The Stage-A surfaces that actually CARRY the case text, and the only ones a hard-negative
#: control travels (§16.17(b)): body, extracted attachment text, structured payload, detail.
TEXT_SURFACE_NAMES: tuple[str, ...] = tuple(
    name for name in STAGE_A_SURFACE_NAMES if name != LOGGING_SURFACE_NAME
)


@dataclass(frozen=True, slots=True)
class _NegativeGroup:
    """A family of protected control spans sharing one origin and two bilingual contexts."""

    kind: str
    origin: str
    contexts: tuple[tuple[str, str], ...]
    controls: tuple[str, ...]


_NEGATIVE_GROUPS: tuple[_NegativeGroup, ...] = (
    _NegativeGroup(
        kind="invoice_number",
        origin="criterion 14 worked example: masking one ordinary invoice number is a FAIL.",
        contexts=(
            ("Приложено е копие на фактура ", " от нашия доставчик."),
            ("Attached is a copy of invoice ", " from our supplier."),
        ),
        controls=(
            "INV-2026-000123",
            "INV-2026-000124",
            "INV-2026-000125",
            "INV-2025-009001",
            "INV-2025-009002",
            "ФТ-2026-000441",
            "ФТ-2026-000442",
            "INV/2026/07/0031",
            "INV-2024-000001",
            "INV-2027-100000",
        ),
    ),
    _NegativeGroup(
        kind="iban_shaped",
        origin="contract 10.3 hard negatives: IBAN-shaped strings are business data, not secrets.",
        contexts=(
            ("Преводът е по сметка ", " на дружеството."),
            ("The transfer goes to account ", " of the company."),
        ),
        controls=(
            "BG18EXMP99990000000001",
            "BG18EXMP99990000000002",
            "BG18EXMP99990000000003",
            "BG42EXMP88880000000004",
            "BG42EXMP88880000000005",
            "DE02EXMP0000000000006",
            "DE02EXMP0000000000007",
            "RO49EXMP0000000000008",
            "GR16EXMP0000000000009",
            "BG18EXMP99990000000010",
        ),
    ),
    _NegativeGroup(
        kind="eik_shaped",
        origin="contract 10.3 hard negatives: EIK/BULSTAT 9- and 13-digit ids must survive.",
        contexts=(
            ("Контрагентът е с ЕИК ", ", вписан в Търговския регистър."),
            ("The counterparty registration number is ", " in the commercial registry."),
        ),
        controls=(
            "200000001",
            "200000002",
            "200000003",
            "200000004",
            "200000005",
            "2000000010001",
            "2000000010002",
            "2000000010003",
            "2000000010004",
            "2000000010005",
        ),
    ),
    _NegativeGroup(
        kind="phone_shaped",
        origin="contract 10.3 hard negatives: phone-shaped digit runs are contact data.",
        contexts=(
            ("Може да се свържете с нас на ", " в работно време."),
            ("You can reach the desk on ", " during office hours."),
        ),
        controls=(
            "+359 2 000 0001",
            "+359 2 000 0002",
            "+359 888 000 001",
            "+359 888 000 002",
            "+49 30 0000 0011",
            "+49 30 0000 0012",
            "+44 20 0000 0013",
            "02 000 0014",
            "0888 000 015",
            "00359 2 000 0016",
        ),
    ),
    _NegativeGroup(
        kind="date_literal",
        origin="contract 10.3 hard negatives: dates in every written form must survive.",
        contexts=(
            ("Срокът за отговор е ", " съгласно договора."),
            ("The response deadline is ", " per the agreement."),
        ),
        controls=(
            "2026-09-06",
            "06.09.2026",
            "6 септември 2026 г.",
            "Mon, 06 Sep 2026 12:00:00 +0300",
            "2026-09-06T12:00:00Z",
            "09/06/2026",
            "01.01.2027 - 31.12.2027",
            "Q3 2026",
            "30 юни 2027 г.",
            "2027-01-01",
        ),
    ),
    _NegativeGroup(
        kind="url_without_credentials",
        origin="contract 10.3 hard negatives: a URL without credentials carries no secret.",
        contexts=(
            ("Отчетът е достъпен на ", " след вход в портала."),
            ("The report is available at ", " after signing in."),
        ),
        controls=(
            "https://portal.example.test/reports/2026/q3?ref=summary",
            "https://portal.example.test/reports/2026/q4?ref=summary",
            "https://docs.acme.test/handbook/v12/section-4",
            "https://docs.acme.test/handbook/v12/section-5",
            "https://partner.test/api/v2/orders?page=3&size=50",
            "https://partner.test/api/v2/orders?page=4&size=50",
            "http://intranet.example.test:8080/dashboards/finance",
            "https://example.test/downloads/handbook-2026-09.pdf",
            "https://acme.test/status",
            "https://partner.test/tenders/2026/notice-000441",
        ),
    ),
    _NegativeGroup(
        kind="sha256_hex",
        origin="contract 10.3 hard negatives: ordinary content hashes are not credentials.",
        contexts=(
            ("Контролната сума на файла е ", " и съвпада с манифеста."),
            ("The file checksum is ", " and it matches the manifest."),
        ),
        controls=tuple(f"{'0123456789abcdef' * 3}{index:016x}" for index in range(1, 11)),
    ),
    _NegativeGroup(
        kind="bulgarian_prose_with_digits",
        origin=(
            "contract 10.3 hard negatives: Bulgarian prose carrying digits must survive whole; "
            "an entropy heuristic that eats it fails red.no_over_redaction."
        ),
        contexts=(
            ("В доклада пише: „", "“ — това е целият цитат."),
            ("The report states: „", "“ — that is the whole quotation."),
        ),
        controls=(
            "Доставката съдържа 12 палета по 480 кг, общо 5760 кг.",
            "Складът е 1200 кв. м, от които 340 кв. м са хладилни.",
            "Планираме 3 смени по 8 часа, считано от 15 октомври.",
            "Отклонението е 4,7 % спрямо бюджета от 128 500 лв.",
            "Изпратени са 27 оферти, приети са 19, отказани са 8.",
            "Договорът е за 24 месеца с опция за още 12 месеца.",
            "Приходите за периода са 1 240 000 лв. без ДДС.",
            "Екипът нарасна от 14 на 21 души за 6 месеца.",
            "Гаранционният срок е 60 месеца при 2 профилактики годишно.",
            "Транспортът е 2 курса седмично по 640 км.",
        ),
    ),
    _NegativeGroup(
        kind="order_id",
        origin="contract 10.3 hard negatives: order identifiers are operational business data.",
        contexts=(
            ("Поръчка ", " е потвърдена от клиента."),
            ("Order ", " has been confirmed by the customer."),
        ),
        controls=(
            "ORD-2026-000441",
            "ORD-2026-000442",
            "ORD-2026-000443",
            "ORD-2025-100081",
            "ORD-2025-100082",
            "SO-2026-0007731",
            "SO-2026-0007732",
            "PO-2026-000915",
            "PO-2026-000916",
            "ORD/2026/BG/0044",
        ),
    ),
    _NegativeGroup(
        kind="tracking_number",
        origin="contract 10.3 hard negatives: courier tracking codes must survive untouched.",
        contexts=(
            ("Пратката е с товарителница ", " и се очаква утре."),
            ("The shipment tracking code is ", " and it arrives tomorrow."),
        ),
        controls=(
            "SPDBG000000001234",
            "SPDBG000000001235",
            "SPDBG000000001236",
            "1ZEXAMPLE0000000001",
            "1ZEXAMPLE0000000002",
            "EXBG0000000001EU",
            "EXBG0000000002EU",
            "DHLEXAMPLE00000001",
            "DHLEXAMPLE00000002",
            "TNTEXAMPLE00000003",
        ),
    ),
    _NegativeGroup(
        kind="misc_business_identifier",
        origin=(
            "contract 10.3 hard negatives: assorted dense identifiers (VAT ids, short git shas, "
            "colour codes, amounts) that a naive entropy rule would mistake for secrets."
        ),
        contexts=(
            ("Референцията в системата е ", " — моля, цитирайте я."),
            ("The system reference is ", " — please quote it."),
        ),
        controls=(
            "BG200000001",
            "BG200000002",
            "a1b2c3d",
            "9f8e7d6",
            "#0d9488",
            "#8b5cf6",
            "12 480,50 EUR",
            "128 500,00 BGN",
            "ISBN 978-0-0000-0000-1",
            "CA 0000 AB",
        ),
    ),
)


def _build_negative_case(case_number: int, group: _NegativeGroup, index: int) -> RedNegative:
    """Embed one control span in a bilingual context and record its exact scalar span."""
    lead, tail = group.contexts[index % len(group.contexts)]
    control = group.controls[index]
    return RedNegative(
        case_id=f"red-{case_number:03d}",
        criterion_id=CRITERION_OVER_REDACTION,
        origin=group.origin,
        control_kind=group.kind,
        control_text=control,
        text=f"{lead}{control}{tail}",
        control_span=(len(lead), len(lead) + len(control)),
        surfaces=TEXT_SURFACE_NAMES,
        expected="unchanged",
    )


def _build_negative_battery() -> tuple[RedNegative, ...]:
    """Assemble the hard-negative battery with contiguous case ids starting at `red-301`."""
    records: list[RedNegative] = []
    case_number = 301
    for group in _NEGATIVE_GROUPS:
        for index in range(len(group.controls)):
            records.append(_build_negative_case(case_number, group, index))
            case_number += 1
    return tuple(records)


RED_NEGATIVES: tuple[RedNegative, ...] = _build_negative_battery()

__all__ = [
    "CRITERION_OVER_REDACTION",
    "CRITERION_UNDER_REDACTION",
    "LOGGING_SURFACE_NAME",
    "RED_NEGATIVES",
    "RED_SURFACES",
    "STAGE_A_SURFACE_NAMES",
    "TEXT_SURFACE_NAMES",
    "Placement",
    "RedNegative",
    "RedPositive",
    "RedSurface",
    "SecretClass",
]
