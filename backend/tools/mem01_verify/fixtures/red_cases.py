"""RED battery — public F evidence for secret redaction (criterion 14, gate RED).

Role
    Declares the positive canary battery (`RED_POSITIVES`): twelve synthetic, recognizably
    fake but class-shaped secrets for each of the ten secret classes, each placed at a declared
    scalar offset — inside the first kilobyte, beyond the 2,000,000-scalar scan cap, or
    straddling that boundary — and expected to be FULLY redacted on every Stage-A surface.
    Re-exports the record types, the surface matrix (`RED_SURFACES`) and the hard-negative
    controls (`RED_NEGATIVES`) from `red_cases_b`, so `fixtures.red_cases` is the single public
    module named by the Stage-A contract section 1.3.

Used by
    `tools.mem01_verify.gates.gate_red` (the evaluator that scores these cases — a different
    author; builder != labeler), `tools.mem01_verify.fixtures.digest` (the fixture battery
    digest that enters `config_hash`), and release packaging.

Depends on
    `tools.mem01_verify.fixtures.red_cases_b` (record types, surfaces, negatives) and the
    standard library only. It imports NOTHING from `app.*`: no expectation here was produced
    by running `redact_secrets` or any other measured component (contract R12).

Key invariants
    - Every record carries `case_id` (unique across the RED battery: surfaces `red-001`..
      `red-006`, positives `red-101`..`red-220`, negatives `red-301`..`red-410`),
      `criterion_id`, `origin` and `expected`.
    - `expected` on every positive is `"fully_redacted"`: after redaction the canary text must
      not survive in whole OR in part on ANY surface it was passed through. A typed placeholder
      substituted for it is an approved transformation (criterion 14); leaving any part of the
      canary is under-redaction.
    - `canary_span` is the exact `(start, end)` scalar interval of the canary inside
      `text_builder()`, computed from the synthetic original built here — never measured.
    - `text_builder` is a zero-argument callable that materializes the filler lazily; no
      multi-megabyte literal is stored in this module.
    - The expectation for `beyond_cap` and `straddling_cap` cases is the RIGHT answer, not
      today's behaviour: contract section 11 records that canaries past the 2,000,000-scalar
      scan cap currently survive, so these cases are meant to FAIL until the redactor is fixed.
"""

from __future__ import annotations

from collections.abc import Callable

from tools.mem01_verify.fixtures.red_cases_b import (
    CRITERION_OVER_REDACTION,
    CRITERION_UNDER_REDACTION,
    RED_NEGATIVES,
    RED_SURFACES,
    STAGE_A_SURFACE_NAMES,
    Placement,
    RedNegative,
    RedPositive,
    RedSurface,
    SecretClass,
)

SCAN_CAP_SCALARS = 2_000_000
BEYOND_CAP_MARGIN_SCALARS = 250

_FILLER_UNIT = (
    "Това е синтетичен запълващ текст без тайни и без идентификатори. "
    "This is synthetic filler prose that carries no secret and no identifier. "
)

_POSITIVE_CONTEXTS: tuple[tuple[str, str], ...] = (
    ("Здравейте, ето стойността за тестовата среда: ", " — моля, ротирайте я веднага."),
    ("Hello team, the staging configuration value is: ", " — please rotate it right away."),
)

_PLACEMENT_PLAN: tuple[Placement, ...] = (
    "start",
    "start",
    "start",
    "start",
    "start",
    "start",
    "start",
    "start",
    "beyond_cap",
    "beyond_cap",
    "straddling_cap",
    "straddling_cap",
)

_PLACEMENT_ORIGIN: dict[Placement, str] = {
    "start": (
        "the canary sits in the first kilobyte, inside any plausible scan window: a survivor "
        "here is a plain detector gap"
    ),
    "beyond_cap": (
        "the canary starts after scalar 2,000,000 — criterion 14 is explicit that a capped scan "
        "is NOT an unscanned tail (redact.py:269), so the tail must be scanned too"
    ),
    "straddling_cap": (
        "the canary spans scalar 2,000,000 — a scan that stops at the cap sees two halves and "
        "matches neither, which is exactly the boundary defect criterion 14 names"
    ),
}

