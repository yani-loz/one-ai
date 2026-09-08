"""COV battery, part A — record shapes, factories, and the excluded-by-property scenarios.

Role:
    Defines the COV fixture record types (`CovScenario`, `CovExpectation`), the factories both
    halves of the battery build with, and every scenario the frozen scope policy disposes as
    `explicitly_excluded` — by declared MIME class or by the inline-image property.

Used by:
    `tools.mem01_verify.fixtures.cov_scenarios_b` (imports the record types and factories) and
    `tools.mem01_verify.fixtures.cov_scenarios` (assembles `COV_SCENARIOS`). The public entry
    point for evaluators is `cov_scenarios.COV_SCENARIOS`, never this module directly.

Depends on:
    Nothing at runtime — data only (stdlib `dataclasses`/`typing`). Its EXPECTATIONS derive from
    `release/criteria.step1.v1.yaml` -> `scope_policy` (v0) and Stage-A contract 4.6 / 10.8,
    never from running an extractor or a parser (contract R12).

Key invariants:
    - Every `expected` value is read off the frozen policy, NOT off product behaviour. Where the
      shipped extractor disagrees (e.g. it marks archives `skipped_nondocument`), the expectation
      is the policy's answer and the fixture is meant to FAIL today.
    - Exclusion comes ONLY from an independently established property: the declared
      `content_type` matching `excluded_by_property.content_type_prefixes` or
      `.content_type_exact`, or `is_inline` on an image MIME. A processing status can neither
      create an exclusion nor establish delivery.
    - Delivery comes ONLY from `delivered_requires`, keyed by `kind`:
      email -> `parse_status == "parsed"` AND a present body; attachment -> `extraction_status ==
      "extracted"` AND stored text AND `extractor_name` AND `extractor_version`.
    - `extraction_status` carries the input's own declared processing status: the closed
      attachment vocabulary for `kind == "attachment"`, and the `parse_status` value
      (`parsed` | `failed`) for `kind == "email"`. For emails the `extractor_*` flags are always
      False and MUST NOT be consulted — `delivered_requires.email` does not name them; and
      `text_present` means `body_present`.
    - `folder_policy` records the frozen roster's folder provenance (`included:<folder>`).
      `scope_policy` v0 declares NO folder-based exclusion, so this field never changes a
      disposition; several cases exist purely to pin that.
    - `structured_truncated` is the stored partial marker of a structured extraction
      (`extracted_data.truncated` on a corpus row). `delivered_requires.attachment` carries
      `partial_marker_absent`, so a record that sets it is NOT delivered however complete the
      rest of its handoff is (contract 16.17(c)); every other record leaves it False.
    - `duplicate_of` is content identity, not an exclusion: a duplicate carries exactly the
      disposition its own properties give it (dedup is an accounted outcome, never a vanishing).
    - `expected.reason` is non-None if and only if the disposition is `explicitly_excluded`, and
      is a single canonical code. Where two exclusion clauses both match (an inline image), the
      MIME-class clause is named first, per the clause order of contract 4.6.
    - `expected.detail` is diagnostic prose for humans; it is never scored.
    - Synthetic only: addresses live under `example.test` / `acme.test` / `partner.test`, no real
      corpus text, no real names. Bulgarian and English contexts are both covered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

COV_FIXTURE_CRITERION = "cov.fixtures"

Disposition = Literal["delivered", "explicitly_excluded", "not_ready"]
InputKind = Literal["email", "attachment"]


@dataclass(frozen=True, slots=True)
class CovExpectation:
    """The disposition the frozen scope policy requires for one coverage scenario.

    Contract:
        `disposition` is the scored value. `reason` is non-None exactly when the disposition is
        `explicitly_excluded` and names the policy clause that established it. `detail` is human
        prose and is never compared by an evaluator.
    """

    disposition: Disposition
    reason: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class CovScenario:
    """One physical input described by properties only, plus its independent expectation.

    Contract:
        Fields describe the input as the frozen roster would record it. No field is derived from
        running a measured component; `extraction_status` is the component's own claim and is
        evidence of nothing on its own (contract 4.6).
    """

    case_id: str
    criterion_id: str
    origin: str
    kind: InputKind
    content_type: str | None
    is_inline: bool
    extraction_status: str
    text_present: bool
    extractor_name_present: bool
    extractor_version_present: bool
    duplicate_of: str | None
    folder_policy: str
    expected: CovExpectation
    structured_truncated: bool = False


COMPLETE_HANDOFF: dict[str, bool] = {
    "text_present": True,
    "extractor_name_present": True,
    "extractor_version_present": True,
}


def excluded_expectation(reason: str, detail: str) -> CovExpectation:
    """Expectation: excluded by the frozen policy clause named in `reason`."""
    return CovExpectation(disposition="explicitly_excluded", reason=reason, detail=detail)


def delivered_expectation(detail: str) -> CovExpectation:
    """Expectation: the declared handoff for this input kind is complete."""
    return CovExpectation(disposition="delivered", reason=None, detail=detail)


def not_ready_expectation(detail: str) -> CovExpectation:
    """Expectation: a required input that is neither excluded by property nor delivered."""
    return CovExpectation(disposition="not_ready", reason=None, detail=detail)


def attachment_scenario(
    case_id: str,
    content_type: str | None,
    extraction_status: str,
    *,
    origin: str,
    expected: CovExpectation,
    is_inline: bool = False,
    text_present: bool = False,
    extractor_name_present: bool = False,
    extractor_version_present: bool = False,
    duplicate_of: str | None = None,
    folder_policy: str = "included:INBOX",
    structured_truncated: bool = False,
) -> CovScenario:
    """Build an attachment scenario; unset handoff flags default to absent (honest NULL)."""
    return CovScenario(
        case_id=case_id,
        criterion_id=COV_FIXTURE_CRITERION,
        origin=origin,
        kind="attachment",
        content_type=content_type,
        is_inline=is_inline,
        extraction_status=extraction_status,
        text_present=text_present,
        extractor_name_present=extractor_name_present,
        extractor_version_present=extractor_version_present,
        duplicate_of=duplicate_of,
        folder_policy=folder_policy,
        expected=expected,
        structured_truncated=structured_truncated,
    )


def email_scenario(
    case_id: str,
    parse_status: str,
    *,
    origin: str,
    expected: CovExpectation,
    body_present: bool,
    folder_policy: str = "included:INBOX",
    structured_truncated: bool = False,
) -> CovScenario:
    """Build an email-body scenario; `extraction_status` carries `parse_status` (see invariants)."""
    return CovScenario(
        case_id=case_id,
        criterion_id=COV_FIXTURE_CRITERION,
        origin=origin,
        kind="email",
        content_type="message/rfc822",
        is_inline=False,
        extraction_status=parse_status,
        text_present=body_present,
        extractor_name_present=False,
        extractor_version_present=False,
        duplicate_of=None,
        folder_policy=folder_policy,
        expected=expected,
        structured_truncated=structured_truncated,
    )


EXCLUDED_BY_PROPERTY: tuple[CovScenario, ...] = (
    attachment_scenario(
        "cov-001",
        "image/png",
        "skipped_nondocument",
        origin="content_type_prefixes contains 'image/': the declared MIME class alone excludes.",
        expected=excluded_expectation(
            "excluded_content_type_prefix:image/",
            "Screenshot part on a synthetic acme.test thread; excluded by property, not status.",
        ),
    ),
    attachment_scenario(
        "cov-002",
        "image/jpeg",
        "skipped_nondocument",
        origin="Inline image: image/ prefix and inline_image both match; MIME clause is named.",
        expected=excluded_expectation(
            "excluded_content_type_prefix:image/",
            "Signature logo referenced by cid; 4.6 lists the MIME-class clause first.",
        ),
        is_inline=True,
    ),
    attachment_scenario(
        "cov-003",
        "audio/mpeg",
        "skipped_nondocument",
        origin="content_type_prefixes contains 'audio/': an audio part is a frozen exclusion.",
        expected=excluded_expectation(
            "excluded_content_type_prefix:audio/",
            "Synthetic voice memo attached to a partner.test message.",
        ),
    ),
    attachment_scenario(
        "cov-004",
        "video/mp4",
        "pending",
        origin="Property decides even when the component has not looked yet (status 'pending').",
        expected=excluded_expectation(
            "excluded_content_type_prefix:video/",
            "Never-processed video part; exclusion does not wait on extraction.",
        ),
    ),
    attachment_scenario(
        "cov-005",
        "application/pkcs7-signature",
        "extracted",
        origin="Exclusion by property outranks a complete handoff: a signature is never delivered.",
        expected=excluded_expectation(
            "excluded_content_type_exact:application/pkcs7-signature",
            "S/MIME signature with a full text handoff; excluded, never counted delivered.",
        ),
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-006",
        "application/pgp-signature",
        "corrupt",
        origin="An excluded MIME class stays excluded even when the extractor failed on it.",
        expected=excluded_expectation(
            "excluded_content_type_exact:application/pgp-signature",
            "Detached PGP signature that failed to parse; the failure is irrelevant here.",
        ),
    ),
    attachment_scenario(
        "cov-007",
        "application/x-pkcs7-signature",
        "skipped_nondocument",
        origin="Third signature type in content_type_exact — the whole list must be honoured.",
        expected=excluded_expectation(
            "excluded_content_type_exact:application/x-pkcs7-signature",
            "Legacy signature MIME spelling from an older synthetic client.",
        ),
    ),
    attachment_scenario(
        "cov-008",
        "text/calendar",
        "extracted",
        origin="Calendar parts are excluded by exact type despite carrying extractable text.",
        expected=excluded_expectation(
            "excluded_content_type_exact:text/calendar",
            "Meeting invite (BG subject) with a full handoff; excluded, not delivered.",
        ),
        folder_policy="included:Календар",
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-009",
        "application/ics",
        "empty",
        origin="Second calendar spelling in content_type_exact; status 'empty' does not decide.",
        expected=excluded_expectation(
            "excluded_content_type_exact:application/ics",
            "Calendar part sent with the alternate MIME type.",
        ),
    ),
    attachment_scenario(
        "cov-010",
        "font/ttf",
        "unsupported_format",
        origin="Fonts are a frozen exclusion; 'unsupported_format' would otherwise be not-ready.",
        expected=excluded_expectation(
            "excluded_content_type_exact:font/ttf",
            "Embedded webfont from a synthetic HTML newsletter.",
        ),
    ),
    attachment_scenario(
        "cov-011",
        "application/vnd.ms-fontobject",
        "skipped_nondocument",
        origin="Last exact entry of the frozen non-document list (EOT font).",
        expected=excluded_expectation(
            "excluded_content_type_exact:application/vnd.ms-fontobject",
            "EOT font part; excluded by declared type.",
        ),
    ),
    attachment_scenario(
        "cov-012",
        "image/svg+xml",
        "unsupported_format",
        origin="Prefix matching is on the DECLARED type: a textual SVG is still image/*.",
        expected=excluded_expectation(
            "excluded_content_type_prefix:image/",
            "Vector chart a text extractor could read; policy v0 excludes it anyway.",
        ),
    ),
    attachment_scenario(
        "cov-013",
        "image/tiff",
        "scanned_pending_ocr",
        origin="A scanned page image is excluded by class in v0 (OCR is explicitly out of scope).",
        expected=excluded_expectation(
            "excluded_content_type_prefix:image/",
            "Синтетичен сканиран документ като TIFF: изключен по MIME клас, не по статус.",
        ),
    ),
    attachment_scenario(
        "cov-014",
        "image/png",
        "skipped_nondocument",
        origin="A duplicate of an excluded input is still ACCOUNTED with the same exclusion.",
        expected=excluded_expectation(
            "excluded_content_type_prefix:image/",
            "Byte-identical resend of cov-001 in another folder; dedup is not a disposition.",
        ),
        duplicate_of="cov-001",
        folder_policy="included:Archive/2026",
    ),
)