_AWS_CANARIES: tuple[str, ...] = tuple(f"AKIAEXAMPLE{index:09d}" for index in range(1, 10)) + tuple(
    f"ASIAEXAMPLE{index:09d}" for index in range(10, 13)
)

_OPENAI_CANARIES: tuple[str, ...] = tuple(
    f"sk-EXAMPLEnotarealopenaikey{index:024d}" for index in range(1, 10)
) + tuple(f"sk-proj-EXAMPLEnotarealprojectkey{index:018d}" for index in range(10, 13))

_GOOGLE_CANARIES: tuple[str, ...] = tuple(
    f"AIzaSyEXAMPLEnotarealgooglekey{index:09d}" for index in range(1, 13)
)

_GITHUB_CANARIES: tuple[str, ...] = tuple(
    f"ghp_EXAMPLEnotarealgithubtoken{index:010d}" for index in range(1, 10)
) + tuple(f"github_pat_EXAMPLEnotarealpat{index:011d}" for index in range(10, 13))

_SLACK_CANARIES: tuple[str, ...] = tuple(
    f"xoxb-2222222222{index:02d}-3333333333{index:02d}-EXAMPLEnotarealslack{index:04d}"
    for index in range(1, 10)
) + tuple(
    f"xoxp-4444444444{index:02d}-5555555555{index:02d}-EXAMPLEnotarealslack{index:04d}"
    for index in range(10, 13)
)

_STRIPE_CANARIES: tuple[str, ...] = tuple(
    f"sk_live_EXAMPLEnotarealstripekey{index:08d}" for index in range(1, 10)
) + tuple(f"rk_live_EXAMPLEnotarealstriperk{index:08d}" for index in range(10, 13))

_JWT_HEADER = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
_JWT_PAYLOAD = "eyJzdWIiOiJFWEFNUExFIiwibmFtZSI6IkV4YW1wbGUiLCJpYXQiOjB9"
_JWT_CANARIES: tuple[str, ...] = tuple(
    f"{_JWT_HEADER}.{_JWT_PAYLOAD}.EXAMPLEnotarealsignature{index:04d}" for index in range(1, 13)
)

_PEM_BODY = "RVhBTVBMRSBGQUtFIEtFWSBOT1QgUkVBTCAtIHN5bnRoZXRpYyBNRU0wMSBmaXh0dXJl"
_PEM_CANARIES: tuple[str, ...] = tuple(
    f"-----BEGIN RSA PRIVATE KEY-----\n{_PEM_BODY}{index:04d}\n-----END RSA PRIVATE KEY-----"
    for index in range(1, 13)
)

_KEYED_VALUE_CANARIES: tuple[str, ...] = (
    tuple(f'api_key = "EXAMPLEnotarealsecretvalue{index:010d}"' for index in range(1, 6))
    + tuple(f"password: EXAMPLEnotarealpassphrase{index:010d}" for index in range(6, 10))
    + tuple(f"CLIENT_SECRET=EXAMPLEnotarealclientsecret{index:010d}" for index in range(10, 13))
)

_CONNSTRING_CANARIES: tuple[str, ...] = (
    tuple(
        f"postgresql://svc_reports:EXAMPLEnotarealpw{index:06d}@db.acme.test:5432/onedb"
        for index in range(1, 6)
    )
    + tuple(
        f"mongodb://svc_sync:EXAMPLEnotarealpw{index:06d}@mongo.partner.test:27017/ops"
        for index in range(6, 10)
    )
    + tuple(
        f"amqp://svc_queue:EXAMPLEnotarealpw{index:06d}@mq.example.test:5672/prod"
        for index in range(10, 13)
    )
)

_CANARY_CLASSES: tuple[tuple[SecretClass, str, tuple[str, ...]], ...] = (
    ("aws", "AWS access key id (AKIA/ASIA prefix, 20 scalars)", _AWS_CANARIES),
    ("openai", "OpenAI API key (sk- / sk-proj- prefix, 51 scalars)", _OPENAI_CANARIES),
    ("google", "Google API key (AIzaSy prefix, 39 scalars)", _GOOGLE_CANARIES),
    ("github", "GitHub token (ghp_ / github_pat_ prefix)", _GITHUB_CANARIES),
    ("slack", "Slack bot/user token (xoxb- / xoxp- with dashed segments)", _SLACK_CANARIES),
    ("stripe", "Stripe live secret / restricted key (sk_live_ / rk_live_)", _STRIPE_CANARIES),
    ("jwt", "JWT: three dot-separated base64url segments", _JWT_CANARIES),
    ("pem", "PEM private-key block with BEGIN/END armour", _PEM_CANARIES),
    ("keyed_value", "keyed assignment of a high-entropy value", _KEYED_VALUE_CANARIES),
    ("connstring", "connection string carrying a password in the authority", _CONNSTRING_CANARIES),
)


def _build_filler(scalar_count: int) -> str:
    """Materialize exactly `scalar_count` scalars of secret-free filler prose."""
    if scalar_count <= 0:
        return ""
    repeats = scalar_count // len(_FILLER_UNIT) + 1
    return (_FILLER_UNIT * repeats)[:scalar_count]


def _make_text_builder(lead: str, filler_scalars: int, canary: str, tail: str) -> Callable[[], str]:
    """Return a zero-argument builder that assembles the case text, filler included, on call."""

    def build_case_text() -> str:
        return f"{lead}{_build_filler(filler_scalars)}{canary}{tail}"

    return build_case_text


def _canary_start_for(placement: Placement, lead_len: int, canary_len: int) -> int:
    """Scalar offset at which the canary begins, per the placement's declared meaning."""
    if placement == "beyond_cap":
        return SCAN_CAP_SCALARS + BEYOND_CAP_MARGIN_SCALARS
    if placement == "straddling_cap":
        return SCAN_CAP_SCALARS - canary_len // 2
    return lead_len


def _build_positive_case(
    case_number: int,
    secret_class: SecretClass,
    class_shape: str,
    canary: str,
    placement: Placement,
    context_index: int,
) -> RedPositive:
    """Assemble one positive canary case with an independently computed span and origin."""
    lead, tail = _POSITIVE_CONTEXTS[context_index % len(_POSITIVE_CONTEXTS)]
    canary_start = _canary_start_for(placement, len(lead), len(canary))
    return RedPositive(
        case_id=f"red-{case_number:03d}",
        criterion_id=CRITERION_UNDER_REDACTION,
        origin=(
            f"criterion 14 (zero unredacted secret spans): {class_shape}; "
            f"{_PLACEMENT_ORIGIN[placement]}."
        ),
        secret_class=secret_class,
        canary_text=canary,
        text_builder=_make_text_builder(lead, canary_start - len(lead), canary, tail),
        canary_span=(canary_start, canary_start + len(canary)),
        placement=placement,
        surfaces=STAGE_A_SURFACE_NAMES,
        expected="fully_redacted",
    )


def _build_positive_battery() -> tuple[RedPositive, ...]:
    """Assemble twelve canaries per class with contiguous case ids starting at `red-101`."""
    records: list[RedPositive] = []
    case_number = 101
    for secret_class, class_shape, canaries in _CANARY_CLASSES:
        for index, canary in enumerate(canaries):
            placement = _PLACEMENT_PLAN[index % len(_PLACEMENT_PLAN)]
            records.append(
                _build_positive_case(
                    case_number, secret_class, class_shape, canary, placement, index
                )
            )
            case_number += 1
    return tuple(records)


RED_POSITIVES: tuple[RedPositive, ...] = _build_positive_battery()

__all__ = [
    "BEYOND_CAP_MARGIN_SCALARS",
    "CRITERION_OVER_REDACTION",
    "CRITERION_UNDER_REDACTION",
    "RED_NEGATIVES",
    "RED_POSITIVES",
    "RED_SURFACES",
    "SCAN_CAP_SCALARS",
    "STAGE_A_SURFACE_NAMES",
    "Placement",
    "RedNegative",
    "RedPositive",
    "RedSurface",
    "SecretClass",
]
